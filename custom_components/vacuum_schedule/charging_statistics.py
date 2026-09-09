"""Continuous battery charging observations for Vacuum Schedule statistics.

Charging speed is a property of the robot/dock, not of a particular cleaning
Job.  This collector therefore observes the live battery while the robot is on
the dock even when no Job exists.  Completed charge sessions are persisted in
an integration-owned ledger and are presentation/statistics data only: they do
not influence scheduling decisions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .statistics_models import distribution

_STORE_VERSION = 1
_SAMPLE_INTERVAL = timedelta(minutes=2)
_MIN_SESSION_GAIN_PERCENT = 1.0
_MIN_SESSION_SECONDS = 60.0
_MAX_SESSIONS = 5000


def _number(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0 or result > 100:
        return None
    return result


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class ChargingStatistics:
    """Observe and persist dock charging sessions independently of Jobs."""

    def __init__(self, hass: HomeAssistant, entry_id: str, input_provider: Any) -> None:
        self.hass = hass
        self.entry_id = str(entry_id)
        self.input_provider = input_provider
        self._store = Store(
            hass,
            _STORE_VERSION,
            f"vacuum_schedule.{self.entry_id}.statistics.charging",
        )
        self._sessions: list[dict[str, Any]] = []
        self._active: dict[str, Any] | None = None
        self._unsub: Callable[[], None] | None = None
        self._interval_unsub: Callable[[], None] | None = None
        self._started = False

    async def async_start(self) -> None:
        if self._started:
            return
        self._started = True
        raw = await self._store.async_load() or {}
        self._sessions = [
            dict(item)
            for item in raw.get("sessions", [])
            if isinstance(item, Mapping)
        ][-_MAX_SESSIONS:]
        stale = raw.get("active")
        if isinstance(stale, Mapping):
            # A crash/restart may leave an active session behind.  Never bridge
            # an offline interval because the robot may have reached 100% and
            # then sat idle for hours.  Preserve only the part actually observed
            # before shutdown, if it already contains a usable measurement.
            self._active = dict(stale)
            await self._async_finalize_active(reason="restart_boundary", persist=False)

        await self._async_observe()
        self._subscribe()
        self._interval_unsub = async_track_time_interval(
            self.hass,
            self._interval_callback,
            _SAMPLE_INTERVAL,
        )
        await self._async_save()

    async def async_stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._interval_unsub is not None:
            self._interval_unsub()
            self._interval_unsub = None
        # Take one final live sample and close the currently observed segment.
        # A later startup starts a fresh segment rather than pretending that the
        # Home Assistant downtime was continuously observed charging time.
        await self._async_observe()
        await self._async_finalize_active(reason="integration_stop", persist=False)
        await self._async_save()

    def _source_entities(self) -> set[str]:
        entities: set[str] = set()
        try:
            snapshot = self.input_provider.snapshot(allow_test_overrides=False)
        except Exception:
            return entities
        for key in (
            "vacuum.battery_percent",
            "vacuum.charging",
            "vacuum.activity",
            "dock.robot_docked",
        ):
            item = snapshot.values.get(key)
            entity_id = getattr(item, "source_entity_id", None) if item else None
            if entity_id:
                entities.add(str(entity_id))
        return entities

    def _subscribe(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        entity_ids = self._source_entities()
        if not entity_ids:
            return

        @callback
        def _state_changed(_event: Event[EventStateChangedData]) -> None:
            self.hass.async_create_task(self._async_observe())

        self._unsub = async_track_state_change_event(
            self.hass,
            sorted(entity_ids),
            _state_changed,
        )

    @callback
    def _interval_callback(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_observe())

    def _live_sample(self) -> dict[str, Any] | None:
        try:
            snapshot = self.input_provider.snapshot(allow_test_overrides=False)
        except Exception:
            return None
        battery_item = snapshot.values.get("vacuum.battery_percent")
        activity_item = snapshot.values.get("vacuum.activity")
        charging_item = snapshot.values.get("vacuum.charging")
        docked_item = snapshot.values.get("dock.robot_docked")
        battery = _number(getattr(battery_item, "live_value", None))
        if battery is None:
            return None
        activity = str(getattr(activity_item, "live_value", "") or "").lower()
        charging = getattr(charging_item, "live_value", None) is True
        docked = getattr(docked_item, "live_value", None) is True or activity == "docked"
        source_entities = sorted(
            {
                str(getattr(item, "source_entity_id", "") or "")
                for item in (battery_item, charging_item, activity_item, docked_item)
                if item is not None and getattr(item, "source_entity_id", None)
            }
        )
        return {
            "at": snapshot.evaluated_at.isoformat(),
            "battery_percent": battery,
            "charging": charging,
            "docked": docked,
            "activity": activity,
            "source_entities": source_entities,
        }

    async def _async_observe(self) -> None:
        sample = self._live_sample()
        if sample is None:
            return
        charging_now = bool(sample["charging"] or sample["docked"])
        percent = float(sample["battery_percent"])

        if not charging_now:
            if self._active is not None:
                self._append_sample(sample)
                await self._async_finalize_active(reason="left_dock", persist=False)
                await self._async_save()
            return

        if self._active is None:
            # At 100% there is no remaining charging interval to observe.
            if percent >= 100.0:
                return
            self._active = {
                "session_id": uuid4().hex,
                "started_at": sample["at"],
                "start_percent": percent,
                "last_at": sample["at"],
                "last_percent": percent,
                "sample_count": 1,
                "source_entities": list(sample["source_entities"]),
            }
            await self._async_save()
            return

        last_percent = _number(self._active.get("last_percent"))
        if last_percent is not None and percent < last_percent - 0.5:
            # A meaningful drop while nominally docked indicates that this is no
            # longer the same charging segment (or a sensor correction).  Do not
            # blend the two slopes.
            await self._async_finalize_active(reason="battery_drop", persist=False)
            self._active = None
            if percent < 100.0:
                self._active = {
                    "session_id": uuid4().hex,
                    "started_at": sample["at"],
                    "start_percent": percent,
                    "last_at": sample["at"],
                    "last_percent": percent,
                    "sample_count": 1,
                    "source_entities": list(sample["source_entities"]),
                }
            await self._async_save()
            return

        changed = self._append_sample(sample)
        if percent >= 100.0:
            await self._async_finalize_active(reason="battery_full", persist=False)
            changed = True
        if changed:
            await self._async_save()

    def _append_sample(self, sample: Mapping[str, Any]) -> bool:
        if self._active is None:
            return False
        at = str(sample.get("at") or "")
        percent = _number(sample.get("battery_percent"))
        if percent is None or not at:
            return False
        previous_at = str(self._active.get("last_at") or "")
        previous_percent = _number(self._active.get("last_percent"))
        if at == previous_at and percent == previous_percent:
            return False
        self._active["last_at"] = at
        self._active["last_percent"] = percent
        self._active["sample_count"] = int(self._active.get("sample_count") or 0) + 1
        sources = set(str(item) for item in self._active.get("source_entities", []) if item)
        sources.update(str(item) for item in sample.get("source_entities", []) if item)
        self._active["source_entities"] = sorted(sources)
        return True

    async def _async_finalize_active(self, *, reason: str, persist: bool = True) -> None:
        active = self._active
        self._active = None
        if not active:
            if persist:
                await self._async_save()
            return
        start_at = _dt(active.get("started_at"))
        end_at = _dt(active.get("last_at"))
        start_percent = _number(active.get("start_percent"))
        end_percent = _number(active.get("last_percent"))
        if start_at is None or end_at is None or start_percent is None or end_percent is None:
            if persist:
                await self._async_save()
            return
        try:
            seconds = max(0.0, (end_at - start_at).total_seconds())
        except TypeError:
            seconds = 0.0
        gain = max(0.0, end_percent - start_percent)
        if gain >= _MIN_SESSION_GAIN_PERCENT and seconds >= _MIN_SESSION_SECONDS:
            self._sessions.append(
                {
                    "session_id": str(active.get("session_id") or uuid4().hex),
                    "started_at": start_at.isoformat(),
                    "ended_at": end_at.isoformat(),
                    "start_percent": start_percent,
                    "end_percent": end_percent,
                    "gain_percent": gain,
                    "duration_seconds": seconds,
                    "rate_percent_per_minute": gain / (seconds / 60.0),
                    "sample_count": int(active.get("sample_count") or 0),
                    "source_entities": list(active.get("source_entities") or []),
                    "observation": "continuous_dock",
                    "closed_reason": reason,
                }
            )
            self._sessions = self._sessions[-_MAX_SESSIONS:]
        if persist:
            await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "schema_version": _STORE_VERSION,
                "sessions": self._sessions,
                "active": dict(self._active) if self._active else None,
            }
        )


    def export_layer(self) -> dict[str, Any]:
        """Export completed charging observations; the active live segment is operational state."""
        return {"sessions": [dict(item) for item in self._sessions]}

    async def async_clear_history(self) -> int:
        """Clear completed charging observations without interrupting the active observer."""
        count = len(self._sessions)
        self._sessions = []
        await self._async_save()
        return count

    async def async_restore_layer(self, payload: Mapping[str, Any]) -> int:
        """Replace completed charging observations while preserving the active live segment."""
        self._sessions = [
            dict(item) for item in payload.get("sessions", [])
            if isinstance(item, Mapping)
        ][-_MAX_SESSIONS:]
        await self._async_save()
        return len(self._sessions)

    def payload(
        self, filters: Mapping[str, Any] | None = None, *, percentile: int = 90
    ) -> dict[str, Any]:
        filters = dict(filters or {})
        start = _dt(filters.get("start"))
        end = _dt(filters.get("end"))
        rows: list[dict[str, Any]] = []
        for session in self._sessions:
            at = _dt(session.get("ended_at") or session.get("started_at"))
            if at is not None and start is not None:
                try:
                    if at < start:
                        continue
                except TypeError:
                    pass
            if at is not None and end is not None:
                try:
                    if at > end:
                        continue
                except TypeError:
                    pass
            rows.append(dict(session))
        rates = [
            float(value)
            for value in (session.get("rate_percent_per_minute") for session in rows)
            if _number(value) is not None
        ]
        gains = [
            float(value)
            for value in (session.get("gain_percent") for session in rows)
            if _number(value) is not None
        ]
        durations = [
            float(value)
            for value in (session.get("duration_seconds") for session in rows)
            if value is not None
        ]
        return {
            "source": "continuous_dock_observation",
            "session_count": len(rows),
            "rate_percent_per_minute": distribution(rates, percentile),
            "gain_percent": distribution(gains, percentile),
            "duration_seconds": distribution(durations, percentile),
            "active": dict(self._active) if self._active else None,
            "recent_sessions": list(reversed(rows[-20:])),
        }
