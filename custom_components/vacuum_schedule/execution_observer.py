"""Physical robot observation for Vacuum Schedule.

The scheduler never infers physical execution from service-call success.  This
module turns Home Assistant state plus optional vendor diagnostics into one
stable observation contract used by the REAL execution backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .models import NormalizedVacuumState


class RobotExecutionPhase(StrEnum):
    """Normalized physical phase reported by the robot observer."""

    IDLE = "IDLE"
    CLEANING = "CLEANING"
    PAUSED = "PAUSED"
    SERVICE = "SERVICE"
    SERVICE_BLOCKED = "SERVICE_BLOCKED"
    RETURNING = "RETURNING"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


_ROBOROCK_SERVICE_STATES = {
    "attaching_the_mop",
    "detaching_the_mop",
    "emptying_the_bin",
    "washing_the_mop",
    "going_to_wash_the_mop",
    "waiting_to_charge",
}
_ROBOROCK_SEGMENT_STATES = {"segment_cleaning"}
_ROBOROCK_ZONE_STATES = {"zoned_cleaning"}
_ROBOROCK_OTHER_CLEAN_STATES = {
    "cleaning",
    "spot_cleaning",
    "manual_mode",
    "remote_control_active",
    "going_to_target",
    "mapping",
    "relocating",
    "sweeping",
    "mopping",
    "sweep_and_mop",
    "transitioning",
}
_NO_ERROR_VALUES = {"", "0", "none", "no_error", "ok", "unknown", "unavailable"}
_INACTIVE_ACTIVITY_VALUES = {
    "", "0", "off", "false", "no", "idle", "standby", "ready", "none",
    "inactive", "disabled", "unknown", "unavailable", "not_available",
    "not_supported", "not_washing", "complete", "completed", "done", "ok",
}
_DIAGNOSTIC_ALIASES = {
    "status": ("status",),
    "cleaning_time": ("cleaning_time",),
    "cleaning_area": ("cleaning_area",),
    "clean_percent": ("clean_percent",),
    "last_clean_start": ("last_clean_start",),
    "last_clean_end": ("last_clean_end",),
    "vacuum_error": ("vacuum_error",),
    "dock_error": ("dock_error", "dock_error_status"),
    "battery": ("battery",),
    "selected_map": ("selected_map",),
    "wash_mode": ("wash_mode",),
    "smart_wash": ("smart_wash",),
    "wash_interval": ("wash_interval",),
    # Dock activity diagnostics are optional and model/HA-version dependent.
    # Keep several known naming variants so richer live UI degrades safely.
    "wash_status": ("wash_status", "mop_wash_status", "dock_wash_status"),
    "wash_phase": ("wash_phase", "mop_wash_phase", "dock_wash_phase"),
    "dry_status": (
        "dry_status",
        "drying_status",
        "mop_drying",
        "mop_drying_status",
        "dock_drying_status",
    ),
    "dust_collection_status": (
        "dust_collection_status", "dust_collection", "auto_empty_status",
        "dock_dust_collection_status",
    ),
    "charge_status": ("charge_status", "charging_status"),
    "back_type": ("back_type", "return_reason"),
}
_DIAGNOSTIC_KEYS = tuple(_DIAGNOSTIC_ALIASES)
_DIAGNOSTIC_ALIAS_TO_KEY = {
    alias: key for key, aliases in _DIAGNOSTIC_ALIASES.items() for alias in aliases
}
# Home Assistant replaces Roborock's deprecated mop-drying binary sensor with
# an actionable switch. Prefer the replacement while retaining the old entity
# as a compatibility fallback until Home Assistant removes it.
_DIAGNOSTIC_ALIAS_PRIORITY = {
    "mop_drying": 20,
    "mop_drying_status": 10,
}


def _resource_blocker_from_error(value: str | None) -> str | None:
    """Translate recoverable dock/resource conditions to pre-flight blocker codes.

    Roborock exposes some service conditions through ``dock_error_status`` even
    though they are not failures of an already completed cleaning task. Keep
    those conditions in the same semantic namespace as pre-flight so the
    scheduler can wait for user service instead of converting them to a generic
    physical execution failure.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text or text in _NO_ERROR_VALUES:
        return None

    if (
        ("dirty_water" in text or "waste_water" in text or "dirty_box" in text)
        and any(token in text for token in ("full", "filled", "overflow", "needs_service"))
    ):
        return "dirty_water_full"
    # Older Roborock mappings sometimes use the generic water-box wording for
    # the dirty-water receptacle.
    if "water_box_full" in text and not any(token in text for token in ("clean", "fresh")):
        return "dirty_water_full"
    if (
        ("clean_water" in text or "fresh_water" in text or "clean_box" in text)
        and any(token in text for token in ("empty", "shortage", "insufficient", "missing"))
    ):
        return "clean_water_insufficient"
    if "water_shortage" in text:
        return "clean_water_insufficient"
    if (
        any(token in text for token in ("detergent", "clean_fluid", "cleaning_solution"))
        and any(token in text for token in ("empty", "missing", "insufficient", "unavailable"))
    ):
        return "detergent_unavailable"
    return None


def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name:
        return str(name).strip().lower()
    raw = getattr(value, "value", value)
    text = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _diagnostic_activity_active(value: Any) -> bool:
    """Return whether an optional dock diagnostic reports active work."""
    text = _enum_text(value)
    return bool(text and text not in _INACTIVE_ACTIVITY_VALUES)


def _dock_service_activity(
    vendor_status: str | None,
    *,
    wash_status: Any = None,
    wash_phase: Any = None,
    dust_collection_status: Any = None,
) -> tuple[bool, bool]:
    """Return ``(service_active, auto_empty_active)`` from all live sources.

    Dock diagnostics are also rendered by the frontend, so the ownership state
    machine must consume the same evidence. Drying remains intentionally absent:
    it can last for hours and is not part of the cleaning Execution Lease.
    """
    normalized_status = _enum_text(vendor_status)
    auto_empty_active = (
        normalized_status == "emptying_the_bin"
        or _diagnostic_activity_active(dust_collection_status)
    )
    wash_active = (
        _diagnostic_activity_active(wash_status)
        or _diagnostic_activity_active(wash_phase)
    )
    return (
        normalized_status in _ROBOROCK_SERVICE_STATES
        or auto_empty_active
        or wash_active,
        auto_empty_active,
    )


@dataclass(slots=True, frozen=True)
class RobotExecutionObservation:
    """One fresh, JSON-friendly view of the physical robot."""

    observed_at: datetime
    normalized_state: NormalizedVacuumState
    phase: RobotExecutionPhase
    raw_state: str | None = None
    vendor: str = "generic"
    vendor_status: str | None = None
    task_kind: str | None = None
    session_active: bool = False
    in_cleaning: int | None = None
    current_map: str | int | None = None
    battery_percent: float | None = None
    cleaning_time_seconds: float | None = None
    cleaning_area_m2: float | None = None
    clean_percent: float | None = None
    wash_mode: str | None = None
    smart_wash: str | None = None
    wash_interval: float | None = None
    wash_status: str | None = None
    wash_phase: str | None = None
    dry_status: str | None = None
    dust_collection_status: str | None = None
    charge_status: str | None = None
    back_type: str | None = None
    last_clean_start: datetime | None = None
    last_clean_end: datetime | None = None
    vacuum_error: str | None = None
    dock_error: str | None = None
    resource_blockers: tuple[str, ...] = ()
    service_activity: bool = False
    auto_empty_activity: bool = False
    raw_vacuum_error: str | None = None
    raw_dock_error: str | None = None
    source_entities: dict[str, str] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.phase not in {RobotExecutionPhase.ERROR, RobotExecutionPhase.UNAVAILABLE}

    def matches_target_type(self, target_type: str) -> bool:
        """Return whether an active vendor task is compatible with the request."""
        if not self.session_active:
            return False
        if not self.task_kind or self.task_kind == "unknown":
            return self.phase in {
                RobotExecutionPhase.CLEANING,
                RobotExecutionPhase.SERVICE,
                RobotExecutionPhase.SERVICE_BLOCKED,
                RobotExecutionPhase.PAUSED,
            }
        if target_type == "segment":
            return self.task_kind == "segment"
        if target_type == "zone":
            return self.task_kind == "zone"
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "normalized_state": self.normalized_state.value,
            "phase": self.phase.value,
            "raw_state": self.raw_state,
            "vendor": self.vendor,
            "vendor_status": self.vendor_status,
            "task_kind": self.task_kind,
            "session_active": self.session_active,
            "in_cleaning": self.in_cleaning,
            "current_map": self.current_map,
            "battery_percent": self.battery_percent,
            "cleaning_time_seconds": self.cleaning_time_seconds,
            "cleaning_area_m2": self.cleaning_area_m2,
            "clean_percent": self.clean_percent,
            "wash_mode": self.wash_mode,
            "smart_wash": self.smart_wash,
            "wash_interval": self.wash_interval,
            "wash_status": self.wash_status,
            "wash_phase": self.wash_phase,
            "dry_status": self.dry_status,
            "dust_collection_status": self.dust_collection_status,
            "charge_status": self.charge_status,
            "back_type": self.back_type,
            "last_clean_start": self.last_clean_start.isoformat() if self.last_clean_start else None,
            "last_clean_end": self.last_clean_end.isoformat() if self.last_clean_end else None,
            "vacuum_error": self.vacuum_error,
            "dock_error": self.dock_error,
            "resource_blockers": list(self.resource_blockers),
            "service_activity": self.service_activity,
            "auto_empty_activity": self.auto_empty_activity,
            "raw_vacuum_error": self.raw_vacuum_error,
            "raw_dock_error": self.raw_dock_error,
            "source_entities": dict(self.source_entities),
        }


class RobotExecutionObserver:
    """Generic Home Assistant observer with optional Roborock enrichment."""

    def __init__(self, hass: Any, executor: Any) -> None:
        self.hass = hass
        self.executor = executor
        self._vendor = self._detect_vendor()

    def _loaded_vacuum_entity(self) -> Any | None:
        entity_id = self.executor.vacuum_entity_id
        component = self.hass.data.get(DATA_INSTANCES, {}).get("vacuum")
        return component.get_entity(entity_id) if component is not None and entity_id else None

    def _detect_vendor(self) -> str:
        entity_id = self.executor.vacuum_entity_id
        if not entity_id:
            return "generic"
        try:
            entry = er.async_get(self.hass).async_get(entity_id)
        except Exception:
            entry = None
        platform = str(getattr(entry, "platform", "") or "").lower()
        return "roborock" if platform == "roborock" else (platform or "generic")

    @property
    def vendor(self) -> str:
        return self._vendor

    def _related_registry_entries(self) -> tuple[Any, ...]:
        entity_id = self.executor.vacuum_entity_id
        if not entity_id:
            return ()
        try:
            registry = er.async_get(self.hass)
            source = registry.async_get(entity_id)
        except Exception:
            return ()
        device_ids = set(self.executor.related_device_ids)
        if source is not None and getattr(source, "device_id", None):
            device_ids.add(source.device_id)
        result: dict[str, Any] = {}
        for device_id in device_ids:
            try:
                entries = er.async_entries_for_device(
                    registry, device_id, include_disabled_entities=False
                )
            except Exception:
                entries = ()
            for entry in entries:
                result[entry.entity_id] = entry
        for manual_entity in self.executor.related_entities:
            item = registry.async_get(manual_entity)
            if item is not None:
                result[item.entity_id] = item
        return tuple(result.values())

    def _diagnostic_entries(self) -> dict[str, Any]:
        """Return the best registry entity for every observer diagnostic key.

        The same resolver is used both for observations and event subscriptions.
        This is important for REAL execution: a diagnostic entity such as
        ``last_clean_end`` must be able to wake the Scheduler immediately instead
        of waiting for the watchdog merely because it is not a pre-flight input.
        """
        excluded_unique_suffixes = ("total_cleaning_area", "total_cleaning_time")
        match_quality: dict[str, int] = {}
        result: dict[str, Any] = {}
        for entry in self._related_registry_entries():
            translation_key = str(getattr(entry, "translation_key", "") or "").lower()
            unique_id = str(getattr(entry, "unique_id", "") or "").lower()
            matched = None
            quality = 0
            if translation_key in _DIAGNOSTIC_ALIAS_TO_KEY:
                matched = _DIAGNOSTIC_ALIAS_TO_KEY[translation_key]
                quality = 100 + _DIAGNOSTIC_ALIAS_PRIORITY.get(translation_key, 0)
            elif not translation_key and not any(
                unique_id.endswith(f"_{suffix}") or unique_id == suffix
                for suffix in excluded_unique_suffixes
            ):
                for alias in sorted(_DIAGNOSTIC_ALIAS_TO_KEY, key=len, reverse=True):
                    if unique_id.endswith(f"_{alias}") or unique_id == alias:
                        matched = _DIAGNOSTIC_ALIAS_TO_KEY[alias]
                        quality = 10 + _DIAGNOSTIC_ALIAS_PRIORITY.get(alias, 0)
                        break
            if matched is None or quality < match_quality.get(matched, -1):
                continue
            result[matched] = entry
            match_quality[matched] = quality
        return result

    def observation_dependencies(self) -> tuple[str, ...]:
        """Return HA entities whose changes may alter a physical observation."""
        entity_ids = {
            str(getattr(entry, "entity_id", "") or "")
            for entry in self._diagnostic_entries().values()
        }
        if self.executor.vacuum_entity_id:
            entity_ids.add(str(self.executor.vacuum_entity_id))
        return tuple(sorted(entity_id for entity_id in entity_ids if entity_id))

    def _diagnostic_states(self) -> tuple[dict[str, Any], dict[str, str]]:
        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for key, entry in self._diagnostic_entries().items():
            entity_id = str(getattr(entry, "entity_id", "") or "")
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            values[key] = state.state
            sources[key] = entity_id
        return values, sources

    @staticmethod
    def _task_kind(vendor_status: str | None) -> str | None:
        if vendor_status in _ROBOROCK_SEGMENT_STATES:
            return "segment"
        if vendor_status in _ROBOROCK_ZONE_STATES:
            return "zone"
        if vendor_status in {"spot_cleaning"}:
            return "spot"
        if vendor_status in _ROBOROCK_OTHER_CLEAN_STATES:
            return "full" if vendor_status == "cleaning" else "other"
        return None

    def observe(self, now: datetime) -> RobotExecutionObservation:
        snapshot = self.executor.snapshot()
        normalized = snapshot.normalized_state
        raw_state = snapshot.raw_state
        vendor_status: str | None = None
        in_cleaning: int | None = None
        current_map: str | int | None = None
        native_vacuum_error: str | None = None
        native_dock_error: str | None = None

        loaded = self._loaded_vacuum_entity()
        if self.vendor == "roborock" and loaded is not None:
            status_trait = getattr(loaded, "_status_trait", None)
            vendor_status = _enum_text(getattr(status_trait, "state", None))
            native_vacuum_error = _enum_text(getattr(status_trait, "error_code", None))
            native_dock_error = _enum_text(getattr(status_trait, "dock_error_status", None))
            raw_in_cleaning = getattr(status_trait, "in_cleaning", None)
            try:
                in_cleaning = int(raw_in_cleaning) if raw_in_cleaning is not None else None
            except (TypeError, ValueError):
                in_cleaning = None
            maps_trait = getattr(loaded, "_maps_trait", None)
            current_map = getattr(maps_trait, "current_map", None)

        diagnostics, sources = self._diagnostic_states()
        if vendor_status is None:
            vendor_status = _enum_text(diagnostics.get("status"))

        raw_vacuum_error = native_vacuum_error or _enum_text(diagnostics.get("vacuum_error"))
        if raw_vacuum_error in _NO_ERROR_VALUES:
            raw_vacuum_error = None
        raw_dock_error = native_dock_error or _enum_text(diagnostics.get("dock_error"))
        if raw_dock_error in _NO_ERROR_VALUES:
            raw_dock_error = None

        resource_blockers = tuple(
            dict.fromkeys(
                blocker
                for blocker in (
                    _resource_blocker_from_error(raw_vacuum_error),
                    _resource_blocker_from_error(raw_dock_error),
                )
                if blocker
            )
        )
        vacuum_error = (
            None if _resource_blocker_from_error(raw_vacuum_error) else raw_vacuum_error
        )
        dock_error = None if _resource_blocker_from_error(raw_dock_error) else raw_dock_error

        service_state, auto_empty_activity = _dock_service_activity(
            vendor_status,
            wash_status=diagnostics.get("wash_status"),
            wash_phase=diagnostics.get("wash_phase"),
            dust_collection_status=diagnostics.get("dust_collection_status"),
        )
        session_hint = service_state or in_cleaning not in (None, 0)

        if normalized is NormalizedVacuumState.UNAVAILABLE:
            phase = RobotExecutionPhase.UNAVAILABLE
        elif resource_blockers and session_hint:
            phase = RobotExecutionPhase.SERVICE_BLOCKED
        elif vacuum_error or dock_error or (
            normalized is NormalizedVacuumState.ERROR and not resource_blockers
        ):
            phase = RobotExecutionPhase.ERROR
        elif service_state:
            phase = RobotExecutionPhase.SERVICE
        elif vendor_status in _ROBOROCK_SEGMENT_STATES | _ROBOROCK_ZONE_STATES | _ROBOROCK_OTHER_CLEAN_STATES:
            phase = RobotExecutionPhase.CLEANING
        elif normalized is NormalizedVacuumState.PAUSED:
            phase = RobotExecutionPhase.PAUSED
        elif normalized is NormalizedVacuumState.RETURNING:
            phase = RobotExecutionPhase.SERVICE if in_cleaning not in (None, 0) else RobotExecutionPhase.RETURNING
        elif normalized in {NormalizedVacuumState.DOCKED, NormalizedVacuumState.IDLE}:
            phase = RobotExecutionPhase.SERVICE if in_cleaning not in (None, 0) else RobotExecutionPhase.IDLE
        elif normalized is NormalizedVacuumState.CLEANING:
            phase = RobotExecutionPhase.CLEANING
        else:
            phase = RobotExecutionPhase.UNKNOWN

        session_active = phase in {
            RobotExecutionPhase.CLEANING,
            RobotExecutionPhase.PAUSED,
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
        } or in_cleaning not in (None, 0)

        battery = snapshot.raw_attributes.get("battery_level")
        if battery is None:
            battery = diagnostics.get("battery")
        return RobotExecutionObservation(
            observed_at=now,
            normalized_state=normalized,
            phase=phase,
            raw_state=raw_state,
            vendor=self.vendor,
            vendor_status=vendor_status,
            task_kind=self._task_kind(vendor_status),
            session_active=session_active,
            in_cleaning=in_cleaning,
            current_map=current_map if current_map is not None else diagnostics.get("selected_map"),
            battery_percent=_number(battery),
            cleaning_time_seconds=_number(diagnostics.get("cleaning_time")),
            cleaning_area_m2=_number(diagnostics.get("cleaning_area")),
            clean_percent=_number(diagnostics.get("clean_percent")),
            wash_mode=_enum_text(diagnostics.get("wash_mode")),
            smart_wash=_enum_text(diagnostics.get("smart_wash")),
            wash_interval=_number(diagnostics.get("wash_interval")),
            wash_status=_enum_text(diagnostics.get("wash_status")),
            wash_phase=_enum_text(diagnostics.get("wash_phase")),
            dry_status=_enum_text(diagnostics.get("dry_status")),
            dust_collection_status=_enum_text(diagnostics.get("dust_collection_status")),
            charge_status=_enum_text(diagnostics.get("charge_status")),
            back_type=_enum_text(diagnostics.get("back_type")),
            last_clean_start=_parse_datetime(diagnostics.get("last_clean_start")),
            last_clean_end=_parse_datetime(diagnostics.get("last_clean_end")),
            vacuum_error=vacuum_error,
            dock_error=dock_error,
            resource_blockers=resource_blockers,
            service_activity=service_state,
            auto_empty_activity=auto_empty_activity,
            raw_vacuum_error=raw_vacuum_error,
            raw_dock_error=raw_dock_error,
            source_entities=sources,
        )
