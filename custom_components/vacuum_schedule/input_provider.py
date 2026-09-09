"""Read-only Home Assistant input normalization for Vacuum Schedule 0.5.0."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
import hashlib
import json
from typing import Any, Mapping

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .bindings import BindingMode, CapabilityBinding, PreflightPolicy, bindings_from_options
from .const import CONF_CAPABILITY_BINDINGS, CONF_PREFLIGHT_POLICY, CONF_CLEANING_ZONES
from .models import NormalizedVacuumState, VacuumSnapshot
from .cleaning_zones import CleaningZone, aggregate_access_paths, condition_matches, cleaning_zones_from_options, effective_zone_delay
from .input_overrides import TestOverride, TestOverrideManager
from .time_utils import as_utc, instant_add, instant_le

_UNAVAILABLE_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN}


@dataclass(slots=True, frozen=True)
class NormalizedInput:
    """One normalized input with live/effective values and source provenance."""

    key: str
    live_value: Any
    effective_value: Any
    live_available: bool
    effective_available: bool
    source_entity_id: str | None = None
    source_attribute: str | None = None
    raw_value: Any = None
    binding_mode: str = "auto"
    auto_candidate: str | None = None
    override: TestOverride | None = None
    status: str = "ready"
    note: str | None = None
    stabilizing: bool = False
    stabilizing_since: datetime | None = None
    stabilizing_until: datetime | None = None
    configured_delay_seconds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "live": self.live_value,
            "effective": self.effective_value,
            "live_available": self.live_available,
            "effective_available": self.effective_available,
            "source_entity_id": self.source_entity_id,
            "source_attribute": self.source_attribute,
            "raw_value": self.raw_value,
            "binding_mode": self.binding_mode,
            "auto_candidate": self.auto_candidate,
            "override": self.override.to_dict() if self.override else None,
            "status": self.status,
            "note": self.note,
            "stabilizing": self.stabilizing,
            "stabilizing_since": self.stabilizing_since.isoformat() if self.stabilizing_since else None,
            "stabilizing_until": self.stabilizing_until.isoformat() if self.stabilizing_until else None,
            "configured_delay_seconds": self.configured_delay_seconds,
        }


@dataclass(slots=True, frozen=True)
class CleaningZoneInputSnapshot:
    zone_id: str
    name: str
    robot_target_type: str
    robot_target_id: str
    control: str
    disabled_until: str | None
    busy: NormalizedInput
    accessible: NormalizedInput
    path_details: tuple[dict[str, Any], ...] = ()
    source_details: tuple[dict[str, Any], ...] = ()

    @property
    def dependencies(self) -> tuple[str, ...]:
        values = {
            item.get("entity_id")
            for item in (*self.path_details, *self.source_details)
            if item.get("entity_id")
        }
        return tuple(sorted(str(item) for item in values if item))

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "robot_target_type": self.robot_target_type,
            "robot_target_id": self.robot_target_id,
            "control": self.control,
            "disabled_until": self.disabled_until,
            "busy": self.busy.to_dict(),
            "accessible": self.accessible.to_dict(),
            "path_details": list(self.path_details),
            "source_details": list(self.source_details),
            "dependencies": list(self.dependencies),
        }


@dataclass(slots=True, frozen=True)
class InputSnapshot:
    snapshot_id: str
    evaluated_at: datetime
    values: Mapping[str, NormalizedInput]
    zones: Mapping[str, CleaningZoneInputSnapshot]

    def flat(self) -> dict[str, NormalizedInput]:
        result = dict(self.values)
        for zone in self.zones.values():
            result[zone.busy.key] = zone.busy
            result[zone.accessible.key] = zone.accessible
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "values": {key: item.to_dict() for key, item in self.values.items()},
            "zones": {key: item.to_dict() for key, item in self.zones.items()},
        }


class InputProvider:
    """Normalize live HA states and apply non-invasive test overrides."""

    def __init__(self, hass: HomeAssistant, entry: Any, executor: Any, overrides: TestOverrideManager) -> None:
        self.hass = hass
        self.entry = entry
        self.executor = executor
        self.overrides = overrides
        # Runtime-only continuous-good timers. Missing state is conservatively
        # seeded from HA state timestamps on the first snapshot after restart.
        self._zone_stable_since: dict[tuple[str, str], datetime] = {}
        self._allow_test_overrides = True

    @property
    def policy(self) -> PreflightPolicy:
        return PreflightPolicy.from_dict(self.entry.options.get(CONF_PREFLIGHT_POLICY, {}))

    @property
    def bindings(self) -> dict[str, CapabilityBinding]:
        return bindings_from_options(self.entry.options.get(CONF_CAPABILITY_BINDINGS, {}))

    @property
    def cleaning_zones(self) -> list[CleaningZone]:
        return cleaning_zones_from_options(self.entry.options.get(CONF_CLEANING_ZONES, []))

    def snapshot(
        self, evaluated_at: datetime | None = None, *, allow_test_overrides: bool = True
    ) -> InputSnapshot:
        """Return a normalized snapshot. REAL jobs can hard-disable test overrides."""
        evaluated_at = evaluated_at or dt_util.now()
        previous_override_policy = self._allow_test_overrides
        self._allow_test_overrides = bool(allow_test_overrides)
        try:
            vacuum = self.executor.snapshot()
            bindings = self.bindings
            policy = self.policy
            values: dict[str, NormalizedInput] = {}

            values["vacuum.available"] = self._finalize(
                "vacuum.available",
                vacuum.available,
                live_available=True,
                source_entity_id=vacuum.vacuum_entity_id,
                raw_value=vacuum.raw_state,
                binding_mode="derived",
            )
            values["vacuum.activity"] = self._finalize(
                "vacuum.activity",
                vacuum.normalized_state.value,
                live_available=vacuum.entity_exists,
                source_entity_id=vacuum.vacuum_entity_id,
                raw_value=vacuum.raw_state,
                binding_mode="derived",
            )

            for key in (
                "vacuum.battery_percent",
                "vacuum.charging",
                "dock.available",
                "dock.robot_docked",
                "dock.clean_water",
                "dock.dirty_water",
                "dock.detergent",
                "mop.attached",
                "dnd.active",
                "dnd.starts_at",
                "dnd.ends_at",
            ):
                values[key] = self._normalized_bound_input(key, bindings[key], vacuum, policy)

            configured_zones = self.cleaning_zones
            valid_zone_ids = {zone.zone_id for zone in configured_zones}
            self._zone_stable_since = {
                key: value for key, value in self._zone_stable_since.items() if key[0] in valid_zone_ids
            }
            zones = {
                zone.zone_id: self._zone_snapshot(zone, evaluated_at, policy)
                for zone in configured_zones
            }
            digest_payload = {
                "values": {key: [item.effective_value, item.effective_available] for key, item in values.items()},
                "zones": {
                    key: [zone.busy.effective_value, zone.accessible.effective_value]
                    for key, zone in zones.items()
                },
                "time": evaluated_at.isoformat(timespec="seconds"),
            }
            snapshot_id = hashlib.sha256(
                json.dumps(digest_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
            result = InputSnapshot(snapshot_id=snapshot_id, evaluated_at=evaluated_at, values=values, zones=zones)
            return result
        finally:
            self._allow_test_overrides = previous_override_policy

    def auto_candidate_info(self, key: str) -> dict[str, Any]:
        """Return current autodiscovery evidence even when a manual override is active."""
        vacuum = self.executor.snapshot()
        entity_id, attribute, raw, available, candidate, note = self._auto_source(key, vacuum)
        return {
            "entity_id": entity_id,
            "attribute": attribute,
            "raw": raw,
            "available": available,
            "candidate": candidate,
            "note": note,
            "normal_state": self._infer_binary_normal_state(key, entity_id)
            if key in {"dock.clean_water", "dock.dirty_water", "dock.detergent", "mop.attached"}
            else None,
        }

    def get_live_value(self, target: str) -> Any:
        """Return current live normalized value, ignoring an override on the target itself."""
        existing = self.overrides.get(target)
        if existing is None:
            item = self.snapshot().flat().get(target)
            return item.live_value if item else None
        # Temporarily remove the target only. Restore it immediately after reading.
        raw = existing.to_dict()
        self.overrides.clear(target)
        try:
            item = self.snapshot().flat().get(target)
            return item.live_value if item else None
        finally:
            self.overrides.set(
                target,
                raw["mode"],
                value=raw.get("value"),
                persistent=bool(raw.get("persistent")),
                frozen_live_value=raw.get("value"),
            )

    def zone_snapshot(self, zone_id: str, evaluated_at: datetime | None = None) -> CleaningZoneInputSnapshot | None:
        return self.snapshot(evaluated_at).zones.get(str(zone_id))

    def cleaning_zone(self, zone_id: str) -> CleaningZone | None:
        return next((zone for zone in self.cleaning_zones if zone.zone_id == str(zone_id)), None)

    def reset_zone_stability(self) -> None:
        """Forget runtime hold timers after configuration changes."""
        self._zone_stable_since.clear()


    def target_configuration_valid(self, zone: CleaningZone) -> tuple[bool, str | None]:
        """Validate a zone target without invoking physical vacuum commands.

        Segment disappearance is detectable from Home Assistant's persisted
        ``last_seen_segments`` catalogue. If no catalogue is available we keep
        the target provisionally valid instead of inventing a failure. Arbitrary
        robot-zone payloads are structurally validated by CleaningZone itself.
        """
        if zone.robot_target_id.startswith("__migration_target_required__:"):
            return False, "migration_target_required"
        if zone.robot_target_type.value != "segment":
            return True, None
        vacuum_entity_id = str(self.entry.data.get("vacuum_entity_id", ""))
        registry_entry = er.async_get(self.hass).async_get(vacuum_entity_id) if vacuum_entity_id else None
        vacuum_options = dict((getattr(registry_entry, "options", {}) or {}).get("vacuum", {})) if registry_entry else {}
        segments = list(vacuum_options.get("last_seen_segments") or [])
        if not segments:
            return True, None
        ids: set[str] = set()
        for segment in segments:
            if isinstance(segment, Mapping):
                value = segment.get("id")
            else:
                value = getattr(segment, "id", None)
            if value is not None:
                ids.add(str(value))
        if zone.robot_target_id not in ids:
            return False, "segment_not_found"
        return True, None

    def _normalized_bound_input(
        self,
        key: str,
        binding: CapabilityBinding,
        vacuum: VacuumSnapshot,
        policy: PreflightPolicy,
    ) -> NormalizedInput:
        if binding.binding_mode is BindingMode.DISABLED:
            return self._finalize(
                key,
                None,
                live_available=False,
                binding_mode=binding.binding_mode.value,
                status="disabled",
                note="disabled_by_user",
            )

        entity_id: str | None = None
        attribute: str | None = binding.attribute
        auto_candidate: str | None = None
        raw: Any = None
        available = False
        note: str | None = None

        if binding.binding_mode is BindingMode.MANUAL:
            entity_id, renamed = self._resolve_registry_entity(binding.entity_id, binding.entity_registry_id)
            raw, available = self._read_entity(entity_id, attribute)
            if not entity_id:
                note = "manual_source_missing"
            elif renamed:
                note = "entity_id_resolved_from_registry"
        else:
            entity_id, attribute, raw, available, auto_candidate, note = self._auto_source(key, vacuum)

        effective_binding = binding
        if key in {"dock.clean_water", "dock.dirty_water", "dock.detergent", "mop.attached"} and not binding.normal_state:
            inferred = self._infer_binary_normal_state(key, entity_id)
            if inferred:
                effective_binding = CapabilityBinding.from_dict(
                    key, {**binding.to_dict(), "normal_state": inferred}
                )
        normalized, semantic_ok = self._normalize_value(key, raw, available, effective_binding, policy, vacuum)
        status = "ready" if semantic_ok and (available or key in {"dock.available", "dnd.starts_at", "dnd.ends_at"}) else "requires_attention"
        return self._finalize(
            key,
            normalized,
            live_available=available if key != "dock.available" else normalized is not None,
            source_entity_id=entity_id,
            source_attribute=attribute,
            raw_value=raw,
            binding_mode=binding.binding_mode.value,
            auto_candidate=auto_candidate,
            status=status,
            note=note if semantic_ok else (note or "ambiguous_semantics"),
        )

    def _auto_source(
        self, key: str, vacuum: VacuumSnapshot
    ) -> tuple[str | None, str | None, Any, bool, str | None, str | None]:
        vacuum_id = vacuum.vacuum_entity_id
        attrs = vacuum.raw_attributes

        if key == "vacuum.battery_percent":
            # Prefer the dedicated Home Assistant battery percentage entity.
            # Legacy battery attributes on vacuum.* are only a compatibility fallback.
            candidate = self._battery_candidate(vacuum)
            if candidate:
                raw, available = self._read_entity(candidate, None)
                return candidate, None, raw, available, candidate, "battery_sensor_entity"
            for attr in ("battery_level", "battery_percent", "battery"):
                if attr in attrs:
                    return (
                        vacuum_id,
                        attr,
                        attrs.get(attr),
                        vacuum.available,
                        f"{vacuum_id}:{attr}",
                        "legacy_vacuum_battery_attribute",
                    )
            return None, None, None, False, None, "auto_source_not_found"

        if key == "vacuum.charging":
            for attr in ("charging", "is_charging"):
                if attr in attrs:
                    return vacuum_id, attr, attrs.get(attr), vacuum.available, f"{vacuum_id}:{attr}", None
            # Docked means connected to the charging/dock station; no ETA is inferred.
            value = vacuum.normalized_state is NormalizedVacuumState.DOCKED
            return vacuum_id, None, value, vacuum.available, f"{vacuum_id}:state", "derived_from_vacuum_state"

        if key == "dock.available":
            candidate = self._first_existing(vacuum.related_entities.dock)
            if candidate:
                state = self.hass.states.get(candidate)
                available = state is not None and state.state not in _UNAVAILABLE_STATES
                return candidate, None, available, True, candidate, None
            return None, None, None, False, None, "dock_source_not_found"

        if key == "dock.robot_docked":
            value = vacuum.normalized_state is NormalizedVacuumState.DOCKED
            return vacuum_id, None, value, vacuum.available, f"{vacuum_id}:state", "derived_from_vacuum_state"

        if key in {"dock.clean_water", "dock.dirty_water"}:
            explicit = (
                vacuum.dock_water.clean_water.entity_id
                if key.endswith("clean_water")
                else vacuum.dock_water.dirty_water.entity_id
            )
            if explicit and not self._is_binary_entity(explicit):
                explicit = None
            candidate = explicit or self._water_candidate(vacuum, clean=key.endswith("clean_water"))
            raw, available = self._read_entity(candidate, None)
            return candidate, None, raw, available, candidate, None if candidate else "binary_water_source_not_found"

        if key == "dock.detergent":
            candidate = self._detergent_candidate(vacuum)
            raw, available = self._read_entity(candidate, None)
            return candidate, None, raw, available, candidate, None if candidate else "binary_detergent_source_not_found"

        if key == "mop.attached":
            candidate = self._binary_related_candidate(vacuum.related_entities.all_entity_ids, positive_tokens=("mop", "attached", "installed", "present"), required_tokens=("mop",))
            raw, available = self._read_entity(candidate, None)
            return candidate, None, raw, available, candidate, None if candidate else "binary_mop_source_not_found"

        if key in {"dnd.active", "dnd.starts_at", "dnd.ends_at"}:
            # Home Assistant integrations commonly expose DND as three separate
            # entities: an enable switch plus begin/end time entities.  The switch
            # means whether the schedule is used at all; it is NOT an indication
            # that the current time is inside the DND window.
            if key == "dnd.active":
                role = "enabled"
                attr_names = (
                    "dnd_enabled", "do_not_disturb_enabled", "dnd",
                    "do_not_disturb", "dnd_active", "do_not_disturb_active",
                )
            elif key == "dnd.starts_at":
                role = "start"
                attr_names = (
                    "dnd_starts_at", "dnd_start", "dnd_begin", "dnd_start_time",
                    "do_not_disturb_start", "do_not_disturb_begin",
                )
            else:
                role = "end"
                attr_names = (
                    "dnd_ends_at", "dnd_end", "dnd_end_time",
                    "do_not_disturb_end",
                )
            for attr in attr_names:
                if attr in attrs:
                    return vacuum_id, attr, attrs.get(attr), vacuum.available, f"{vacuum_id}:{attr}", None

            candidate = self._dnd_candidate(vacuum, role)
            if candidate:
                raw, available = self._read_entity(candidate, None)
                return candidate, None, raw, available, candidate, None
            return None, None, None, False, None, f"dnd_{role}_source_not_found"

        return None, None, None, False, None, "auto_source_not_found"

    def _resolve_registry_entity(
        self, entity_id: str | None, registry_id: str | None
    ) -> tuple[str | None, bool]:
        """Resolve a saved registry entry to its current entity_id after user renames."""
        if registry_id:
            registry = er.async_get(self.hass)
            entities = getattr(registry, "entities", {})
            for candidate in getattr(entities, "values", lambda: ())():
                if str(getattr(candidate, "id", "")) == str(registry_id):
                    current = str(getattr(candidate, "entity_id", "") or "")
                    if current:
                        return current, bool(entity_id and current != entity_id)
        return entity_id, False

    def _resolve_condition_entity(self, condition: Any) -> tuple[str | None, bool]:
        return self._resolve_registry_entity(
            getattr(condition, "entity_id", None), getattr(condition, "entity_registry_id", None)
        )

    def _read_entity(self, entity_id: str | None, attribute: str | None) -> tuple[Any, bool]:
        if not entity_id:
            return None, False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE_STATES:
            return None, False
        if attribute:
            return state.attributes.get(attribute), attribute in state.attributes
        return state.state, True

    def _first_existing(self, entity_ids: tuple[str, ...] | list[str]) -> str | None:
        for entity_id in entity_ids:
            if self.hass.states.get(entity_id) is not None:
                return entity_id
        return str(entity_ids[0]) if entity_ids else None

    def _battery_candidate(self, vacuum: VacuumSnapshot) -> str | None:
        """Return the best dedicated percentage battery entity for the selected robot."""
        registry = er.async_get(self.hass)
        vacuum_entry = registry.async_get(vacuum.vacuum_entity_id) if vacuum.vacuum_entity_id else None
        primary_device_id = getattr(vacuum_entry, "device_id", None)
        ranked: list[tuple[int, str]] = []
        for entity_id in vacuum.related_entities.battery:
            state = self.hass.states.get(entity_id)
            entry = registry.async_get(entity_id)
            domain = entity_id.split(".", 1)[0]
            device_class = str(
                (state.attributes.get("device_class") if state else None)
                or getattr(entry, "original_device_class", None)
                or getattr(entry, "device_class", None)
                or ""
            ).lower()
            unit = str(state.attributes.get("unit_of_measurement", "") if state else "").strip().lower()
            tokens = " ".join(
                str(value or "")
                for value in (
                    entity_id,
                    getattr(entry, "unique_id", None),
                    getattr(entry, "translation_key", None),
                    getattr(entry, "original_name", None),
                    getattr(state, "name", None),
                )
            ).lower()
            score = 0
            if primary_device_id and getattr(entry, "device_id", None) == primary_device_id:
                score += 100
            if device_class == "battery":
                score += 80
            if domain == "sensor":
                score += 40
            elif domain == "number":
                score += 30
            if unit in {"%", "percent", "percentage"}:
                score += 30
            if "battery" in tokens:
                score += 20
            if state is not None and state.state not in _UNAVAILABLE_STATES:
                score += 10
                try:
                    if 0.0 <= float(state.state) <= 100.0:
                        score += 15
                except (TypeError, ValueError):
                    pass
            ranked.append((score, entity_id))
        if not ranked:
            return None
        return max(ranked, key=lambda item: (item[0], item[1]))[1]

    def _is_binary_entity(self, entity_id: str | None) -> bool:
        if not entity_id:
            return False
        domain = entity_id.split(".", 1)[0]
        if domain in {"binary_sensor", "switch", "input_boolean"}:
            return True
        state = self.hass.states.get(entity_id)
        return state is not None and _boolean(state.state) is not None and domain not in {"sensor", "number"}

    def _binary_related_candidate(self, entity_ids: tuple[str, ...] | list[str], *, positive_tokens: tuple[str, ...] = (), required_tokens: tuple[str, ...] = ()) -> str | None:
        ranked: list[tuple[int, str]] = []
        registry = er.async_get(self.hass)
        for entity_id in entity_ids:
            if not self._is_binary_entity(entity_id):
                continue
            state = self.hass.states.get(entity_id)
            reg = registry.async_get(entity_id)
            tokens = " ".join(str(value or "") for value in (entity_id, getattr(reg, "unique_id", None), getattr(reg, "translation_key", None), getattr(reg, "original_name", None), getattr(state, "name", None))).lower()
            normalized = tokens.replace("-", "_").replace(" ", "_")
            if required_tokens and not any(token in normalized for token in required_tokens):
                continue
            score = 20 + sum(8 for token in positive_tokens if token in normalized)
            if state is not None:
                score += 3
            ranked.append((score, entity_id))
        return max(ranked, key=lambda item: (item[0], item[1]))[1] if ranked else None

    def _water_candidate(self, vacuum: VacuumSnapshot, *, clean: bool) -> str | None:
        registry = er.async_get(self.hass)
        wanted = "clean" if clean else "dirty"
        ranked: list[tuple[int, str]] = []
        for entity_id in vacuum.related_entities.all_entity_ids:
            if not self._is_binary_entity(entity_id):
                continue
            state = self.hass.states.get(entity_id)
            reg = registry.async_get(entity_id)
            tokens = " ".join(str(value or "") for value in (entity_id, getattr(reg, "unique_id", None), getattr(reg, "translation_key", None), getattr(reg, "original_name", None), getattr(state, "name", None))).lower()
            normalized = tokens.replace("-", "_").replace(" ", "_")
            if clean:
                matches = any(token in normalized for token in ("clean_water", "fresh_water", "cleanwatertank", "freshwatertank"))
            else:
                matches = any(token in normalized for token in ("dirty_water", "waste_water", "dirtywatertank", "wastewatertank"))
            if not matches:
                continue
            score = 50
            if "tank" in normalized:
                score += 10
            ranked.append((score, entity_id))
        return max(ranked, key=lambda item: (item[0], item[1]))[1] if ranked else None

    def _detergent_candidate(self, vacuum: VacuumSnapshot) -> str | None:
        tokens = ("detergent", "cleaning_solution", "cleaning_fluid", "clean_fluid", "cleaner", "solution", "detergent_tank")
        return self._binary_related_candidate(
            vacuum.related_entities.all_entity_ids, positive_tokens=tokens, required_tokens=tokens
        )

    def _infer_binary_normal_state(self, key: str, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        reg = er.async_get(self.hass).async_get(entity_id)
        tokens = " ".join(str(value or "") for value in (entity_id, getattr(reg, "unique_id", None), getattr(reg, "translation_key", None), getattr(reg, "original_name", None), getattr(state, "name", None))).lower().replace("-", "_").replace(" ", "_")
        # Home Assistant BinarySensorDeviceClass.PROBLEM has canonical semantics:
        # on = problem, off = normal. Roborock exposes clean-water-empty,
        # dirty-water-full and cleaning-fluid-empty this way.
        live_device_class = str(state.attributes.get("device_class", "") if state else "").lower()
        registry_device_class = str(
            getattr(reg, "original_device_class", None)
            or getattr(reg, "device_class", None)
            or ""
        ).lower()
        if live_device_class == "problem" or registry_device_class == "problem":
            return "off"
        bad_tokens = ("empty", "low", "missing", "absent", "shortage", "insufficient", "refill", "detached")
        if key == "dock.dirty_water":
            bad_tokens = (*bad_tokens, "full", "high")
        if any(token in tokens for token in bad_tokens):
            return "off"
        if key == "mop.attached" and any(token in tokens for token in ("attached", "installed", "present")):
            return "on"
        if key == "dock.detergent" and any(token in tokens for token in ("present", "available", "installed")):
            return "on"
        if key == "dock.clean_water" and any(token in tokens for token in ("present", "available", "installed", "ready")):
            return "on"
        return None

    def _dnd_candidate(self, vacuum: VacuumSnapshot, role: str = "enabled") -> str | None:
        """Find the DND enable/start/end entity with role-aware scoring.

        Roborock and other integrations may place all three entities on the same
        device.  A generic first-match search can therefore mistake the DND switch
        for the begin/end time (or vice versa), so each role is ranked separately.
        """
        ranked: list[tuple[int, str]] = []
        for entity_id in vacuum.related_entities.all_entity_ids:
            state = self.hass.states.get(entity_id)
            label = f"{entity_id} {state.name if state else ''}".lower()
            normalized = label.replace("-", "_").replace(" ", "_").replace(".", "_")
            if not any(token in normalized for token in ("dnd", "do_not_disturb", "donotdisturb", "quiet_time")):
                continue
            domain = entity_id.split(".", 1)[0]
            has_start = any(token in normalized for token in ("_begin", "_start", "starts_at", "start_time"))
            has_end = any(token in normalized for token in ("_end", "ends_at", "end_time"))
            score = 10
            if role == "enabled":
                if has_start or has_end:
                    score -= 12
                if domain in {"switch", "binary_sensor", "input_boolean"}:
                    score += 10
                if state is not None and _boolean(state.state) is not None:
                    score += 4
            elif role == "start":
                score += 14 if has_start else -8
                score -= 8 if has_end else 0
                if domain in {"time", "input_datetime", "sensor"}:
                    score += 6
            else:
                score += 14 if has_end else -8
                score -= 8 if has_start else 0
                if domain in {"time", "input_datetime", "sensor"}:
                    score += 6
            ranked.append((score, entity_id))
        if not ranked:
            return None
        score, entity_id = max(ranked, key=lambda item: (item[0], item[1]))
        return entity_id if score > 0 else None

    def _normalize_value(
        self,
        key: str,
        raw: Any,
        available: bool,
        binding: CapabilityBinding,
        policy: PreflightPolicy,
        vacuum: VacuumSnapshot,
    ) -> tuple[Any, bool]:
        if not available:
            return None, True
        mapped = self._mapped(raw, binding)
        if mapped is not None:
            raw = mapped

        if key == "vacuum.battery_percent":
            number = _number(raw)
            return (number, number is not None)
        if key in {"vacuum.charging", "dock.robot_docked", "dnd.active"}:
            value = _boolean(raw)
            return value, value is not None
        if key in {"dock.clean_water", "dock.dirty_water", "dock.detergent", "mop.attached"}:
            value = _boolean(raw)
            if value is None or binding.normal_state not in {"on", "off"}:
                return value, False
            expected = binding.normal_state == "on"
            return value is expected, True
        if key == "dock.available":
            return bool(raw), True
        if key in {"dnd.starts_at", "dnd.ends_at"}:
            value = _time_text(raw)
            return value, value is not None or raw in (None, "")
        return raw, True

    @staticmethod
    def _mapped(raw: Any, binding: CapabilityBinding) -> Any | None:
        if not binding.value_mapping:
            return None
        text = str(raw)
        if text in binding.value_mapping:
            return binding.value_mapping[text]
        lowered = text.lower()
        for candidate, value in binding.value_mapping.items():
            if str(candidate).lower() == lowered:
                return value
        return None

    def _finalize(
        self,
        key: str,
        live_value: Any,
        *,
        live_available: bool,
        source_entity_id: str | None = None,
        source_attribute: str | None = None,
        raw_value: Any = None,
        binding_mode: str = "auto",
        auto_candidate: str | None = None,
        status: str = "ready",
        note: str | None = None,
    ) -> NormalizedInput:
        if self._allow_test_overrides:
            effective, effective_available, override = self.overrides.apply(
                key, live_value, live_available=live_available
            )
        else:
            effective, effective_available, override = live_value, live_available, None
        return NormalizedInput(
            key=key,
            live_value=live_value,
            effective_value=effective,
            live_available=live_available,
            effective_available=effective_available,
            source_entity_id=source_entity_id,
            source_attribute=source_attribute,
            raw_value=raw_value,
            binding_mode=binding_mode,
            auto_candidate=auto_candidate,
            override=override,
            status=status,
            note=note,
        )

    def _zone_snapshot(
        self, zone: CleaningZone, evaluated_at: datetime, policy: PreflightPolicy
    ) -> CleaningZoneInputSnapshot:
        busy = False
        busy_unknown = False
        source_details: list[dict[str, Any]] = []
        free_since_candidates: list[datetime] = []

        for condition in zone.busy_sources:
            resolved_entity_id, renamed = self._resolve_condition_entity(condition)
            state = self.hass.states.get(resolved_entity_id) if resolved_entity_id else None
            source_available = state is not None and state.state not in _UNAVAILABLE_STATES
            raw = None
            if source_available and state is not None:
                raw = state.attributes.get(condition.attribute) if condition.attribute else state.state
                if condition.attribute and condition.attribute not in state.attributes:
                    source_available = False
            matched = condition_matches(raw, condition) if source_available else False
            changed_at = _condition_timestamp(state, condition.attribute) if state is not None else None
            source_details.append({
                "kind": "busy",
                "entity_id": resolved_entity_id or condition.entity_id,
                "configured_entity_id": condition.entity_id,
                "entity_registry_id": condition.entity_registry_id,
                "entity_renamed": renamed,
                "attribute": condition.attribute,
                "operator": condition.operator.value,
                "expected": condition.value,
                "raw": raw,
                "available": source_available,
                "matched": matched,
                "changed_at": changed_at.isoformat() if changed_at else None,
            })
            if not source_available:
                busy_unknown = True
                busy = True
            elif matched:
                busy = True
            elif changed_at is not None:
                free_since_candidates.append(changed_at)

        path_details: list[dict[str, Any]] = []
        access_path_results: list[tuple[bool, bool]] = []
        open_path_since_candidates: list[datetime] = []
        if zone.access_paths:
            for path in zone.access_paths:
                condition_results: list[bool] = []
                path_has_unknown = False
                path_condition_times: list[datetime] = []
                for condition in path.conditions:
                    resolved_entity_id, renamed = self._resolve_condition_entity(condition)
                    state = self.hass.states.get(resolved_entity_id) if resolved_entity_id else None
                    source_available = state is not None and state.state not in _UNAVAILABLE_STATES
                    raw = None
                    if source_available and state is not None:
                        raw = state.attributes.get(condition.attribute) if condition.attribute else state.state
                        if condition.attribute and condition.attribute not in state.attributes:
                            source_available = False
                    if not source_available:
                        path_has_unknown = True
                    matched = condition_matches(raw, condition) if source_available else False
                    condition_results.append(source_available and matched)
                    changed_at = _condition_timestamp(state, condition.attribute) if state is not None else None
                    if source_available and matched and changed_at is not None:
                        path_condition_times.append(changed_at)
                    path_details.append({
                        "path_id": path.path_id,
                        "path_name": path.name,
                        "entity_id": resolved_entity_id or condition.entity_id,
                        "configured_entity_id": condition.entity_id,
                        "entity_registry_id": condition.entity_registry_id,
                        "entity_renamed": renamed,
                        "attribute": condition.attribute,
                        "operator": condition.operator.value,
                        "expected": condition.value,
                        "raw": raw,
                        "available": source_available,
                        "matched": matched,
                        "changed_at": changed_at.isoformat() if changed_at else None,
                    })
                path_open = bool(condition_results) and all(condition_results)
                path_known = bool(condition_results) and not path_has_unknown
                access_path_results.append((path_open, path_known))
                if path_open and len(path_condition_times) == len(condition_results):
                    open_path_since_candidates.append(max(path_condition_times, key=as_utc))
        accessible, access_known = aggregate_access_paths(access_path_results)

        busy_input = self._finalize(
            f"zone.{zone.zone_id}.busy", busy, live_available=not busy_unknown,
            raw_value=busy, binding_mode="cleaning_zone",
            note="source_unavailable" if busy_unknown else None,
        )
        accessible_input = self._finalize(
            f"zone.{zone.zone_id}.accessible", accessible, live_available=access_known,
            raw_value=accessible, binding_mode="cleaning_zone",
            note="source_unavailable" if not access_known else None,
        )

        occupancy_delay = effective_zone_delay(
            zone.occupancy_clear_delay_seconds, policy.occupancy_clear_delay_seconds
        )
        access_delay = effective_zone_delay(
            zone.access_stable_delay_seconds, policy.access_stable_delay_seconds
        )
        free_seed = (
            max(free_since_candidates, key=as_utc)
            if zone.busy_sources and len(free_since_candidates) == len(zone.busy_sources)
            else None
        )
        access_seed = (
            min(open_path_since_candidates, key=as_utc)
            if accessible and open_path_since_candidates
            else None
        )
        busy_input = self._stabilize_zone_input(
            zone.zone_id, "occupancy", busy_input, good_value=False, delay_seconds=occupancy_delay,
            now=evaluated_at, seed_since=free_seed, configured=bool(zone.busy_sources),
        )
        accessible_input = self._stabilize_zone_input(
            zone.zone_id, "access", accessible_input, good_value=True, delay_seconds=access_delay,
            now=evaluated_at, seed_since=access_seed, configured=bool(zone.access_paths),
        )
        return CleaningZoneInputSnapshot(
            zone_id=zone.zone_id,
            name=zone.name,
            robot_target_type=zone.robot_target_type.value,
            robot_target_id=zone.robot_target_id,
            control=zone.control.value,
            disabled_until=zone.disabled_until,
            busy=busy_input,
            accessible=accessible_input,
            path_details=tuple(path_details),
            source_details=tuple(source_details),
        )

    def _stabilize_zone_input(
        self, zone_id: str, kind: str, item: NormalizedInput, *, good_value: bool,
        delay_seconds: int, now: datetime, seed_since: datetime | None, configured: bool,
    ) -> NormalizedInput:
        """Require one zone condition to remain good continuously for a delay."""
        key = (zone_id, kind)
        delay_seconds = max(0, int(delay_seconds))
        if not configured or delay_seconds <= 0:
            self._zone_stable_since.pop(key, None)
            return replace(item, configured_delay_seconds=delay_seconds)

        is_good = item.effective_available and item.effective_value is good_value
        if not is_good:
            self._zone_stable_since.pop(key, None)
            return replace(item, configured_delay_seconds=delay_seconds)

        stable_since = self._zone_stable_since.get(key)
        if stable_since is None:
            # Test overrides are synthetic observations: their hold starts now.
            candidate = None if item.override is not None else seed_since
            stable_since = _clamp_since(candidate, now)
            self._zone_stable_since[key] = stable_since
        until = instant_add(stable_since, timedelta(seconds=delay_seconds))
        if instant_le(until, now):
            return replace(
                item, stabilizing=False, stabilizing_since=stable_since,
                stabilizing_until=None, configured_delay_seconds=delay_seconds,
            )

        # Keep the pre-flight result blocking until the continuous-good interval
        # completes. For occupancy the blocking value is True; for accessibility
        # it is False. Live/raw values still expose the sensor's immediate state.
        return replace(
            item, effective_value=not good_value, status="settling", note="stability_delay",
            stabilizing=True, stabilizing_since=stable_since, stabilizing_until=until,
            configured_delay_seconds=delay_seconds,
        )


def _condition_timestamp(state: State | None, attribute: str | None) -> datetime | None:
    if state is None:
        return None
    value = state.last_updated if attribute else state.last_changed
    return value if isinstance(value, datetime) else None


def _clamp_since(candidate: datetime | None, now: datetime) -> datetime:
    if candidate is None:
        return now
    if candidate.tzinfo is None and now.tzinfo is not None:
        candidate = candidate.replace(tzinfo=now.tzinfo)
    elif candidate.tzinfo is not None and now.tzinfo is not None:
        candidate = candidate.astimezone(now.tzinfo)
    return candidate if instant_le(candidate, now) else now


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "on", "yes", "active", "charging", "docked", "attached", "present", "full"}:
        return True
    if text in {"0", "false", "off", "no", "inactive", "idle", "detached", "absent", "empty"}:
        return False
    return None


def _time_text(value: Any) -> str | None:
    """Normalize a Home Assistant time or datetime entity to local clock text."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None).isoformat(timespec="seconds")
    if isinstance(value, time):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    text = str(value).strip()
    try:
        parsed_time = time.fromisoformat(text)
        return parsed_time.replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        pass
    try:
        parsed_dt = datetime.fromisoformat(text)
        return parsed_dt.timetz().replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        return None
