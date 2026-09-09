"""Synthetic dock-water meter and maintenance journal.

The water meter is deliberately synthetic: it learns from explicit service
anchors, physical robot observations and dock thresholds. Maintenance detection
facts are immutable; the user's interpretation is editable and the derived
meter/calibration is rebuilt from the ledger after every correction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .cleaning_scope import cleaning_scope
from .storage_migrations import (
    migrate_legacy_water_maintenance,
    migrate_water_cycle_threshold_events,
    recover_unconfirmed_water_service_after_upgrade,
    migrate_water_model_baseline,
    repair_water_calibration_v3,
    repair_water_calibration_v4,
)
from .const import SIGNAL_SCHEDULER_UPDATED


_WATER_STORE_VERSION = 1
_MAINTENANCE_MERGE_SECONDS = 5 * 60
_WATER_SCALE_MIN = 0.50
_WATER_SCALE_MAX = 2.00
_WATER_SCALE_MAX_STEP_FRACTION = 0.25
_WATER_SCALE_OBSERVATION_LIMIT = 30
_WATER_COMPONENT_MIN_CYCLES = 4
_WATER_COMPONENT_MIN_RATIO_SPREAD = 0.10
_VALID_ACTIONS = {
    "clean": {"full", "level", "add_ml", "noop", "unknown"},
    "dirty": {"empty", "level", "remove_ml", "noop", "unknown"},
}


@dataclass(slots=True, frozen=True)
class WaterModelProfile:
    profile_id: str
    label: str
    clean_capacity_ml: float
    dirty_capacity_ml: float
    internal_clean_ml: float | None
    floor_ml_per_effective_m2: float
    clean_wash_ml: float
    dirty_wash_ml: float
    bootstrap_quality: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "clean_capacity_ml": self.clean_capacity_ml,
            "dirty_capacity_ml": self.dirty_capacity_ml,
            "internal_clean_ml": self.internal_clean_ml,
            "floor_ml_per_effective_m2": self.floor_ml_per_effective_m2,
            "clean_wash_ml": self.clean_wash_ml,
            "dirty_wash_ml": self.dirty_wash_ml,
            "bootstrap_quality": self.bootstrap_quality,
        }


S8_PRO_ULTRA_PROFILE = WaterModelProfile(
    profile_id="roborock_s8_pro_ultra",
    label="Roborock S8 Pro Ultra",
    clean_capacity_ml=3500.0,
    dirty_capacity_ml=2900.0,
    internal_clean_ml=200.0,
    floor_ml_per_effective_m2=6.5,
    clean_wash_ml=124.0,
    dirty_wash_ml=124.0,
)


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


class WaterStatistics:
    """Persist physical facts, maintenance sessions and derived water state."""

    def __init__(self, hass: HomeAssistant, entry: Any, input_provider: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.input_provider = input_provider
        self._legacy_store = Store(hass, _WATER_STORE_VERSION, f"vacuum_schedule.{self.entry_id}.statistics.water")
        self._ledger_store = Store(hass, _WATER_STORE_VERSION, f"vacuum_schedule.{self.entry_id}.statistics.water.ledger")
        self._state_store = Store(hass, _WATER_STORE_VERSION, f"vacuum_schedule.{self.entry_id}.statistics.water.state")
        self.profile: WaterModelProfile | None = None
        self.events: list[dict[str, Any]] = []
        self.cycles: list[dict[str, Any]] = []
        self.maintenance_sessions: list[dict[str, Any]] = []
        # Kept as a compatibility projection for 0.9.0-0.9.5 callers/tests.
        self.pending_service: dict[str, dict[str, Any]] = {}
        self.processed_job_ids: set[str] = set()
        self.calibration: dict[str, Any] = {}
        self.model_baseline: dict[str, Any] = {}
        self.model_epoch: str | None = None
        self.model_repair_applied = False
        self.water_state_baseline: dict[str, Any] = {}
        self.clean: dict[str, Any] = {}
        self.dirty: dict[str, Any] = {}
        self._unsub = None
        self._last_resource_ok: dict[str, bool | None] = {"clean": None, "dirty": None}
        self._presence_entity_kind: dict[str, str] = {}
        self._presence_absent_since: dict[str, str] = {}
        self._operational_update_callback = None

    def _detect_profile(self) -> WaterModelProfile | None:
        executor = self.input_provider.executor
        entity_id = getattr(executor, "vacuum_entity_id", None)
        if not entity_id:
            return None
        try:
            registry = er.async_get(self.hass)
            entry = registry.async_get(entity_id)
            device = dr.async_get(self.hass).async_get(getattr(entry, "device_id", None)) if entry else None
        except Exception:
            device = None
        haystack = " ".join(
            str(value or "")
            for value in (
                getattr(device, "manufacturer", None),
                getattr(device, "model", None),
                getattr(device, "name", None),
                getattr(device, "name_by_user", None),
            )
        ).lower().replace("-", " ")
        if "roborock" in haystack and "s8" in haystack and "pro" in haystack and "ultra" in haystack:
            return S8_PRO_ULTRA_PROFILE
        return None

    def _new_balance(self, tank: str) -> dict[str, Any]:
        capacity = None
        if self.profile is not None:
            capacity = self.profile.clean_capacity_ml if tank == "clean" else self.profile.dirty_capacity_ml
        return {
            "tank": tank,
            "known": False,
            "estimate_ml_eq": None,
            "lower_ml_eq": None,
            "upper_ml_eq": None,
            "capacity_ml": capacity,
            "last_anchor": None,
            "anchor_at": None,
            "state_confidence": "unknown",
            "usage_since_anchor_ml_eq": 0.0,
            "base_usage_since_anchor_ml_eq": 0.0,
            "base_floor_usage_since_anchor_ml_eq": 0.0,
            "base_wash_usage_since_anchor_ml_eq": 0.0,
            "anchor_volume_ml_eq": None,
            "cycle_start_anchor": None,
        }

    def _fresh_calibration(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile.profile_id if self.profile else None,
            "clean_scale": 1.0,
            "dirty_scale": 1.0,
            "clean_floor_scale": 1.0,
            "clean_wash_scale": 1.0,
            "clean_component_model_trained": False,
            "clean_component_model_samples": 0,
            "clean_component_model_median_error_percent": None,
            "clean_component_cycles": [],
            "clean_full_cycles": 0,
            "dirty_full_cycles": 0,
            "clean_cycle_scale_samples": [],
            "dirty_cycle_scale_samples": [],
            "clean_manual_scale_samples": [],
            "dirty_manual_scale_samples": [],
            "clean_scale_observations": [],
            "dirty_scale_observations": [],
            "clean_rejected_calibration_samples": 0,
            "dirty_rejected_calibration_samples": 0,
            "algorithm_version": 4,
        }

    async def async_start(self) -> None:
        self.profile = self._detect_profile()
        ledger_raw = await self._ledger_store.async_load()
        state_raw = await self._state_store.async_load()
        legacy_raw: Mapping[str, Any] = {}
        if ledger_raw is None and state_raw is None:
            legacy_raw = await self._legacy_store.async_load() or {}
        ledger = dict(ledger_raw or legacy_raw or {})
        state = dict(state_raw or legacy_raw or {})
        self.events = [dict(item) for item in ledger.get("events", []) if isinstance(item, Mapping)]
        self.cycles = [dict(item) for item in ledger.get("cycles", []) if isinstance(item, Mapping)]
        self.maintenance_sessions = [dict(item) for item in ledger.get("maintenance_sessions", []) if isinstance(item, Mapping)]
        self.processed_job_ids = {str(value) for value in ledger.get("processed_job_ids", []) if value}
        self.pending_service = {str(key): dict(value) for key, value in dict(state.get("pending_service", {})).items() if isinstance(value, Mapping)}
        self.calibration = dict(state.get("calibration") or {})
        self.model_baseline = dict(state.get("model_baseline") or {})
        self.model_epoch = str(state.get("model_epoch") or "") or None
        self.water_state_baseline = dict(state.get("water_state_baseline") or {})
        self.clean = dict(state.get("clean") or self._new_balance("clean"))
        self.dirty = dict(state.get("dirty") or self._new_balance("dirty"))
        self._migrate_legacy_maintenance()
        self._remove_false_initial_confirmation_revisions()
        self._migrate_cycle_threshold_events()
        migrate_water_model_baseline(self)
        repaired_v3 = repair_water_calibration_v3(self)
        repaired_v4 = repair_water_calibration_v4(self)
        self.model_repair_applied = bool(repaired_v3 or repaired_v4)
        self._rebuild_from_ledger()
        self._recover_unconfirmed_service_after_upgrade()
        self._subscribe()
        self._update_resource_thresholds(datetime.now().astimezone(), persist=False)
        await self.async_save()

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await self.async_save()

    async def async_save(self) -> None:
        self._sync_pending_projection()
        await self._ledger_store.async_save(
            {
                "profile_id": self.profile.profile_id if self.profile else None,
                "events": list(self.events),
                "cycles": list(self.cycles),
                "maintenance_sessions": list(self.maintenance_sessions),
                "processed_job_ids": sorted(self.processed_job_ids),
            }
        )
        await self._state_store.async_save(
            {
                "profile": self.profile.to_dict() if self.profile else None,
                "pending_service": dict(self.pending_service),
                "calibration": dict(self.calibration),
                "model_baseline": dict(self.model_baseline),
                "model_epoch": self.model_epoch,
                "water_state_baseline": dict(self.water_state_baseline),
                "clean": dict(self.clean),
                "dirty": dict(self.dirty),
            }
        )

    # ---------------------------------------------------------------------
    # Maintenance journal
    # ---------------------------------------------------------------------

    def _migrate_legacy_maintenance(self) -> None:
        """Delegate legacy maintenance-session migration."""
        migrate_legacy_water_maintenance(self)

    def _recover_unconfirmed_service_after_upgrade(self) -> None:
        """Delegate one-time recovery of threshold-anchored service facts."""
        recover_unconfirmed_water_service_after_upgrade(self)

    def _migrate_cycle_threshold_events(self) -> None:
        """Delegate legacy cycle-threshold migration."""
        migrate_water_cycle_threshold_events(self)

    def _session_time(self, session: Mapping[str, Any]) -> str:
        return str(session.get("occurred_at") or session.get("detected_at") or session.get("created_at") or _iso_now())

    def _sync_pending_projection(self) -> None:
        projection: dict[str, dict[str, Any]] = {}
        for session in self.maintenance_sessions:
            if session.get("status") != "pending":
                continue
            for item in (session.get("detection") or {}).get("items", []):
                tank = str(item.get("tank") or "")
                if tank not in {"clean", "dirty"}:
                    continue
                projection[tank] = {
                    "pending_id": str(session.get("session_id") or ""),
                    "session_id": str(session.get("session_id") or ""),
                    "tank": tank,
                    "removed_at": item.get("removed_at"),
                    "returned_at": item.get("returned_at"),
                }
        self.pending_service = projection

    def _find_session(self, session_id: str) -> dict[str, Any] | None:
        sid = str(session_id or "")
        return next((s for s in self.maintenance_sessions if str(s.get("session_id") or "") == sid), None)

    @staticmethod
    def _maintenance_semantic_snapshot(
        interpretations: Mapping[str, Any],
        note: Any,
        occurred_at: Any,
    ) -> dict[str, Any]:
        """Return editable maintenance meaning without storage timestamps."""
        normalized: dict[str, dict[str, Any]] = {}
        for tank, raw in dict(interpretations or {}).items():
            if str(tank) not in {"clean", "dirty"} or not isinstance(raw, Mapping):
                continue
            action = str(raw.get("action") or "").lower()
            value = _number(raw.get("value"))
            if action not in {"level", "add_ml", "remove_ml"}:
                value = None
            normalized[str(tank)] = {"action": action, "value": value}
        return {
            "interpretations": normalized,
            "note": str(note).strip() if note else None,
            "occurred_at": str(occurred_at or "") or None,
        }

    def _remove_false_initial_confirmation_revisions(self) -> int:
        """Remove revisions created by old builds for pending -> confirmed.

        The first interpretation of an automatically detected service is its
        confirmation, not a correction. Versions through 0.12.31 archived the
        empty pending snapshot and rendered it as a blank correction row.
        """
        removed = 0
        for session in self.maintenance_sessions:
            revisions = list(session.get("revisions") or [])
            cleaned = []
            for revision in revisions:
                previous = dict(revision.get("previous") or {}) if isinstance(revision, Mapping) else {}
                if str(previous.get("status") or "").lower() == "pending":
                    removed += 1
                    continue
                cleaned.append(revision)
            if len(cleaned) != len(revisions):
                session["revisions"] = cleaned
        return removed

    def _detect_maintenance(self, tank: str, removed_at: str | None, returned_at: str, entity_id: str | None = None, *, signal: str = "tank_presence") -> dict[str, Any]:
        returned_dt = _parse_dt(returned_at)
        candidate = None
        for session in reversed(sorted(self.maintenance_sessions, key=self._session_time)):
            if session.get("source") != "detected" or session.get("status") != "pending":
                continue
            items = (session.get("detection") or {}).get("items", [])
            same = next((item for item in items if str(item.get("tank")) == tank), None)
            other_dt = _parse_dt(session.get("occurred_at"))
            if same is not None:
                # Presence return and resource recovery often arrive as two HA
                # state events for the same physical service. Keep one immutable
                # maintenance fact and only enrich its observed signals.
                if returned_dt and other_dt and abs((returned_dt - other_dt).total_seconds()) <= 120:
                    signals = same.setdefault("signals", [])
                    if signal not in signals:
                        signals.append(signal)
                    if entity_id and not same.get("entity_id"):
                        same["entity_id"] = entity_id
                    if removed_at and not same.get("removed_at"):
                        same["removed_at"] = removed_at
                    return session
                continue
            if returned_dt and other_dt and abs((returned_dt - other_dt).total_seconds()) <= _MAINTENANCE_MERGE_SECONDS:
                candidate = session
                break
        if candidate is None:
            candidate = {
                "session_id": uuid4().hex,
                "source": "detected",
                "detected_at": returned_at,
                "occurred_at": returned_at,
                "status": "pending",
                "detection": {"items": []},
                "interpretations": {},
                "note": None,
                "revisions": [],
                "created_at": returned_at,
                "updated_at": returned_at,
            }
            self.maintenance_sessions.append(candidate)
        candidate.setdefault("detection", {}).setdefault("items", []).append({
            "tank": tank,
            "removed_at": removed_at,
            "returned_at": returned_at,
            "entity_id": entity_id,
            "signals": [signal],
        })
        candidate["occurred_at"] = max(str(candidate.get("occurred_at") or returned_at), returned_at)
        candidate["updated_at"] = returned_at
        self._sync_pending_projection()
        return candidate

    def _validate_interpretations(self, interpretations: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for tank, raw in dict(interpretations or {}).items():
            tank = str(tank).lower()
            if tank not in {"clean", "dirty"} or not isinstance(raw, Mapping):
                continue
            action = str(raw.get("action") or "").lower()
            if action not in _VALID_ACTIONS[tank]:
                raise ValueError("invalid_service_action")
            value = _number(raw.get("value"))
            if action == "level":
                if value is None or value < 0 or value > 100:
                    raise ValueError("invalid_percent")
            elif action in {"add_ml", "remove_ml"}:
                if value is None or value <= 0:
                    raise ValueError("invalid_volume")
            else:
                value = None
            result[tank] = {"action": action, "value": value}
        if not result:
            raise ValueError("maintenance_interpretation_required")
        return result

    async def async_save_maintenance(
        self,
        interpretations: Mapping[str, Any],
        *,
        session_id: str | None = None,
        occurred_at: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Create or edit one maintenance session and rebuild all derived state."""
        clean_interpretations = self._validate_interpretations(interpretations)
        now = _iso_now()
        normalized_note = str(note).strip() if note else None
        session = self._find_session(session_id) if session_id else None
        if session_id and session is None:
            raise ValueError("maintenance_session_not_found")
        if session is None:
            session = {
                "session_id": uuid4().hex,
                "source": "manual",
                "detected_at": None,
                "occurred_at": occurred_at or now,
                "status": "confirmed",
                "detection": {"items": []},
                "interpretations": {},
                "note": None,
                "revisions": [],
                "created_at": now,
                "updated_at": now,
            }
            self.maintenance_sessions.append(session)
        else:
            # Detection facts, source and detected timestamps are immutable.
            previous_status = str(session.get("status") or "")
            next_occurred_at = session.get("occurred_at")
            if session.get("source") == "manual" and occurred_at:
                next_occurred_at = occurred_at
            previous_semantics = self._maintenance_semantic_snapshot(
                session.get("interpretations") or {},
                session.get("note"),
                session.get("occurred_at"),
            )
            next_semantics = self._maintenance_semantic_snapshot(
                clean_interpretations,
                normalized_note,
                next_occurred_at,
            )
            if previous_status == "confirmed" and previous_semantics == next_semantics:
                # Saving an unchanged editor is a no-op, not a correction.
                return self.maintenance_payload()
            if previous_status == "confirmed":
                session.setdefault("revisions", []).append({
                    "revision_id": uuid4().hex,
                    "changed_at": now,
                    "previous": {
                        "interpretations": dict(session.get("interpretations") or {}),
                        "note": session.get("note"),
                        "occurred_at": session.get("occurred_at"),
                        "status": session.get("status"),
                        "updated_at": session.get("updated_at"),
                    },
                })
            # A pending -> confirmed transition is the initial interpretation,
            # so it deliberately creates no revision.
            if session.get("source") == "manual" and occurred_at:
                session["occurred_at"] = occurred_at
        session["interpretations"] = {
            tank: {**data, "updated_at": now}
            for tank, data in clean_interpretations.items()
        }
        session["status"] = "confirmed"
        session["note"] = normalized_note
        session["updated_at"] = now
        self._rebuild_from_ledger()
        await self.async_save()
        self._fire_attention_updated("maintenance_saved", operational=True)
        return self.maintenance_payload()

    async def async_service(self, tank: str, action: str, *, value: float | None = None, pending_id: str | None = None) -> dict[str, Any]:
        """Backward-compatible one-tank API used by older frontends/services."""
        tank = str(tank).lower()
        pending_session = None
        if pending_id:
            pending_session = self._find_session(str(pending_id))
        if pending_session is None and tank in self.pending_service:
            pending_session = self._find_session(str(self.pending_service[tank].get("session_id") or ""))
        if pending_session is not None:
            interpretations = dict(pending_session.get("interpretations") or {})
            interpretations[tank] = {"action": action, "value": value}
            # If this is a multi-tank detected session, keep it pending until every
            # detected tank has an interpretation. Old one-tank callers therefore
            # remain safe instead of silently resolving the other tank.
            detected = {str(i.get("tank")) for i in (pending_session.get("detection") or {}).get("items", [])}
            normalized = self._validate_interpretations(interpretations)
            if detected and not detected.issubset(normalized):
                pending_session["interpretations"] = {k: {**v, "updated_at": _iso_now()} for k, v in normalized.items()}
                pending_session["updated_at"] = _iso_now()
                self._sync_pending_projection()
                await self.async_save()
                return self.payload()
            await self.async_save_maintenance(normalized, session_id=str(pending_session.get("session_id")))
            return self.payload()
        await self.async_save_maintenance({tank: {"action": action, "value": value}})
        return self.payload()

    # ---------------------------------------------------------------------
    # Physical observation ledger and deterministic replay
    # ---------------------------------------------------------------------

    def set_operational_update_callback(self, callback) -> None:
        """Register Scheduler wake-up hook for water state/model changes."""
        self._operational_update_callback = callback

    def _fire_attention_updated(self, reason: str, *, operational: bool = False) -> None:
        try:
            async_dispatcher_send(
                self.hass,
                SIGNAL_SCHEDULER_UPDATED,
                {"entry_id": self.entry_id, "reason": reason},
            )
        except Exception:
            pass
        if operational and self._operational_update_callback is not None:
            try:
                self._operational_update_callback()
            except Exception:
                pass

    def _resource_ok(self, key: str) -> bool | None:
        try:
            snapshot = self.input_provider.snapshot(datetime.now().astimezone(), allow_test_overrides=False)
            item = snapshot.values.get(key)
            if item is None or not item.effective_available or item.status != "ready":
                return None
            value = item.effective_value
            return bool(value) if value is not None else None
        except Exception:
            return None

    def _candidate_presence_entities(self) -> dict[str, str]:
        result: dict[str, str] = {}
        executor = self.input_provider.executor
        try:
            registry = er.async_get(self.hass)
        except Exception:
            return result
        ids: set[str] = set()
        try:
            ids.update(executor.snapshot().related_entities.water)
            ids.update(executor.snapshot().related_entities.dock)
        except Exception:
            pass
        for entity_id in ids:
            entry = registry.async_get(entity_id)
            state = self.hass.states.get(entity_id)
            tokens = " ".join(
                str(value or "")
                for value in (
                    entity_id,
                    getattr(entry, "translation_key", None),
                    getattr(entry, "original_name", None),
                    getattr(entry, "unique_id", None),
                    getattr(state, "name", None),
                )
            ).lower().replace("-", "_").replace(" ", "_")
            if not any(token in tokens for token in ("present", "installed", "attached", "in_place")):
                continue
            if "dirty" in tokens or "waste" in tokens:
                result[entity_id] = "dirty"
            elif "clean" in tokens or "fresh" in tokens:
                result[entity_id] = "clean"
        return result

    def _subscribe(self) -> None:
        entity_ids: set[str] = set()
        try:
            bindings = self.input_provider.bindings
            for key in ("dock.clean_water", "dock.dirty_water"):
                binding = bindings.get(key)
                entity_id = getattr(binding, "entity_id", None) if binding else None
                if entity_id:
                    entity_ids.add(str(entity_id))
        except Exception:
            pass
        self._presence_entity_kind = self._candidate_presence_entities()
        entity_ids.update(self._presence_entity_kind)
        if not entity_ids:
            return

        async def _async_handle(event) -> None:
            entity_id = str(event.data.get("entity_id") or "")
            now = datetime.now().astimezone()
            new_state = event.data.get("new_state")
            kind = self._presence_entity_kind.get(entity_id)
            if kind:
                state = str(getattr(new_state, "state", "unknown") or "unknown").lower()
                present = state in {"on", "true", "present", "installed", "yes", "1"}
                absent = state in {"off", "false", "not_present", "removed", "no", "0"}
                if absent:
                    self._presence_absent_since[kind] = now.isoformat()
                    # A resource sensor frequently flips to "not OK" merely
                    # because the tank was physically removed. That is not an
                    # EMPTY/FULL anchor. Remove a just-recorded threshold race
                    # and keep the previous synthetic balance until the user
                    # confirms what happened to the tank.
                    if self._discard_recent_presence_threshold(kind, now):
                        self._rebuild_from_ledger()
                    self._last_resource_ok[kind] = None
                elif present and kind in self._presence_absent_since:
                    removed_at = self._presence_absent_since.pop(kind)
                    self._detect_maintenance(kind, removed_at, now.isoformat(), entity_id, signal="tank_presence")
                    await self.async_save()
                    self._fire_attention_updated("maintenance_detected", operational=True)
            changed = self._update_resource_thresholds(now, persist=False)
            if changed:
                await self.async_save()

        @callback
        def _handle(event) -> None:
            self.hass.async_create_task(_async_handle(event))

        self._unsub = async_track_state_change_event(self.hass, sorted(entity_ids), _handle)

    def _discard_recent_presence_threshold(self, tank: str, now: datetime) -> bool:
        """Drop a resource-threshold edge caused by physical tank removal.

        Presence and resource entities are separate HA state events and may
        arrive in either order. A threshold within 15 seconds of a confirmed
        tank-removal event is treated as removal telemetry, not as evidence that
        clean water reached zero or the dirty tank reached full.
        """
        for idx in range(len(self.events) - 1, -1, -1):
            event = self.events[idx]
            if str(event.get("event_type") or "") != "SENSOR_THRESHOLD" or str(event.get("tank") or "") != tank:
                continue
            event_at = _parse_dt(event.get("at"))
            if event_at is None:
                return False
            try:
                age = abs((now - event_at).total_seconds())
            except TypeError:
                return False
            if age <= 15:
                self.events.pop(idx)
                return True
            return False
        return False

    def _update_resource_thresholds(self, now: datetime, *, persist: bool = False) -> bool:
        changed = False
        for tank, key in (("clean", "dock.clean_water"), ("dirty", "dock.dirty_water")):
            current = self._resource_ok(key)
            previous = self._last_resource_ok[tank]
            # While a presence sensor says the tank is removed, an unavailable
            # or false resource state describes absence, not the water level.
            # Keep the previous edge state so reinsertion cannot manufacture a
            # false "recovered from empty/full" maintenance anchor.
            if tank in self._presence_absent_since:
                continue
            self._last_resource_ok[tank] = current
            if previous is True and current is False:
                self.events.append({
                    "event_id": uuid4().hex,
                    "event_type": "SENSOR_THRESHOLD",
                    "tank": tank,
                    "at": now.isoformat(),
                    "observation_quality": "live_resource_threshold",
                    "details": {"source_key": key},
                })
                changed = True
            elif previous is False and current is True:
                # A serviceable water condition recovered. Even when the dock
                # exposes no explicit tank-present entity, this is a strong
                # operational signal that the user likely refilled/emptied a
                # tank. Keep it pending until the user tells us what happened.
                self._detect_maintenance(tank, None, now.isoformat(), signal="resource_recovered")
                changed = True
        if changed:
            self._rebuild_from_ledger()
            self._fire_attention_updated("water_state_changed", operational=True)
        return changed

    @staticmethod
    def _weighted_median_scale(observations: Sequence[Mapping[str, Any]]) -> float | None:
        weighted: list[float] = []
        for item in observations:
            value = _number(item.get("scale"))
            if value is None:
                continue
            weight = max(1, min(5, int(item.get("weight") or 1)))
            weighted.extend([float(value)] * weight)
        if not weighted:
            return None
        weighted.sort()
        return weighted[len(weighted) // 2]

    def _record_scale_observation(
        self, tank: str, *, scale: float, source: str, at: str,
        base_usage_ml_eq: float, observed_change_ml_eq: float, weight: int,
    ) -> bool:
        """Apply one bounded, robust calibration observation.

        Calibration observations are absolute bootstrap-relative scale estimates.
        Implausible observations are rejected rather than clipped, and accepted
        observations may move the active scale by at most 25% per anchor.
        """
        if tank not in {"clean", "dirty"}:
            return False
        scale = float(scale)
        if not (_WATER_SCALE_MIN <= scale <= _WATER_SCALE_MAX):
            key = f"{tank}_rejected_calibration_samples"
            self.calibration[key] = int(self.calibration.get(key, 0) or 0) + 1
            return False
        key = f"{tank}_scale_observations"
        observations = [dict(item) for item in self.calibration.get(key, []) if isinstance(item, Mapping)]
        observations.append({
            "scale": scale,
            "source": str(source),
            "at": at,
            "base_usage_ml_eq": float(base_usage_ml_eq),
            "observed_change_ml_eq": float(observed_change_ml_eq),
            "weight": max(1, min(5, int(weight))),
        })
        observations = observations[-_WATER_SCALE_OBSERVATION_LIMIT:]
        self.calibration[key] = observations
        center = self._weighted_median_scale(observations)
        if center is None:
            return False
        current = float(self.calibration.get(f"{tank}_scale", 1.0) or 1.0)
        lower_step = max(_WATER_SCALE_MIN, current * (1.0 - _WATER_SCALE_MAX_STEP_FRACTION))
        upper_step = min(_WATER_SCALE_MAX, current * (1.0 + _WATER_SCALE_MAX_STEP_FRACTION))
        self.calibration[f"{tank}_scale"] = max(lower_step, min(upper_step, center))
        return True

    def _learn_from_level_anchor(
        self, tank: str, balance: Mapping[str, Any], *, observed_ml_eq: float,
        at: str, source: str,
    ) -> bool:
        """Use explicit level corrections as supervised calibration anchors.

        Standalone manual corrections are strong observations. Levels entered for
        an automatically detected tank-service session are weak observations: the
        service itself may have changed the volume, so only naturally plausible
        deltas survive the same scale sanity checks.
        """
        capacity = _number(balance.get("capacity_ml"))
        anchor = _number(balance.get("anchor_volume_ml_eq"))
        base_usage = _number(balance.get("base_usage_since_anchor_ml_eq"))
        if capacity is None or anchor is None or base_usage is None:
            return False
        model_epoch = _parse_dt(self.model_epoch)
        anchor_at = _parse_dt(balance.get("anchor_at"))
        observed_at = _parse_dt(at)
        if model_epoch is not None and (anchor_at is None or anchor_at < model_epoch or observed_at is None or observed_at <= model_epoch):
            return False
        minimum_usage = max(100.0, capacity * 0.05)
        if base_usage < minimum_usage:
            return False
        observed_change = anchor - observed_ml_eq if tank == "clean" else observed_ml_eq - anchor
        if observed_change <= 0:
            return False
        scale = observed_change / base_usage
        source_key = "manual_level" if source == "manual" else "detected_level"
        accepted = self._record_scale_observation(
            tank, scale=scale, source=source_key, at=at,
            base_usage_ml_eq=base_usage, observed_change_ml_eq=observed_change,
            weight=3 if source == "manual" else 1,
        )
        if accepted:
            key = f"{tank}_manual_scale_samples"
            samples = [float(value) for value in self.calibration.get(key, []) if _number(value) is not None]
            samples.append(float(scale))
            self.calibration[key] = samples[-20:]
        return accepted

    def _close_cycle_replay(self, tank: str, at: str) -> None:
        balance = self.clean if tank == "clean" else self.dirty
        capacity = _number(balance.get("capacity_ml"))
        scaled_usage = _number(balance.get("usage_since_anchor_ml_eq"))
        base_usage = _number(balance.get("base_usage_since_anchor_ml_eq"))
        floor_base = _number(balance.get("base_floor_usage_since_anchor_ml_eq")) or 0.0
        wash_base = _number(balance.get("base_wash_usage_since_anchor_ml_eq")) or 0.0
        start_anchor = balance.get("cycle_start_anchor")

        def reset_cycle() -> None:
            balance["usage_since_anchor_ml_eq"] = 0.0
            balance["base_usage_since_anchor_ml_eq"] = 0.0
            balance["base_floor_usage_since_anchor_ml_eq"] = 0.0
            balance["base_wash_usage_since_anchor_ml_eq"] = 0.0
            balance["cycle_start_anchor"] = None

        if capacity is None or not start_anchor:
            reset_cycle()
            return
        if base_usage is None or base_usage <= 0:
            current_scale = float(self.calibration.get(f"{tank}_scale", 1.0) or 1.0)
            base_usage = (scaled_usage / current_scale) if scaled_usage and current_scale > 0 else None
        if base_usage is None or base_usage <= 0:
            reset_cycle()
            return
        model_epoch = _parse_dt(self.model_epoch)
        cycle_start = _parse_dt(start_anchor)
        cycle_end = _parse_dt(at)
        if model_epoch is not None and (cycle_end is None or cycle_end <= model_epoch or cycle_start is None or cycle_start < model_epoch):
            reset_cycle()
            return

        observed_scale = capacity / base_usage
        minimum_cycle_usage = max(250.0, capacity * 0.25)
        quality = "full_sensor_cycle_replayed"
        accepted = False
        if base_usage < minimum_cycle_usage:
            quality = "rejected_insufficient_usage"
            key = f"{tank}_rejected_calibration_samples"
            self.calibration[key] = int(self.calibration.get(key, 0) or 0) + 1
        elif not (_WATER_SCALE_MIN <= observed_scale <= _WATER_SCALE_MAX):
            quality = "rejected_implausible_scale"
            key = f"{tank}_rejected_calibration_samples"
            self.calibration[key] = int(self.calibration.get(key, 0) or 0) + 1
        else:
            accepted = self._record_scale_observation(
                tank, scale=observed_scale, source="full_sensor_cycle", at=at,
                base_usage_ml_eq=base_usage, observed_change_ml_eq=capacity, weight=2,
            )
            if accepted:
                key = f"{tank}_cycle_scale_samples"
                samples = [float(value) for value in self.calibration.get(key, []) if _number(value) is not None]
                samples.append(float(observed_scale))
                self.calibration[key] = samples[-20:]
                self.calibration[f"{tank}_full_cycles"] = int(self.calibration.get(f"{tank}_full_cycles", 0) or 0) + 1
                if tank == "clean":
                    component_rows = [
                        dict(item) for item in self.calibration.get("clean_component_cycles", [])
                        if isinstance(item, Mapping)
                    ]
                    component_rows.append({
                        "at": at,
                        "floor_base_ml_eq": float(floor_base),
                        "wash_base_ml_eq": float(wash_base),
                        "capacity_ml_eq": float(capacity),
                    })
                    self.calibration["clean_component_cycles"] = component_rows[-_WATER_SCALE_OBSERVATION_LIMIT:]
                    self._update_clean_component_model()

        component_predicted = None
        component_error = None
        if tank == "clean" and bool(self.calibration.get("clean_component_model_trained")):
            component_predicted = (
                floor_base * float(self.calibration.get("clean_floor_scale", 1.0) or 1.0)
                + wash_base * float(self.calibration.get("clean_wash_scale", 1.0) or 1.0)
            )
            if capacity > 0:
                component_error = abs(component_predicted - capacity) / capacity * 100.0

        self.cycles.append({
            "cycle_id": uuid4().hex,
            "tank": tank,
            "start_at": start_anchor,
            "end_at": at,
            "model_usage_ml_eq_before_scale": base_usage,
            "floor_base_ml_eq": floor_base if tank == "clean" else None,
            "wash_base_ml_eq": wash_base if tank == "clean" else base_usage,
            "scaled_usage_ml_eq": scaled_usage,
            "component_predicted_usage_ml_eq": component_predicted,
            "component_error_percent": component_error,
            "capacity_ml_eq": capacity,
            "cycle_correction_ratio": observed_scale,
            "observed_scale": observed_scale,
            "cycle_error_percent": abs(1.0 - observed_scale) * 100.0,
            "quality": quality,
            "accepted_for_calibration": accepted,
        })
        reset_cycle()

    def _apply_interpretation(
        self, tank: str, interpretation: Mapping[str, Any], at: str, *, source: str = "detected"
    ) -> None:
        balance = self.clean if tank == "clean" else self.dirty
        capacity = _number(balance.get("capacity_ml"))
        action = str(interpretation.get("action") or "")
        value = _number(interpretation.get("value"))
        if action in {"full", "empty"}:
            if capacity is None:
                return
            estimate = capacity if tank == "clean" else 0.0
            balance.update({
                "known": True,
                "estimate_ml_eq": estimate,
                "lower_ml_eq": estimate,
                "upper_ml_eq": estimate,
                "last_anchor": action,
                "anchor_at": at,
                "state_confidence": "high",
                "usage_since_anchor_ml_eq": 0.0,
                "base_usage_since_anchor_ml_eq": 0.0,
                "base_floor_usage_since_anchor_ml_eq": 0.0,
                "base_wash_usage_since_anchor_ml_eq": 0.0,
                "anchor_volume_ml_eq": estimate,
                "cycle_start_anchor": at,
            })
        elif action == "level":
            if capacity is None or value is None:
                return
            percent = max(0.0, min(100.0, value))
            estimate = capacity * percent / 100.0
            self._learn_from_level_anchor(tank, balance, observed_ml_eq=estimate, at=at, source=source)
            margin = capacity * 0.10
            balance.update({
                "known": True,
                "estimate_ml_eq": estimate,
                "lower_ml_eq": max(0.0, estimate - margin),
                "upper_ml_eq": min(capacity, estimate + margin),
                "last_anchor": "approximate_level",
                "anchor_at": at,
                "state_confidence": "medium",
                "usage_since_anchor_ml_eq": 0.0,
                "base_usage_since_anchor_ml_eq": 0.0,
                "base_floor_usage_since_anchor_ml_eq": 0.0,
                "base_wash_usage_since_anchor_ml_eq": 0.0,
                "anchor_volume_ml_eq": estimate,
                "cycle_start_anchor": None,
            })
        elif action in {"add_ml", "remove_ml"}:
            if capacity is None or value is None or not balance.get("known"):
                return
            current = _number(balance.get("estimate_ml_eq"))
            if current is None:
                return
            delta = abs(value) * (1.0 if action == "add_ml" else -1.0)
            estimate = max(0.0, min(capacity, current + delta))
            balance.update({
                "known": True,
                "estimate_ml_eq": estimate,
                "lower_ml_eq": estimate,
                "upper_ml_eq": estimate,
                "last_anchor": "exact_volume",
                "anchor_at": at,
                "state_confidence": "medium",
                "usage_since_anchor_ml_eq": 0.0,
                "base_usage_since_anchor_ml_eq": 0.0,
                "base_floor_usage_since_anchor_ml_eq": 0.0,
                "base_wash_usage_since_anchor_ml_eq": 0.0,
                "anchor_volume_ml_eq": estimate,
                "cycle_start_anchor": None,
            })
        elif action == "unknown":
            balance.update({
                "known": False,
                "estimate_ml_eq": None,
                "lower_ml_eq": None,
                "upper_ml_eq": None,
                "last_anchor": "unknown_service",
                "anchor_at": at,
                "state_confidence": "unknown",
                "usage_since_anchor_ml_eq": 0.0,
                "base_usage_since_anchor_ml_eq": 0.0,
                "base_floor_usage_since_anchor_ml_eq": 0.0,
                "base_wash_usage_since_anchor_ml_eq": 0.0,
                "anchor_volume_ml_eq": None,
                "cycle_start_anchor": None,
            })
        # noop intentionally leaves the derived state unchanged.

    def _usage_scale(self, tank: str, event_type: str) -> float:
        """Return the calibrated scale for one derived water component."""
        if tank == "clean" and bool(self.calibration.get("clean_component_model_trained")):
            if event_type == "FLOOR_MOP":
                return float(self.calibration.get("clean_floor_scale", self.calibration.get("clean_scale", 1.0)) or 1.0)
            if event_type == "MOP_WASH":
                return float(self.calibration.get("clean_wash_scale", self.calibration.get("clean_scale", 1.0)) or 1.0)
        return float(self.calibration.get(f"{tank}_scale", 1.0) or 1.0)

    def _update_clean_component_model(self) -> bool:
        """Fit separate floor and mop-wash scales from diverse full clean cycles.

        One FULL→LOW cycle cannot identify two components.  The split is enabled
        only after several accepted cycles with materially different floor/wash
        proportions; otherwise the robust common ``clean_scale`` remains in use.
        """
        rows = [
            dict(item) for item in self.calibration.get("clean_component_cycles", [])
            if isinstance(item, Mapping)
        ][-_WATER_SCALE_OBSERVATION_LIMIT:]
        usable: list[tuple[float, float, float]] = []
        ratios: list[float] = []
        for item in rows:
            floor = _number(item.get("floor_base_ml_eq")) or 0.0
            wash = _number(item.get("wash_base_ml_eq")) or 0.0
            capacity = _number(item.get("capacity_ml_eq"))
            if capacity is None or capacity <= 0 or floor + wash <= 0:
                continue
            usable.append((floor / capacity, wash / capacity, capacity))
            ratios.append(floor / (floor + wash))
        self.calibration["clean_component_model_samples"] = len(usable)
        if len(usable) < _WATER_COMPONENT_MIN_CYCLES:
            self.calibration["clean_component_model_trained"] = False
            return False
        if not ratios or max(ratios) - min(ratios) < _WATER_COMPONENT_MIN_RATIO_SPREAD:
            self.calibration["clean_component_model_trained"] = False
            return False

        a = sum(x * x for x, z, _ in usable)
        b = sum(x * z for x, z, _ in usable)
        c = sum(z * z for x, z, _ in usable)
        d = sum(x for x, z, _ in usable)
        e = sum(z for x, z, _ in usable)
        det = a * c - b * b
        if det <= 1e-6:
            self.calibration["clean_component_model_trained"] = False
            return False
        floor_scale = (d * c - b * e) / det
        wash_scale = (a * e - b * d) / det
        if not (_WATER_SCALE_MIN <= floor_scale <= _WATER_SCALE_MAX):
            self.calibration["clean_component_model_trained"] = False
            return False
        if not (_WATER_SCALE_MIN <= wash_scale <= _WATER_SCALE_MAX):
            self.calibration["clean_component_model_trained"] = False
            return False

        errors = sorted(abs((x * floor_scale + z * wash_scale) - 1.0) * 100.0 for x, z, _ in usable)
        median_error = errors[len(errors) // 2] if len(errors) % 2 else (errors[len(errors)//2 - 1] + errors[len(errors)//2]) / 2.0
        # A noisy split is worse than the robust common scale.
        if median_error > 20.0:
            self.calibration["clean_component_model_trained"] = False
            return False

        common = float(self.calibration.get("clean_scale", 1.0) or 1.0)
        old_floor = float(self.calibration.get("clean_floor_scale", common) or common)
        old_wash = float(self.calibration.get("clean_wash_scale", common) or common)
        def bounded_step(current: float, target: float) -> float:
            lower = max(_WATER_SCALE_MIN, current * (1.0 - _WATER_SCALE_MAX_STEP_FRACTION))
            upper = min(_WATER_SCALE_MAX, current * (1.0 + _WATER_SCALE_MAX_STEP_FRACTION))
            return max(lower, min(upper, target))
        self.calibration["clean_floor_scale"] = bounded_step(old_floor, floor_scale)
        self.calibration["clean_wash_scale"] = bounded_step(old_wash, wash_scale)
        self.calibration["clean_component_model_trained"] = True
        self.calibration["clean_component_model_median_error_percent"] = median_error
        return True

    def _apply_usage_replay(self, event: Mapping[str, Any]) -> None:
        tank = str(event.get("tank") or "")
        if tank not in {"clean", "dirty"}:
            return
        balance = self.clean if tank == "clean" else self.dirty
        details = dict(event.get("details") or {})
        base = _number(details.get("base_ml_eq"))
        if base is None:
            estimated = _number(event.get("estimated_ml_eq"))
            applied = _number(details.get("applied_scale")) or 1.0
            base = None if estimated is None else estimated / applied
        if base is None:
            return
        event_at = _parse_dt(event.get("at"))
        model_epoch = _parse_dt(self.model_epoch)
        event_type = str(event.get("event_type") or "")
        if model_epoch is not None and event_at is not None and event_at <= model_epoch:
            scale = _number(details.get("applied_scale")) or self._usage_scale(tank, event_type)
        else:
            scale = self._usage_scale(tank, event_type)
        ml_eq = max(0.0, base * scale)
        # Keep the persisted presentation value consistent with the exact scale
        # used by chronological replay.  Historical calibration can change when
        # repaired floor-area facts are replayed.
        if isinstance(event, dict):
            event["estimated_ml_eq"] = ml_eq
            event_details = dict(event.get("details") or {})
            event_details["base_ml_eq"] = base
            event_details["applied_scale"] = scale
            event["details"] = event_details
        balance["usage_since_anchor_ml_eq"] = float(balance.get("usage_since_anchor_ml_eq") or 0.0) + ml_eq
        balance["base_usage_since_anchor_ml_eq"] = float(balance.get("base_usage_since_anchor_ml_eq") or 0.0) + max(0.0, base)
        if tank == "clean" and event_type == "FLOOR_MOP":
            balance["base_floor_usage_since_anchor_ml_eq"] = float(balance.get("base_floor_usage_since_anchor_ml_eq") or 0.0) + max(0.0, base)
        elif tank == "clean" and event_type == "MOP_WASH":
            balance["base_wash_usage_since_anchor_ml_eq"] = float(balance.get("base_wash_usage_since_anchor_ml_eq") or 0.0) + max(0.0, base)
        if not balance.get("known"):
            return
        capacity = _number(balance.get("capacity_ml"))
        estimate = _number(balance.get("estimate_ml_eq"))
        lower = _number(balance.get("lower_ml_eq"))
        upper = _number(balance.get("upper_ml_eq"))
        if capacity is None or estimate is None:
            return
        if tank == "clean":
            balance["estimate_ml_eq"] = max(0.0, estimate - ml_eq)
            if lower is not None:
                balance["lower_ml_eq"] = max(0.0, lower - ml_eq)
            if upper is not None:
                balance["upper_ml_eq"] = max(0.0, upper - ml_eq)
        else:
            balance["estimate_ml_eq"] = min(capacity, estimate + ml_eq)
            if lower is not None:
                balance["lower_ml_eq"] = min(capacity, lower + ml_eq)
            if upper is not None:
                balance["upper_ml_eq"] = min(capacity, upper + ml_eq)

    def _apply_uncertainty_replay(self, event: Mapping[str, Any]) -> None:
        """Invalidate only the affected tank state/calibration cycle.

        An external cleaning with unresolved wet/dry parameters is a real physical
        water-consumption possibility. Pretending it consumed zero would make the
        next FULL/LOW cycle inflate calibration coefficients. Keep known derived
        events, but stop claiming an absolute balance until a later maintenance
        anchor establishes it again.
        """
        tank = str(event.get("tank") or "")
        if tank not in {"clean", "dirty"}:
            return
        balance = self.clean if tank == "clean" else self.dirty
        balance.update({
            "known": False,
            "estimate_ml_eq": None,
            "lower_ml_eq": None,
            "upper_ml_eq": None,
            "last_anchor": "external_usage_uncertain",
            "anchor_at": str(event.get("at") or _iso_now()),
            "state_confidence": "unknown",
            "usage_since_anchor_ml_eq": 0.0,
            "base_usage_since_anchor_ml_eq": 0.0,
            "base_floor_usage_since_anchor_ml_eq": 0.0,
            "base_wash_usage_since_anchor_ml_eq": 0.0,
            "anchor_volume_ml_eq": None,
            "cycle_start_anchor": None,
        })

    def _rebuild_from_ledger(self) -> None:
        state_baseline_at = _parse_dt(self.water_state_baseline.get("at"))
        if state_baseline_at is not None:
            self.clean = dict(self.water_state_baseline.get("clean") or self._new_balance("clean"))
            self.dirty = dict(self.water_state_baseline.get("dirty") or self._new_balance("dirty"))
        else:
            self.clean = self._new_balance("clean")
            self.dirty = self._new_balance("dirty")
        self.calibration = dict(self.model_baseline or self._fresh_calibration())
        self.cycles = []
        timeline: list[tuple[datetime, int, str, Mapping[str, Any]]] = []
        for event in self.events:
            if event.get("event_type") not in {"FLOOR_MOP", "MOP_WASH", "WATER_UNCERTAINTY", "SENSOR_THRESHOLD"}:
                continue
            dt = _parse_dt(event.get("at"))
            if dt and (state_baseline_at is None or dt > state_baseline_at):
                timeline.append((dt, 20, "event", event))
        for session in self.maintenance_sessions:
            if session.get("status") != "confirmed" or not session.get("interpretations"):
                continue
            dt = _parse_dt(self._session_time(session))
            if dt and (state_baseline_at is None or dt > state_baseline_at):
                timeline.append((dt, 10, "maintenance", session))
        timeline.sort(key=lambda row: (row[0], row[1]))
        for dt, _priority, kind, row in timeline:
            at = dt.isoformat()
            if kind == "maintenance":
                for tank, interpretation in dict(row.get("interpretations") or {}).items():
                    self._apply_interpretation(
                        str(tank), dict(interpretation or {}), at, source=str(row.get("source") or "detected")
                    )
                continue
            event_type = str(row.get("event_type") or "")
            tank = str(row.get("tank") or "")
            if event_type in {"FLOOR_MOP", "MOP_WASH"}:
                self._apply_usage_replay(row)
            elif event_type == "WATER_UNCERTAINTY" and tank in {"clean", "dirty"}:
                self._apply_uncertainty_replay(row)
            elif event_type == "SENSOR_THRESHOLD" and tank in {"clean", "dirty"}:
                self._close_cycle_replay(tank, at)
                balance = self.clean if tank == "clean" else self.dirty
                capacity = _number(balance.get("capacity_ml"))
                if capacity is not None:
                    estimate = 0.0 if tank == "clean" else capacity
                    balance.update({
                        "known": True,
                        "estimate_ml_eq": estimate,
                        "lower_ml_eq": estimate,
                        "upper_ml_eq": estimate,
                        "last_anchor": "sensor_threshold",
                        "anchor_at": at,
                        "state_confidence": "high",
                        "usage_since_anchor_ml_eq": 0.0,
                        "base_usage_since_anchor_ml_eq": 0.0,
                        "base_floor_usage_since_anchor_ml_eq": 0.0,
                        "base_wash_usage_since_anchor_ml_eq": 0.0,
                        "anchor_volume_ml_eq": estimate,
                        "cycle_start_anchor": None,
                    })
        self._sync_pending_projection()

    def _record_job_usage(self, record: Mapping[str, Any]) -> bool:
        """Append one Job's derived water events without persistence/rebuild."""
        job_id = str(record.get("job_id") or "")
        if not job_id or job_id in self.processed_job_ids:
            return False
        if str(record.get("execution_mode")) != "REAL":
            self.processed_job_ids.add(job_id)
            return True
        if self.profile is None:
            return False
        params = dict(record.get("cleaning_params_snapshot") or {})
        scope = cleaning_scope(params)
        area_data = record.get("area") or {}
        # Floor-water consumption is driven by physical coverage, so repeated
        # passes use processed area rather than unique floor area.
        effective_area = _number(area_data.get("processed_m2")) or _number(area_data.get("physical_cleaned_m2"))
        passes = max(1.0, _number(params.get("passes")) or 1.0)
        if scope.uses_water and effective_area is not None and effective_area > 0:
            base_usage = effective_area * self.profile.floor_ml_per_effective_m2
            self._append_usage("clean", "FLOOR_MOP", base_usage, record, {
                "effective_area_m2": effective_area,
                "configured_passes": passes,
                "cleaning_scope": scope.kind,
                "cleaning_scope_source": scope.source,
                "cleaning_mode": params.get("cleaning_mode"),
                "mop_mode": params.get("mop_mode"),
                "water_mode": params.get("water_mode"),
                "base_ml_eq": base_usage,
            })
        elif scope.kind == "unknown" and effective_area is not None and effective_area > 0:
            # We observed physical floor work but cannot prove whether the robot
            # was dry or wet. Zero would corrupt the next FULL→LOW calibration
            # cycle, while assuming wet would recreate the old over-counting bug.
            self._append_uncertainty(
                "clean", record, reason="cleaning_scope_unresolved"
            )
        for wash in (record.get("water_facts") or {}).get("wash_events") or []:
            self._append_usage("clean", "MOP_WASH", self.profile.clean_wash_ml, record, {**dict(wash), "base_ml_eq": self.profile.clean_wash_ml})
            self._append_usage("dirty", "MOP_WASH", self.profile.dirty_wash_ml, record, {**dict(wash), "base_ml_eq": self.profile.dirty_wash_ml})
        external = record.get("external_execution") or {}
        if (
            str(record.get("origin") or "").upper() == "EXTERNAL"
            and isinstance(external, Mapping)
            and bool(external.get("water_uncertain_clean_floor"))
            and scope.kind != "unknown"
        ):
            self._append_uncertainty(
                "clean",
                record,
                reason="external_floor_water_parameters_unresolved",
            )
        self.processed_job_ids.add(job_id)
        return True

    async def async_record_job(self, record: Mapping[str, Any]) -> None:
        if not self._record_job_usage(record):
            return
        self._rebuild_from_ledger()
        await self.async_save()

    async def async_repair_job_records(self, records: list[Mapping[str, Any]]) -> int:
        """Rebuild derived water events for repaired Statistics Ledger Jobs.

        The raw maintenance/sensor ledger is preserved.  Only FLOOR_MOP and
        MOP_WASH events derived from the supplied Job records are replaced, then
        balances/calibration are replayed chronologically from the complete ledger.
        """
        if self.profile is None:
            return 0
        by_job = {str(record.get("job_id") or ""): dict(record) for record in records if str(record.get("job_id") or "")}
        if not by_job:
            return 0
        job_ids = set(by_job)
        before = len(self.events)
        self.events = [
            event for event in self.events
            if not (
                str(event.get("job_id") or "") in job_ids
                and str(event.get("event_type") or "") in {"FLOOR_MOP", "MOP_WASH", "WATER_UNCERTAINTY"}
            )
        ]
        self.processed_job_ids.difference_update(job_ids)
        rebuilt = 0
        for record in by_job.values():
            if self._record_job_usage(record):
                rebuilt += 1
        if rebuilt or len(self.events) != before:
            self._rebuild_from_ledger()
            await self.async_save()
        return rebuilt

    def _append_uncertainty(self, tank: str, record: Mapping[str, Any], *, reason: str) -> None:
        at = str(record.get("finished_at") or record.get("recorded_at") or _iso_now())
        self.events.append({
            "event_id": uuid4().hex,
            "event_type": "WATER_UNCERTAINTY",
            "tank": tank,
            "at": at,
            "job_id": record.get("job_id"),
            "estimated_ml_eq": None,
            "observation_quality": "external_partial",
            "details": {"reason": str(reason)},
        })

    def _append_usage(self, tank: str, event_type: str, base_ml_eq: float, record: Mapping[str, Any], details: Mapping[str, Any]) -> None:
        at = str(record.get("finished_at") or record.get("recorded_at") or _iso_now())
        scale = self._usage_scale(tank, event_type)
        self.events.append({
            "event_id": uuid4().hex,
            "event_type": event_type,
            "tank": tank,
            "at": at,
            "job_id": record.get("job_id"),
            "estimated_ml_eq": float(max(0.0, base_ml_eq * scale)),
            "observation_quality": "derived_from_robot_facts",
            "details": {**dict(details), "base_ml_eq": float(base_ml_eq), "applied_scale": scale},
        })

    def job_usage_summaries(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Return water usage for many Jobs with one ledger scan.

        Forecast UI can request thousands of trained samples. Scanning the whole
        water ledger once per Job makes that path quadratic and can stall the HA
        event loop long enough for the Statistics page to remain on its loading
        placeholder. Keep the public summary shape, but batch the lookup.
        """
        wanted = {str(job_id or "") for job_id in job_ids if str(job_id or "")}
        grouped: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in wanted}
        if wanted:
            for event in self.events:
                job_id = str(event.get("job_id") or "")
                if job_id in grouped:
                    grouped[job_id].append(dict(event))
        result: dict[str, dict[str, Any]] = {}
        for job_id in wanted:
            rows = grouped.get(job_id, [])
            clean = sum(float(event.get("estimated_ml_eq") or 0.0) for event in rows if event.get("tank") == "clean")
            dirty = sum(float(event.get("estimated_ml_eq") or 0.0) for event in rows if event.get("tank") == "dirty")
            available = job_id in self.processed_job_ids
            uncertainty = [dict(event) for event in rows if event.get("event_type") == "WATER_UNCERTAINTY"]
            result[job_id] = {
                "available": available,
                "complete": available and not uncertainty,
                "clean_used_ml_eq": clean if available else None,
                "dirty_gained_ml_eq": dirty if available else None,
                "events": rows,
                "uncertainty_events": uncertainty,
                "wash_count": sum(1 for event in rows if event.get("event_type") == "MOP_WASH" and event.get("tank") == "clean"),
                "floor_mop_count": sum(1 for event in rows if event.get("event_type") == "FLOOR_MOP" and event.get("tank") == "clean"),
            }
        return result

    def job_usage_summary(self, job_id: str) -> dict[str, Any]:
        job_id = str(job_id or "")
        if not job_id:
            return {
                "available": False,
                "complete": False,
                "clean_used_ml_eq": None,
                "dirty_gained_ml_eq": None,
                "events": [],
                "uncertainty_events": [],
                "wash_count": 0,
                "floor_mop_count": 0,
            }
        return self.job_usage_summaries([job_id])[job_id]

    # ---------------------------------------------------------------------
    # Data-management layers (0.10.22)
    # ---------------------------------------------------------------------

    def export_statistics_layer(self) -> dict[str, Any]:
        derived = [
            dict(event) for event in self.events
            if str(event.get("event_type") or "") in {"FLOOR_MOP", "MOP_WASH", "WATER_UNCERTAINTY"}
        ]
        return {
            "derived_events": derived,
            "processed_job_ids": sorted(self.processed_job_ids),
            "cycles": [dict(item) for item in self.cycles],
        }

    def export_model_layer(self) -> dict[str, Any]:
        return {
            "calibration": dict(self.calibration),
            "model_baseline": dict(self.model_baseline),
            "model_epoch": self.model_epoch,
        }

    def export_maintenance_layer(self) -> dict[str, Any]:
        return {
            "confirmed_sessions": [
                dict(item) for item in self.maintenance_sessions
                if item.get("status") == "confirmed"
            ],
            "water_state_baseline": dict(self.water_state_baseline),
        }

    async def async_clear_statistics_layer(self) -> dict[str, int]:
        event_types = {"FLOOR_MOP", "MOP_WASH"}
        before = len(self.events)
        self.events = [item for item in self.events if str(item.get("event_type") or "") not in event_types]
        jobs = len(self.processed_job_ids)
        self.processed_job_ids = set()
        cycles = len(self.cycles)
        self.cycles = []
        # Freeze learned coefficients before deleting their training facts.
        self.model_baseline = dict(self.calibration or self._fresh_calibration())
        self.model_epoch = _iso_now()
        self._rebuild_from_ledger()
        await self.async_save()
        return {"water_events": before - len(self.events), "water_jobs": jobs, "water_cycles": cycles}

    async def async_restore_statistics_layer(self, payload: Mapping[str, Any]) -> dict[str, int]:
        event_types = {"FLOOR_MOP", "MOP_WASH"}
        self.events = [item for item in self.events if str(item.get("event_type") or "") not in event_types]
        restored_events = [dict(item) for item in payload.get("derived_events", []) if isinstance(item, Mapping)]
        self.events.extend(restored_events)
        self.processed_job_ids = {str(value) for value in payload.get("processed_job_ids", []) if value}
        self._rebuild_from_ledger()
        await self.async_save()
        return {"water_events": len(restored_events), "water_jobs": len(self.processed_job_ids)}

    async def async_clear_model_layer(self) -> None:
        self.model_baseline = self._fresh_calibration()
        self.model_epoch = _iso_now()
        self.calibration = dict(self.model_baseline)
        self._rebuild_from_ledger()
        await self.async_save()

    async def async_restore_model_layer(self, payload: Mapping[str, Any]) -> None:
        calibration = dict(payload.get("calibration") or payload.get("model_baseline") or self._fresh_calibration())
        self.model_baseline = calibration
        # The restored trained coefficients are the new independent baseline;
        # historical water facts must not immediately train them a second time.
        self.model_epoch = _iso_now()
        self.calibration = dict(calibration)
        if repair_water_calibration_v4(self):
            # Pre-v4 model backups may contain coefficients learned from dry Jobs
            # misclassified as wet. Never resurrect that calibration.
            self.model_epoch = None
        self._rebuild_from_ledger()
        await self.async_save()

    async def async_clear_maintenance_history(self) -> int:
        confirmed = [item for item in self.maintenance_sessions if item.get("status") == "confirmed"]
        if not confirmed:
            return 0
        # Preserve the current operational tank estimate while deleting its
        # human-readable service history. This baseline is not shown as a fake
        # maintenance event and only anchors future synthetic accounting.
        self.water_state_baseline = {
            "at": _iso_now(),
            "clean": dict(self.clean),
            "dirty": dict(self.dirty),
        }
        self.maintenance_sessions = [item for item in self.maintenance_sessions if item.get("status") != "confirmed"]
        self._rebuild_from_ledger()
        await self.async_save()
        return len(confirmed)

    async def async_restore_maintenance_layer(self, payload: Mapping[str, Any]) -> int:
        pending = [dict(item) for item in self.maintenance_sessions if item.get("status") != "confirmed"]
        confirmed = [
            dict(item) for item in payload.get("confirmed_sessions", [])
            if isinstance(item, Mapping)
        ]
        self.maintenance_sessions = [*pending, *confirmed]
        self.water_state_baseline = dict(payload.get("water_state_baseline") or {})
        self._rebuild_from_ledger()
        await self.async_save()
        return len(confirmed)

    # ---------------------------------------------------------------------
    # Payloads
    # ---------------------------------------------------------------------

    @staticmethod
    def _balance_payload(balance: Mapping[str, Any], *, dirty: bool = False) -> dict[str, Any]:
        capacity = _number(balance.get("capacity_ml"))
        estimate = _number(balance.get("estimate_ml_eq"))
        result = dict(balance)
        percent = None
        if capacity and estimate is not None:
            percent = max(0.0, min(100.0, estimate / capacity * 100.0))
        if dirty:
            result["filled_percent"] = percent
            result["free_percent"] = None if percent is None else 100.0 - percent
            result["free_ml_eq"] = None if capacity is None or estimate is None else max(0.0, capacity - estimate)
        else:
            result["remaining_percent"] = percent
        return result

    def _model_quality(self, tank: str) -> str:
        if self.profile is None:
            return "unavailable"
        observations = [
            item for item in self.calibration.get(f"{tank}_scale_observations", [])
            if isinstance(item, Mapping) and _number(item.get("scale")) is not None
        ]
        count = len(observations)
        if count == 0:
            return "bootstrap"
        if count <= 2:
            return "low"
        if count <= 7:
            return "medium"
        return "high"

    def _median_cycle_error(self, tank: str) -> float | None:
        values = sorted(
            float(value)
            for value in (
                cycle.get("cycle_error_percent")
                for cycle in self.cycles
                if cycle.get("tank") == tank and cycle.get("accepted_for_calibration") is not False
            )
            if _number(value) is not None
        )
        if not values:
            return None
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0

    def _session_public(self, session: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session.get("session_id"),
            "source": session.get("source"),
            "detected_at": session.get("detected_at"),
            "occurred_at": session.get("occurred_at"),
            "status": session.get("status"),
            "detection": dict(session.get("detection") or {}),
            "interpretations": dict(session.get("interpretations") or {}),
            "note": session.get("note"),
            "revisions": list(session.get("revisions") or []),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }

    def maintenance_payload(self, *, limit: int = 100) -> dict[str, Any]:
        self._sync_pending_projection()
        ordered = sorted(self.maintenance_sessions, key=self._session_time, reverse=True)
        pending = [self._session_public(s) for s in ordered if s.get("status") == "pending"]
        confirmed = [self._session_public(s) for s in ordered if s.get("status") == "confirmed"]
        last = confirmed[0] if confirmed else None
        return {
            "profile": self.profile.to_dict() if self.profile else None,
            "clean": self._balance_payload(self.clean),
            "dirty": self._balance_payload(self.dirty, dirty=True),
            "pending_sessions": pending,
            "sessions": [self._session_public(s) for s in ordered[: max(1, int(limit))]],
            "summary": {
                "pending_count": len(pending),
                "confirmed_count": len(confirmed),
                "last_service_at": last.get("occurred_at") if last else None,
                "last_service_session_id": last.get("session_id") if last else None,
            },
        }

    def payload(self) -> dict[str, Any]:
        maintenance = self.maintenance_payload(limit=30)
        return {
            "profile": self.profile.to_dict() if self.profile else None,
            "clean": self._balance_payload(self.clean),
            "dirty": self._balance_payload(self.dirty, dirty=True),
            "pending_service": list(self.pending_service.values()),
            "maintenance": maintenance,
            "calibration": {
                **dict(self.calibration),
                "clean_model_quality": self._model_quality("clean"),
                "dirty_model_quality": self._model_quality("dirty"),
                "clean_median_cycle_error_percent": self._median_cycle_error("clean"),
                "dirty_median_cycle_error_percent": self._median_cycle_error("dirty"),
            },
            "recent_events": list(reversed(self.events[-30:])),
            "recent_cycles": list(reversed(self.cycles[-20:])),
        }
