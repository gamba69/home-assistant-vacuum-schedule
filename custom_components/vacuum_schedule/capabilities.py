"""Generic Home Assistant capability discovery for Vacuum Schedule."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .models import (
    CapabilityReport,
    DockWaterBindings,
    CapabilitySupport,
    NormalizedVacuumState,
    RelatedDeviceSet,
    RelatedEntitySet,
    RoomMappingInfo,
    StatusEntityBinding,
    VacuumSnapshot,
)

_STANDARD_FEATURES: dict[str, VacuumEntityFeature] = {
    "start": VacuumEntityFeature.START,
    "stop": VacuumEntityFeature.STOP,
    "pause": VacuumEntityFeature.PAUSE,
    "return_home": VacuumEntityFeature.RETURN_HOME,
    "fan_mode": VacuumEntityFeature.FAN_SPEED,
    "room_cleaning": VacuumEntityFeature.CLEAN_AREA,
    "spot_cleaning": VacuumEntityFeature.CLEAN_SPOT,
    "map": VacuumEntityFeature.MAP,
    "locate": VacuumEntityFeature.LOCATE,
    "send_command": VacuumEntityFeature.SEND_COMMAND,
    "robot_state": VacuumEntityFeature.STATE,
}

_EXTENDED_CAPABILITIES = (
    "zone_cleaning",
    "mop_water_mode",
    "repetitions",
)

_STATE_MAP: dict[str, NormalizedVacuumState] = {
    "cleaning": NormalizedVacuumState.CLEANING,
    "docked": NormalizedVacuumState.DOCKED,
    "idle": NormalizedVacuumState.IDLE,
    "paused": NormalizedVacuumState.PAUSED,
    "returning": NormalizedVacuumState.RETURNING,
    "error": NormalizedVacuumState.ERROR,
}

_BATTERY_TOKENS = ("battery", "battery_level", "battery_percent", "battery_percentage")
_DOCK_TOKENS = ("dock", "station", "base")
_WATER_TOKENS = ("water", "tank", "reservoir")
_MOP_TOKENS = ("mop", "mopping")
_CONSUMABLE_TOKENS = (
    "filter",
    "brush",
    "consumable",
    "lifespan",
    "life_left",
    "sensor_dirty",
    "detergent",
    "cleaning_solution",
    "cleaning_fluid",
    "clean_fluid",
    "cleaner",
)

_MOP_WATER_CONTROL_TOKENS = (
    "mop_mode",
    "mop_intensity",
    "water_box_mode",
    "water_flow",
    "cleaning_mode",
    "cleaning_route",
)
_MAP_ENTITY_TOKENS = ("map", "карта")

_ROBOROCK_DOMAIN = "roborock"
_ROBOROCK_ZONED_CLEAN_SERVICE = "set_vacuum_zoned_cleaning"


def normalize_vacuum_state(state: State | None) -> NormalizedVacuumState:
    """Normalize a Home Assistant vacuum state without vendor-specific assumptions."""
    if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
        return NormalizedVacuumState.UNAVAILABLE
    return _STATE_MAP.get(state.state.lower(), NormalizedVacuumState.UNKNOWN)


def _support_from_flag(features: int, flag: VacuumEntityFeature) -> CapabilitySupport:
    return (
        CapabilitySupport.SUPPORTED
        if features & int(flag)
        else CapabilitySupport.UNSUPPORTED
    )


def _base_capabilities(state: State | None) -> CapabilityReport:
    """Discover capabilities represented by the standard vacuum feature flags."""
    if state is None:
        return CapabilityReport(
            capabilities={
                **{name: CapabilitySupport.UNKNOWN for name in _STANDARD_FEATURES},
                **{name: CapabilitySupport.UNKNOWN for name in _EXTENDED_CAPABILITIES},
            }
        )

    raw_features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    try:
        features = int(raw_features or 0)
    except (TypeError, ValueError):
        features = 0

    capabilities = {
        name: _support_from_flag(features, flag)
        for name, flag in _STANDARD_FEATURES.items()
    }
    capabilities.update(
        {name: CapabilitySupport.UNKNOWN for name in _EXTENDED_CAPABILITIES}
    )

    fan_speed_list = state.attributes.get("fan_speed_list", ())
    if not isinstance(fan_speed_list, (list, tuple)):
        fan_speed_list = ()

    sources = {
        name: (f"vacuum.supported_features:{flag.name}",)
        for name, flag in _STANDARD_FEATURES.items()
        if capabilities[name] is CapabilitySupport.SUPPORTED
    }

    return CapabilityReport(
        capabilities=capabilities,
        supported_features_raw=features,
        fan_speed_list=tuple(str(item) for item in fan_speed_list),
        sources=sources,
    )


def discover_capabilities(state: State | None) -> CapabilityReport:
    """Discover capabilities available from the vacuum entity alone."""
    return _base_capabilities(state)


def _tokens_for_entity(entity_id: str, state: State | None) -> str:
    chunks = [entity_id]
    if state is not None:
        chunks.append(str(state.name or ""))
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        if device_class:
            chunks.append(str(device_class))
    return " ".join(chunks).lower().replace("-", "_")


def _registry_tokens(entry: Any, state: State | None) -> str:
    """Build stable capability-probe tokens from registry metadata and live state."""
    chunks = [getattr(entry, "entity_id", "")]
    for attr in ("unique_id", "translation_key", "original_name"):
        value = getattr(entry, attr, None)
        if value:
            chunks.append(str(value))
    if state is not None:
        chunks.append(str(state.name or ""))
    return " ".join(chunks).lower().replace("-", "_").replace(" ", "_")


def _matches(tokens: str, candidates: Iterable[str]) -> bool:
    return any(candidate in tokens for candidate in candidates)


def _numeric_percent(value: Any) -> bool:
    """Return whether a raw state can represent a battery percentage."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return 0.0 <= number <= 100.0


def _is_battery_percentage_entity(entity_id: str, state: State | None, entry: Any = None) -> bool:
    """Identify a percentage battery entity, excluding charging/low-battery booleans."""
    domain = entity_id.split(".", 1)[0]
    if domain not in {"sensor", "number"}:
        return False

    live_device_class = str(state.attributes.get(ATTR_DEVICE_CLASS, "")).lower() if state else ""
    registry_device_class = str(
        getattr(entry, "original_device_class", None)
        or getattr(entry, "device_class", None)
        or ""
    ).lower()
    device_class = live_device_class or registry_device_class
    if device_class == "battery":
        return True

    tokens = " ".join(
        part
        for part in (
            _tokens_for_entity(entity_id, state),
            _registry_tokens(entry, state) if entry is not None else "",
        )
        if part
    )
    if not _matches(tokens, _BATTERY_TOKENS):
        return False

    unit = str(state.attributes.get("unit_of_measurement", "")).strip().lower() if state else ""
    if unit in {"%", "percent", "percentage"}:
        return True
    # Some integrations omit device_class/unit but still expose an obvious numeric
    # sensor.<robot>_battery. Only accept that fallback when no conflicting unit exists.
    return not unit and state is not None and _numeric_percent(state.state)


def _classify_related_entity(entity_id: str, state: State | None, entry: Any = None) -> str:
    tokens = _tokens_for_entity(entity_id, state)

    if _is_battery_percentage_entity(entity_id, state, entry):
        return "battery"
    if _matches(tokens, _DOCK_TOKENS):
        return "dock"
    if _matches(tokens, _WATER_TOKENS):
        return "water"
    if _matches(tokens, _MOP_TOKENS):
        return "mop"
    if _matches(tokens, _CONSUMABLE_TOKENS):
        return "consumables"
    return "diagnostics"


def _primary_registry_entry(hass: HomeAssistant, vacuum_entity_id: str) -> Any:
    """Return the primary entity-registry entry, if registered."""
    return er.async_get(hass).async_get(vacuum_entity_id)


def discover_related_devices(
    hass: HomeAssistant,
    vacuum_entity_id: str | None,
    configured_related_device_ids: Iterable[str] = (),
) -> RelatedDeviceSet:
    """Resolve the primary and explicitly associated Home Assistant devices."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    primary_device_id: str | None = None
    if vacuum_entity_id:
        source_entry = entity_registry.async_get(vacuum_entity_id)
        if source_entry is not None:
            primary_device_id = source_entry.device_id

    configured = tuple(
        sorted(
            {
                str(device_id)
                for device_id in configured_related_device_ids
                if device_id and str(device_id) != primary_device_id
            }
        )
    )
    existing = tuple(
        device_id for device_id in configured if device_registry.async_get(device_id) is not None
    )
    missing = tuple(device_id for device_id in configured if device_id not in existing)

    return RelatedDeviceSet(
        primary=primary_device_id,
        configured=configured,
        existing=existing,
        missing=missing,
    )


def _entries_for_devices(
    hass: HomeAssistant,
    device_ids: Iterable[str],
    *,
    include_disabled_entities: bool,
) -> tuple[Any, ...]:
    """Return entity-registry entries belonging to all supplied devices."""
    registry = er.async_get(hass)
    entries: dict[str, Any] = {}
    for device_id in device_ids:
        for entry in er.async_entries_for_device(
            registry,
            device_id,
            include_disabled_entities=include_disabled_entities,
        ):
            entries[entry.entity_id] = entry
    return tuple(entries.values())


def _probe_entries(
    hass: HomeAssistant,
    vacuum_entity_id: str,
    related_devices: RelatedDeviceSet,
    manual_related_entities: Iterable[str],
) -> tuple[Any, ...]:
    """Build the complete registry evidence set for capability probing."""
    registry = er.async_get(hass)
    entries: dict[str, Any] = {
        entry.entity_id: entry
        for entry in _entries_for_devices(
            hass,
            related_devices.all_existing_device_ids,
            include_disabled_entities=True,
        )
        if entry.entity_id != vacuum_entity_id
    }
    for entity_id in manual_related_entities:
        entry = registry.async_get(str(entity_id))
        if entry is not None and entry.entity_id != vacuum_entity_id:
            entries[entry.entity_id] = entry
    return tuple(entries.values())


def _extended_capability_evidence(
    hass: HomeAssistant,
    vacuum_entity_id: str,
    base: CapabilityReport,
    related_devices: RelatedDeviceSet,
    manual_related_entities: Iterable[str],
) -> tuple[dict[str, CapabilitySupport], dict[str, tuple[str, ...]]]:
    """Resolve non-standard capabilities using read-only HA metadata and services."""
    capabilities = dict(base.capabilities)
    sources = dict(base.sources)
    source_entry = _primary_registry_entry(hass, vacuum_entity_id)
    probe_entries = _probe_entries(
        hass,
        vacuum_entity_id,
        related_devices,
        manual_related_entities,
    )

    mop_water_controls: list[str] = []
    map_entities: list[str] = []
    for entry in probe_entries:
        entity_id = entry.entity_id
        tokens = _registry_tokens(entry, hass.states.get(entity_id))
        if entity_id.startswith(("select.", "number.", "input_select.")) and _matches(
            tokens, _MOP_WATER_CONTROL_TOKENS
        ):
            mop_water_controls.append(entity_id)
        if entity_id.startswith(("image.", "camera.")) and _matches(
            tokens, _MAP_ENTITY_TOKENS
        ):
            map_entities.append(entity_id)

    if mop_water_controls:
        capabilities["mop_water_mode"] = CapabilitySupport.SUPPORTED
        sources["mop_water_mode"] = tuple(sorted(mop_water_controls))

    if map_entities and capabilities["map"] is not CapabilitySupport.SUPPORTED:
        capabilities["map"] = CapabilitySupport.SUPPORTED
        sources["map"] = tuple(sorted(map_entities))

    source_platform = getattr(source_entry, "platform", None)
    roborock_zoned_service = (
        source_platform == _ROBOROCK_DOMAIN
        and hass.services.has_service(
            _ROBOROCK_DOMAIN, _ROBOROCK_ZONED_CLEAN_SERVICE
        )
    )

    if (
        roborock_zoned_service
        and capabilities.get("spot_cleaning") is CapabilitySupport.SUPPORTED
    ):
        capabilities["zone_cleaning"] = CapabilitySupport.SUPPORTED
        sources["zone_cleaning"] = (
            f"{_ROBOROCK_DOMAIN}.{_ROBOROCK_ZONED_CLEAN_SERVICE}",
        )
        capabilities["repetitions"] = CapabilitySupport.SUPPORTED
        sources["repetitions"] = (
            f"{_ROBOROCK_DOMAIN}.{_ROBOROCK_ZONED_CLEAN_SERVICE}:repeats",
        )

    return capabilities, sources


def discover_related_entities(
    hass: HomeAssistant,
    vacuum_entity_id: str,
    related_devices: RelatedDeviceSet,
    manual_related_entities: Iterable[str] = (),
) -> RelatedEntitySet:
    """Discover entities from primary/related devices plus manual fallback entities."""
    buckets: dict[str, set[str]] = {
        "battery": set(),
        "dock": set(),
        "water": set(),
        "mop": set(),
        "consumables": set(),
        "diagnostics": set(),
    }

    for entry in _entries_for_devices(
        hass,
        related_devices.all_existing_device_ids,
        include_disabled_entities=False,
    ):
        if entry.entity_id == vacuum_entity_id:
            continue
        category = _classify_related_entity(
            entry.entity_id, hass.states.get(entry.entity_id), entry
        )
        buckets[category].add(entry.entity_id)

    manual = tuple(sorted({str(entity_id) for entity_id in manual_related_entities}))
    for entity_id in manual:
        if entity_id == vacuum_entity_id:
            continue
        category = _classify_related_entity(
            entity_id, hass.states.get(entity_id), er.async_get(hass).async_get(entity_id)
        )
        buckets[category].add(entity_id)

    return RelatedEntitySet(
        manual=manual,
        battery=tuple(sorted(buckets["battery"])),
        dock=tuple(sorted(buckets["dock"])),
        water=tuple(sorted(buckets["water"])),
        mop=tuple(sorted(buckets["mop"])),
        consumables=tuple(sorted(buckets["consumables"])),
        diagnostics=tuple(sorted(buckets["diagnostics"])),
    )




def detect_dock_water_status_entities(
    hass: HomeAssistant, entity_ids: Iterable[str]
) -> tuple[str | None, str | None]:
    """Return obvious clean- and dirty-water status entities from candidates."""
    registry = er.async_get(hass)
    clean: str | None = None
    dirty: str | None = None
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        reg = registry.async_get(entity_id)
        tokens = " ".join(
            str(value or "")
            for value in (
                entity_id,
                getattr(reg, "unique_id", None),
                getattr(reg, "translation_key", None),
                getattr(reg, "original_name", None),
                getattr(state, "name", None),
            )
        ).lower().replace("-", "_").replace(" ", "_")
        if dirty is None and "dirty" in tokens and "water" in tokens:
            dirty = entity_id
        elif clean is None and "clean" in tokens and "water" in tokens:
            clean = entity_id
        if clean and dirty:
            break
    return clean, dirty

def _status_entity_binding(hass: HomeAssistant, entity_id: str | None) -> StatusEntityBinding:
    """Read one configured dock-status entity from Home Assistant state memory."""
    if not entity_id:
        return StatusEntityBinding()
    state = hass.states.get(entity_id)
    if state is None:
        return StatusEntityBinding(entity_id=entity_id)
    return StatusEntityBinding(
        entity_id=entity_id,
        exists=True,
        available=state.state not in {STATE_UNAVAILABLE, STATE_UNKNOWN},
        state=state.state,
        device_class=(
            str(state.attributes.get(ATTR_DEVICE_CLASS))
            if state.attributes.get(ATTR_DEVICE_CLASS) is not None
            else None
        ),
    )

def discover_vacuum_snapshot(
    hass: HomeAssistant,
    vacuum_entity_id: str | None,
    related_device_ids: Iterable[str] = (),
    manual_related_entities: Iterable[str] = (),
    clean_water_status_entity_id: str | None = None,
    dirty_water_status_entity_id: str | None = None,
) -> VacuumSnapshot:
    """Build the complete read-only snapshot used by diagnostics and later scheduler code."""
    related_devices = discover_related_devices(
        hass,
        vacuum_entity_id,
        related_device_ids,
    )

    if not vacuum_entity_id:
        return VacuumSnapshot(
            configured=False,
            vacuum_entity_id=None,
            entity_exists=False,
            available=False,
            raw_state=None,
            normalized_state=NormalizedVacuumState.UNAVAILABLE,
            raw_attributes={},
            capabilities=discover_capabilities(None),
            related_devices=related_devices,
            related_entities=RelatedEntitySet(
                manual=tuple(sorted({str(item) for item in manual_related_entities}))
            ),
            room_mapping=RoomMappingInfo(
                clean_area_support=CapabilitySupport.UNKNOWN,
            ),
            dock_water=DockWaterBindings(
                clean_water=_status_entity_binding(hass, clean_water_status_entity_id),
                dirty_water=_status_entity_binding(hass, dirty_water_status_entity_id),
            ),
        )

    state = hass.states.get(vacuum_entity_id)
    normalized = normalize_vacuum_state(state)
    related = discover_related_entities(
        hass,
        vacuum_entity_id,
        related_devices,
        manual_related_entities,
    )

    base_report = discover_capabilities(state)
    capabilities, sources = _extended_capability_evidence(
        hass,
        vacuum_entity_id,
        base_report,
        related_devices,
        manual_related_entities,
    )
    capability_report = CapabilityReport(
        capabilities=capabilities,
        supported_features_raw=base_report.supported_features_raw,
        fan_speed_list=base_report.fan_speed_list,
        sources=sources,
    )

    return VacuumSnapshot(
        configured=True,
        vacuum_entity_id=vacuum_entity_id,
        entity_exists=state is not None,
        available=state is not None and normalized is not NormalizedVacuumState.UNAVAILABLE,
        raw_state=state.state if state is not None else None,
        normalized_state=normalized,
        raw_attributes=dict(state.attributes) if state is not None else {},
        capabilities=capability_report,
        related_devices=related_devices,
        related_entities=related,
        room_mapping=RoomMappingInfo(
            clean_area_support=capability_report.capabilities["room_cleaning"],
        ),
        dock_water=DockWaterBindings(
            clean_water=_status_entity_binding(hass, clean_water_status_entity_id),
            dirty_water=_status_entity_binding(hass, dirty_water_status_entity_id),
        ),
    )
