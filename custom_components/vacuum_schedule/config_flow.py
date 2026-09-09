"""Config flow for Vacuum Schedule."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, time
import json
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector, translation
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
)

from .capabilities import detect_dock_water_status_entities, discover_vacuum_snapshot
from .domain_migration import LEGACY_DOMAIN, async_migrate_legacy_domain_storage
from .const import (
    CONF_CLEAN_WATER_STATUS_ENTITY_ID,
    CONF_DIRTY_WATER_STATUS_ENTITY_ID,
    CONF_RELATED_DEVICE_IDS,
    CONF_RELATED_ENTITIES,
    CONF_CLEANING_ZONES,
    CONF_SCHEDULES,
    CONF_WEEKDAY_OVERRIDES,
    CONF_WEEKDAY_TIMES,
    CONF_FORCE_ENABLED,
    CONF_FORCE_MAX_ADVANCE_MINUTES,
    CONF_FORCE_PRIORITY,
    CONF_FORCE_PREEMPTS_SCHEDULED,
    CONF_FORCE_CONDITION_GROUPS,
    CONF_FORCE_CONDITION_GROUP_NAMES,
    CONF_ZONE_EXECUTION_POLICY,
    CONF_VACUUM_ENTITY_ID,
    DEFAULT_EXECUTION_WINDOW_MINUTES,
    DEFAULT_PASSES,
    DEFAULT_PREWARNING_MINUTES,
    DEFAULT_ZONE_EXECUTION_POLICY,
    DOMAIN,
    ENTRY_MINOR_VERSION,
    ENTRY_VERSION,
    INDEX_TO_WEEKDAY,
    TARGET_TYPE_CLEANING_ZONES,
    WEEKDAY_CODES,
    ZONE_EXECUTION_POLICY_COMBINED,
    ZONE_EXECUTION_POLICY_PROGRESSIVE,
)
from .models import CapabilitySupport, VacuumSnapshot
from .schedule import ScheduleDefinition, ScheduleValidationError, default_cleaning_params
from .cleaning_zones import cleaning_zones_from_options


def _vacuum_schema(default: str | None = None) -> vol.Schema:
    """Build the primary robot selector schema."""
    key = (
        vol.Required(CONF_VACUUM_ENTITY_ID, default=default)
        if default
        else vol.Required(CONF_VACUUM_ENTITY_ID)
    )
    return vol.Schema({key: EntitySelector(EntitySelectorConfig(domain="vacuum"))})


def _resources_schema(
    device_defaults: Iterable[str] = (),
    entity_defaults: Iterable[str] = (),
    clean_water_default: str | None = None,
    dirty_water_default: str | None = None,
) -> vol.Schema:
    """Build selectors for linked devices and explicit dock-water status sources."""
    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_RELATED_DEVICE_IDS,
            default=list(device_defaults),
        ): DeviceSelector(DeviceSelectorConfig(multiple=True)),
        vol.Optional(
            CONF_RELATED_ENTITIES,
            default=list(entity_defaults),
        ): EntitySelector(EntitySelectorConfig(multiple=True)),
    }
    clean_key = (
        vol.Optional(CONF_CLEAN_WATER_STATUS_ENTITY_ID, default=clean_water_default)
        if clean_water_default
        else vol.Optional(CONF_CLEAN_WATER_STATUS_ENTITY_ID)
    )
    dirty_key = (
        vol.Optional(CONF_DIRTY_WATER_STATUS_ENTITY_ID, default=dirty_water_default)
        if dirty_water_default
        else vol.Optional(CONF_DIRTY_WATER_STATUS_ENTITY_ID)
    )
    fields[clean_key] = EntitySelector(EntitySelectorConfig())
    fields[dirty_key] = EntitySelector(EntitySelectorConfig())
    return vol.Schema(fields)


CONF_NAME = "name"
CONF_ENABLED = "enabled"
CONF_WEEKDAYS = "weekdays"
CONF_DATES = "dates"
CONF_LOCAL_TIME = "local_time"
CONF_TARGET_TYPE = "target_type"
CONF_TARGETS = "targets"
CONF_CLEANING_MODE = "cleaning_mode"
CONF_CLEANING_ROUTE = "cleaning_route"
CONF_MOP_MODE = "mop_mode"
CONF_FAN_MODE = "fan_mode"
CONF_WATER_MODE = "water_mode"
CONF_PASSES = "passes"
CONF_PREWARNING_MINUTES = "prewarning_minutes"
CONF_EXECUTION_WINDOW_MINUTES = "execution_window_minutes"
CONF_MINIMUM_BATTERY_PERCENT = "minimum_battery_percent"
CONF_MINIMUM_START_WINDOW_MINUTES = "minimum_start_window_minutes"
CONF_SCHEDULE_ID = "schedule_id"
CONF_CONFIRM = "confirm"

_CONTROL_NONE = "__vacuum_schedule_no_override__"
_CONTROL_ENTITY_SUFFIX = "_entity_id"


def _translated_selector_option(hass: Any, selector_key: str, value: str, fallback: str | None = None) -> str:
    """Return a localized option label from Home Assistant translation resources."""
    language = str(getattr(hass.config, "language", "en") or "en")
    resources = translation.async_get_cached_translations(
        hass, language, "selector", DOMAIN
    )
    key = f"component.{DOMAIN}.selector.{selector_key}.options.{value}"
    return resources.get(key, fallback if fallback is not None else value.replace("_", " "))


def _control_value_label(hass: Any, kind: str, value: str) -> str:
    """Translate common device preset values while preserving unknown vendor values."""
    selector_keys = {
        CONF_CLEANING_MODE: "cleaning_mode_value",
        CONF_CLEANING_ROUTE: "cleaning_route_value",
        CONF_MOP_MODE: "mop_mode_value",
        CONF_WATER_MODE: "water_mode_value",
    }
    key = selector_keys.get(kind)
    if key is None:
        return value
    return _translated_selector_option(hass, key, value.strip().lower(), value.replace("_", " "))


def _parse_csv(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _dates_from_text(value: str) -> list[str]:
    if not value.strip():
        return []
    result = _parse_csv(value)
    for item in result:
        date.fromisoformat(item)
    return result


def _time_to_string(value: str | time) -> str:
    if isinstance(value, time):
        return value.isoformat()
    return str(value)


def _control_token(entity_id: str, value: str) -> str:
    return json.dumps([entity_id, value], ensure_ascii=False, separators=(",", ":"))


def _decode_control_token(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str) or value == _CONTROL_NONE:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or len(payload) != 2:
        return None
    entity_id, option = str(payload[0]), str(payload[1])
    if "." not in entity_id or not option:
        return None
    return entity_id, option


def _stored_control_token(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    entity_id = params.get(f"{key}{_CONTROL_ENTITY_SUFFIX}")
    if entity_id and value not in (None, ""):
        return _control_token(str(entity_id), str(value))
    return _CONTROL_NONE


def _schedule_to_form(schedule: ScheduleDefinition) -> dict[str, Any]:
    params = dict(schedule.cleaning_params)
    weekday_times = {
        INDEX_TO_WEEKDAY[index]: value.isoformat(timespec="minutes")
        for index, value in sorted(schedule.weekday_times.items())
    }
    weekday_overrides: dict[str, dict[str, Any]] = {}
    for index, raw_override in sorted(schedule.weekday_overrides.items()):
        override = dict(raw_override)
        form_override: dict[str, Any] = {}
        for key in (CONF_CLEANING_MODE, CONF_MOP_MODE, CONF_WATER_MODE):
            token = _stored_control_token(override, key)
            if token != _CONTROL_NONE:
                form_override[key] = token
        if override.get(CONF_FAN_MODE) not in (None, ""):
            form_override[CONF_FAN_MODE] = str(override[CONF_FAN_MODE])
        if override.get(CONF_PASSES) not in (None, ""):
            form_override[CONF_PASSES] = int(override[CONF_PASSES])
        if form_override:
            weekday_overrides[INDEX_TO_WEEKDAY[index]] = form_override
    return {
        CONF_NAME: schedule.name,
        CONF_ENABLED: schedule.enabled,
        CONF_WEEKDAYS: [INDEX_TO_WEEKDAY[index] for index in schedule.weekdays],
        CONF_DATES: ", ".join(item.isoformat() for item in schedule.dates),
        CONF_LOCAL_TIME: schedule.local_time,
        CONF_WEEKDAY_TIMES: weekday_times,
        CONF_WEEKDAY_OVERRIDES: weekday_overrides,
        CONF_TARGET_TYPE: schedule.target_type,
        CONF_CLEANING_MODE: _stored_control_token(params, CONF_CLEANING_MODE),
        CONF_CLEANING_ROUTE: _stored_control_token(params, CONF_CLEANING_ROUTE),
        CONF_MOP_MODE: _stored_control_token(params, CONF_MOP_MODE),
        CONF_FAN_MODE: str(params.get(CONF_FAN_MODE, "")),
        CONF_WATER_MODE: _stored_control_token(params, CONF_WATER_MODE),
        CONF_PASSES: int(params.get(CONF_PASSES, DEFAULT_PASSES)),
        CONF_ZONE_EXECUTION_POLICY: schedule.zone_execution_policy,
        CONF_PREWARNING_MINUTES: schedule.prewarning_minutes,
        CONF_EXECUTION_WINDOW_MINUTES: schedule.execution_window_minutes,
        CONF_MINIMUM_BATTERY_PERCENT: params.get(CONF_MINIMUM_BATTERY_PERCENT, ""),
        CONF_MINIMUM_START_WINDOW_MINUTES: params.get(CONF_MINIMUM_START_WINDOW_MINUTES, ""),
        CONF_FORCE_ENABLED: schedule.force_enabled,
        CONF_FORCE_MAX_ADVANCE_MINUTES: schedule.force_max_advance_minutes,
        CONF_FORCE_PRIORITY: schedule.force_priority,
        CONF_FORCE_PREEMPTS_SCHEDULED: schedule.force_preempts_scheduled,
        CONF_FORCE_CONDITION_GROUPS: [[dict(condition) for condition in group] for group in schedule.force_condition_groups],
        CONF_FORCE_CONDITION_GROUP_NAMES: list(schedule.force_condition_group_names),
        "notification_policy": dict(schedule.notification_policy),
    }


def _weekday_override_params_from_form(raw: Any) -> dict[str, dict[str, Any]]:
    """Decode sparse UI weekday overrides into execution parameter mappings."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for raw_day, raw_values in raw.items():
        day = str(raw_day).strip().lower()
        if day not in WEEKDAY_CODES or not isinstance(raw_values, dict):
            continue
        params: dict[str, Any] = {}
        for key in (CONF_CLEANING_MODE, CONF_MOP_MODE, CONF_WATER_MODE):
            token = raw_values.get(key)
            decoded = _decode_control_token(token)
            if decoded is not None:
                entity_id, value = decoded
                params[key] = value
                params[f"{key}{_CONTROL_ENTITY_SUFFIX}"] = entity_id
        fan_mode = raw_values.get(CONF_FAN_MODE)
        if fan_mode not in (None, "", _CONTROL_NONE):
            params[CONF_FAN_MODE] = str(fan_mode)
        passes = raw_values.get(CONF_PASSES)
        if passes not in (None, ""):
            params[CONF_PASSES] = max(1, int(passes))
        if params:
            result[day] = params
    return result


def _state_discrete_options(state: Any) -> list[str]:
    """Return finite options from a select or reasonably small number entity."""
    if state is None:
        return []
    entity_id = str(state.entity_id)
    if entity_id.startswith(("select.", "input_select.")):
        values = state.attributes.get("options", [])
        return [str(item) for item in values if str(item).strip()]
    if entity_id.startswith(("number.", "input_number.")):
        try:
            minimum = float(state.attributes["min"])
            maximum = float(state.attributes["max"])
            step = float(state.attributes.get("step", 1))
        except (KeyError, TypeError, ValueError):
            return []
        if step <= 0:
            return []
        count = int(round((maximum - minimum) / step)) + 1
        if count <= 0 or count > 50:
            return []
        values: list[str] = []
        for index in range(count):
            item = minimum + step * index
            values.append(str(int(item)) if item.is_integer() else f"{item:g}")
        return values
    return []


def _control_kind(entity_id: str, state: Any, registry_entry: Any = None) -> str | None:
    friendly = "" if state is None else str(state.attributes.get("friendly_name", ""))
    registry_tokens = " ".join(
        str(getattr(registry_entry, attr, "") or "")
        for attr in ("unique_id", "original_name", "translation_key")
    )
    haystack = f"{entity_id} {friendly} {registry_tokens}".lower().replace("-", "_").replace(" ", "_")
    if "cleaning_route" in haystack or "clean_route" in haystack:
        return CONF_CLEANING_ROUTE
    if "cleaning_mode" in haystack or "clean_mode" in haystack:
        return CONF_CLEANING_MODE
    if "mop_mode" in haystack:
        return CONF_MOP_MODE
    if any(token in haystack for token in ("mop_intensity", "water_box_mode", "water_flow", "water_level", "water")):
        return CONF_WATER_MODE
    return None


def _device_control_options(hass: Any, snapshot: VacuumSnapshot) -> dict[str, list[dict[str, str]]]:
    """Build device-backed schedule selectors from discovered related controls."""
    result: dict[str, list[dict[str, str]]] = {
        CONF_CLEANING_MODE: [],
        CONF_CLEANING_ROUTE: [],
        CONF_MOP_MODE: [],
        CONF_WATER_MODE: [],
    }
    sources = snapshot.capabilities.sources.get("mop_water_mode", ())
    by_kind: dict[str, list[tuple[str, Any, list[str]]]] = {key: [] for key in result}
    registry = er.async_get(hass)
    for entity_id in sources:
        state = hass.states.get(entity_id)
        registry_entry = registry.async_get(entity_id)
        kind = _control_kind(entity_id, state, registry_entry)
        values = _state_discrete_options(state)
        if kind is None or not values:
            continue
        by_kind[kind].append((entity_id, state, values))
    for kind, controls in by_kind.items():
        multi = len(controls) > 1
        for entity_id, state, values in controls:
            name = state.name if state is not None else entity_id
            for value in values:
                if value.strip().lower() == "custom":
                    continue
                localized = _control_value_label(hass, kind, value)
                result[kind].append(
                    {
                        "value": _control_token(entity_id, value),
                        "label": f"{name}: {localized}" if multi else localized,
                    }
                )
    return result


def _select_with_none(hass: Any, options: list[dict[str, str]], default: str) -> selector.SelectSelector:
    no_override = _translated_selector_option(
        hass, "override_choice", "none", "Do not override / use vacuum setting"
    )
    rendered = [{"value": _CONTROL_NONE, "label": no_override}, *options]
    known = {item["value"] for item in rendered}
    if default not in known and default != _CONTROL_NONE:
        decoded = _decode_control_token(default)
        label = decoded[1] if decoded is not None else str(default)
        rendered.append({"value": default, "label": f"⚠ {label}"})
    return selector.SelectSelector(selector.SelectSelectorConfig(options=rendered))


def _schedule_details_schema(
    hass: Any,
    snapshot: VacuumSnapshot,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    defaults = defaults or {
        CONF_NAME: "",
        CONF_ENABLED: True,
        CONF_WEEKDAYS: [],
        CONF_DATES: "",
        CONF_LOCAL_TIME: time(9, 0),
        CONF_TARGET_TYPE: TARGET_TYPE_CLEANING_ZONES,
        CONF_CLEANING_MODE: _CONTROL_NONE,
        CONF_CLEANING_ROUTE: _CONTROL_NONE,
        CONF_MOP_MODE: _CONTROL_NONE,
        CONF_FAN_MODE: "",
        CONF_WATER_MODE: _CONTROL_NONE,
        CONF_PASSES: DEFAULT_PASSES,
        CONF_ZONE_EXECUTION_POLICY: DEFAULT_ZONE_EXECUTION_POLICY,
        CONF_PREWARNING_MINUTES: DEFAULT_PREWARNING_MINUTES,
        CONF_EXECUTION_WINDOW_MINUTES: DEFAULT_EXECUTION_WINDOW_MINUTES,
    }
    fields: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_ENABLED, default=defaults.get(CONF_ENABLED, True)): selector.BooleanSelector(),
        vol.Optional(CONF_WEEKDAYS, default=list(defaults.get(CONF_WEEKDAYS, []))): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(WEEKDAY_CODES), multiple=True, translation_key="weekdays")
        ),
        vol.Optional(CONF_DATES, default=defaults.get(CONF_DATES, "")): selector.TextSelector(
            selector.TextSelectorConfig(multiline=False)
        ),
        vol.Required(CONF_LOCAL_TIME, default=defaults.get(CONF_LOCAL_TIME, time(9, 0))): selector.TimeSelector(),
    }

    fan_modes = list(snapshot.capabilities.fan_speed_list)
    if fan_modes:
        saved_fan = str(defaults.get(CONF_FAN_MODE, ""))
        # "custom" is a vendor-side placeholder, not a useful deterministic
        # scheduler preset. Hide it for new selections but preserve it when an
        # existing schedule already stores it.
        visible_fans = [item for item in fan_modes if item.lower() != "custom"]
        if saved_fan.lower() == "custom" and saved_fan not in visible_fans:
            visible_fans.append(saved_fan)
        fan_options = [_CONTROL_NONE, *visible_fans]
        if saved_fan and saved_fan not in fan_options:
            fan_options.append(saved_fan)
        fan_default = saved_fan or _CONTROL_NONE
        fields[vol.Optional(CONF_FAN_MODE, default=fan_default)] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=fan_options, translation_key="fan_mode")
        )

    controls = _device_control_options(hass, snapshot)
    for key in (CONF_CLEANING_MODE, CONF_CLEANING_ROUTE, CONF_MOP_MODE, CONF_WATER_MODE):
        if controls[key] or defaults.get(key, _CONTROL_NONE) != _CONTROL_NONE:
            default = str(defaults.get(key, _CONTROL_NONE))
            fields[vol.Optional(key, default=default)] = _select_with_none(hass, controls[key], default)

    # Repetitions are intentionally a choice, not a free numeric text field.
    # The exact execution validation remains an adapter concern for later stages.
    fields[vol.Required(CONF_PASSES, default=str(defaults.get(CONF_PASSES, DEFAULT_PASSES)))] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[{"value": str(item), "label": str(item)} for item in range(1, 11)]
        )
    )
    fields[vol.Required(
        CONF_ZONE_EXECUTION_POLICY,
        default=str(defaults.get(CONF_ZONE_EXECUTION_POLICY, DEFAULT_ZONE_EXECUTION_POLICY)),
    )] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[ZONE_EXECUTION_POLICY_COMBINED, ZONE_EXECUTION_POLICY_PROGRESSIVE],
            translation_key="zone_execution_policy",
        )
    )
    fields[vol.Required(CONF_PREWARNING_MINUTES, default=defaults.get(CONF_PREWARNING_MINUTES, DEFAULT_PREWARNING_MINUTES))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=1440, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    fields[vol.Required(CONF_EXECUTION_WINDOW_MINUTES, default=defaults.get(CONF_EXECUTION_WINDOW_MINUTES, DEFAULT_EXECUTION_WINDOW_MINUTES))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=10080, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    battery_default = defaults.get(CONF_MINIMUM_BATTERY_PERCENT)
    battery_marker = (
        vol.Optional(CONF_MINIMUM_BATTERY_PERCENT, default=float(battery_default))
        if battery_default not in (None, "")
        else vol.Optional(CONF_MINIMUM_BATTERY_PERCENT)
    )
    fields[battery_marker] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    window_default = defaults.get(CONF_MINIMUM_START_WINDOW_MINUTES)
    window_marker = (
        vol.Optional(CONF_MINIMUM_START_WINDOW_MINUTES, default=int(window_default))
        if window_default not in (None, "")
        else vol.Optional(CONF_MINIMUM_START_WINDOW_MINUTES)
    )
    fields[window_marker] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=10080, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    return vol.Schema(fields)


async def _async_robot_segment_options(hass: Any, vacuum_entity_id: str) -> list[dict[str, str]]:
    """Read cleanable segments directly from the loaded vacuum entity."""
    segments: list[Any] = []
    component = hass.data.get(DATA_INSTANCES, {}).get("vacuum")
    entity = component.get_entity(vacuum_entity_id) if component is not None else None
    getter = getattr(entity, "async_get_segments", None) if entity is not None else None
    if getter is not None:
        try:
            segments = list(await getter())
        except Exception:  # The flow has a registry fallback below.
            segments = []

    # Home Assistant stores the last mapped segments in entity-registry options.
    # This also keeps editing usable while the device is temporarily unavailable.
    if not segments:
        registry_entry = er.async_get(hass).async_get(vacuum_entity_id)
        vacuum_options = {}
        if registry_entry is not None:
            vacuum_options = dict((registry_entry.options or {}).get("vacuum", {}))
        segments = list(vacuum_options.get("last_seen_segments") or [])

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for segment in segments:
        if isinstance(segment, dict):
            segment_id = str(segment.get("id", ""))
            name = str(segment.get("name") or segment_id)
            group = segment.get("group")
        else:
            segment_id = str(getattr(segment, "id", ""))
            name = str(getattr(segment, "name", None) or segment_id)
            group = getattr(segment, "group", None)
        if not segment_id or segment_id in seen:
            continue
        seen.add(segment_id)
        # The vacuum API may expose a broad map/floor group (for example
        # "Home") in addition to the actual room name. The scheduler target
        # selector is already explicitly a room/segment selector, so repeating
        # the group only adds visual noise.
        options.append({"value": segment_id, "label": name})
    return options


async def _schedule_targets_schema(
    hass: Any,
    vacuum_entity_id: str,
    target_type: str,
    defaults: list[str] | tuple[str, ...] | None = None,
    cleaning_zones: list[Any] | None = None,
) -> tuple[vol.Schema, bool]:
    """Return target selector. New schedules always select Scheduler Cleaning Zones."""
    defaults = list(defaults or [])
    segments_available = True
    if target_type == TARGET_TYPE_CLEANING_ZONES:
        zones = cleaning_zones or []
        options = [{"value": zone.zone_id, "label": zone.name} for zone in zones]
        known = {item["value"] for item in options}
        for saved in defaults:
            if saved not in known:
                options.append({"value": saved, "label": f"⚠ {saved}"})
        field = selector.SelectSelector(selector.SelectSelectorConfig(options=options, multiple=True))
        return vol.Schema({vol.Required(CONF_TARGETS, default=defaults): field}), bool(options)
    # Legacy branches remain readable only for pre-migration entries.
    if target_type == "areas":
        field = selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)); default: Any = defaults
    elif target_type == "segments":
        options = await _async_robot_segment_options(hass, vacuum_entity_id)
        known = {item["value"] for item in options}
        for saved in defaults:
            if saved not in known: options.append({"value": saved, "label": f"⚠ {saved}"})
        segments_available = bool(options)
        field = selector.SelectSelector(selector.SelectSelectorConfig(options=options, multiple=True)); default = defaults
    else:
        field = selector.TextSelector(selector.TextSelectorConfig(multiline=True)); default = defaults[0] if defaults else ""
    return vol.Schema({vol.Required(CONF_TARGETS, default=default): field}), segments_available


def _targets_from_form(target_type: str, value: Any) -> list[str]:
    """Normalize targets selected by the user."""
    if target_type in {"areas", "segments", TARGET_TYPE_CLEANING_ZONES}:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)] if str(value).strip() else []
    text = str(value).strip()
    return [text] if text else []


def _apply_control_from_form(params: dict[str, Any], user_input: dict[str, Any], key: str) -> None:
    if key not in user_input:
        return
    params.pop(f"{key}{_CONTROL_ENTITY_SUFFIX}", None)
    decoded = _decode_control_token(user_input.get(key))
    if decoded is None:
        params[key] = ""
        return
    entity_id, value = decoded
    params[key] = value
    params[f"{key}{_CONTROL_ENTITY_SUFFIX}"] = entity_id


def _schedule_from_form(
    user_input: dict[str, Any],
    targets: list[str],
    previous: ScheduleDefinition | None = None,
) -> ScheduleDefinition:
    params = dict(previous.cleaning_params) if previous is not None else default_cleaning_params()
    _apply_control_from_form(params, user_input, CONF_CLEANING_MODE)
    _apply_control_from_form(params, user_input, CONF_CLEANING_ROUTE)
    _apply_control_from_form(params, user_input, CONF_MOP_MODE)
    _apply_control_from_form(params, user_input, CONF_WATER_MODE)
    if CONF_FAN_MODE in user_input:
        fan_mode = str(user_input.get(CONF_FAN_MODE, "")).strip()
        params[CONF_FAN_MODE] = "" if fan_mode == _CONTROL_NONE else fan_mode
    params[CONF_PASSES] = int(user_input.get(CONF_PASSES, params.get(CONF_PASSES, DEFAULT_PASSES)))
    if CONF_MINIMUM_BATTERY_PERCENT in user_input:
        raw_battery = user_input.get(CONF_MINIMUM_BATTERY_PERCENT)
        if raw_battery in (None, ""):
            params.pop(CONF_MINIMUM_BATTERY_PERCENT, None)
        else:
            params[CONF_MINIMUM_BATTERY_PERCENT] = max(0.0, min(100.0, float(raw_battery)))
    if CONF_MINIMUM_START_WINDOW_MINUTES in user_input:
        raw_window = user_input.get(CONF_MINIMUM_START_WINDOW_MINUTES)
        if raw_window in (None, ""):
            params.pop(CONF_MINIMUM_START_WINDOW_MINUTES, None)
        else:
            params[CONF_MINIMUM_START_WINDOW_MINUTES] = max(0, int(raw_window))
    raw_weekday_times = user_input.get(CONF_WEEKDAY_TIMES)
    if isinstance(raw_weekday_times, dict):
        weekday_times = {
            str(day): _time_to_string(value)
            for day, value in raw_weekday_times.items()
            if str(day).lower() in WEEKDAY_CODES and str(value).strip()
        }
        weekdays = list(weekday_times)
    else:
        weekdays = list(user_input.get(CONF_WEEKDAYS, []))
        if previous is not None and previous.weekday_times:
            # The native Options Flow still exposes the legacy uniform editor.
            # Preserve genuinely flexible times for existing days rather than
            # silently flattening them when unrelated fields are edited there.
            selected = {
                WEEKDAY_CODES.index(str(day))
                for day in weekdays
                if str(day) in WEEKDAY_CODES
            }
            weekday_times = {
                INDEX_TO_WEEKDAY[index]: value.isoformat(timespec="minutes")
                for index, value in previous.weekday_times.items()
                if index in selected
            }
            for day in weekdays:
                code = str(day)
                if code not in weekday_times:
                    weekday_times[code] = _time_to_string(user_input[CONF_LOCAL_TIME])
        else:
            weekday_times = {
                str(day): _time_to_string(user_input[CONF_LOCAL_TIME])
                for day in weekdays
            }

    if CONF_WEEKDAY_OVERRIDES in user_input:
        weekday_overrides = _weekday_override_params_from_form(
            user_input.get(CONF_WEEKDAY_OVERRIDES)
        )
    elif previous is not None:
        weekday_overrides = {
            INDEX_TO_WEEKDAY[index]: dict(values)
            for index, values in previous.weekday_overrides.items()
            if INDEX_TO_WEEKDAY[index] in weekdays
        }
    else:
        weekday_overrides = {}

    payload = {
        "name": str(user_input[CONF_NAME]),
        "enabled": bool(user_input.get(CONF_ENABLED, True)),
        "paused": previous.paused if previous is not None else False,
        "weekdays": weekdays,
        "dates": _dates_from_text(str(user_input.get(CONF_DATES, ""))),
        "local_time": _time_to_string(user_input[CONF_LOCAL_TIME]),
        "weekday_times": weekday_times,
        "weekday_overrides": weekday_overrides,
        "target_type": str(user_input.get(CONF_TARGET_TYPE, TARGET_TYPE_CLEANING_ZONES)),
        "targets": targets,
        "zone_execution_policy": str(user_input.get(CONF_ZONE_EXECUTION_POLICY, DEFAULT_ZONE_EXECUTION_POLICY)),
        "cleaning_params": params,
        "prewarning_minutes": int(user_input[CONF_PREWARNING_MINUTES]),
        "execution_window_minutes": int(user_input[CONF_EXECUTION_WINDOW_MINUTES]),
        "notification_policy": dict(user_input.get("notification_policy", previous.notification_policy if previous is not None else {})),
        "force_enabled": bool(user_input.get(CONF_FORCE_ENABLED, previous.force_enabled if previous is not None else False)),
        "force_max_advance_minutes": int(user_input.get(CONF_FORCE_MAX_ADVANCE_MINUTES, previous.force_max_advance_minutes if previous is not None else 0) or 0),
        "force_priority": int(user_input.get(CONF_FORCE_PRIORITY, previous.force_priority if previous is not None else 0) or 0),
        "force_preempts_scheduled": bool(user_input.get(CONF_FORCE_PREEMPTS_SCHEDULED, previous.force_preempts_scheduled if previous is not None else False)),
        "force_condition_groups": user_input.get(CONF_FORCE_CONDITION_GROUPS, [[dict(condition) for condition in group] for group in previous.force_condition_groups] if previous is not None else []),
        "force_condition_group_names": user_input.get(CONF_FORCE_CONDITION_GROUP_NAMES, list(previous.force_condition_group_names) if previous is not None else []),
    }
    if previous is None:
        return ScheduleDefinition.create(**payload)
    return previous.revised(**payload)


def _validate_zone_execution_compatibility(
    schedule: ScheduleDefinition, zones: list[Any]
) -> None:
    """Reject a literal single-run policy that needs incompatible robot APIs."""
    if schedule.zone_execution_policy != ZONE_EXECUTION_POLICY_COMBINED:
        return
    zone_by_id = {zone.zone_id: zone for zone in zones}
    target_types = {
        zone_by_id[zone_id].robot_target_type.value
        for zone_id in schedule.targets
        if zone_id in zone_by_id
    }
    if len(target_types) > 1:
        raise ScheduleValidationError("combined_execution_mixed_target_types")


def _schedule_select_schema(schedules: list[ScheduleDefinition]) -> vol.Schema:
    options = [{"value": item.schedule_id, "label": item.name} for item in schedules]
    return vol.Schema(
        {
            vol.Required(CONF_SCHEDULE_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options)
            )
        }
    )


class VacuumScheduleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Vacuum Schedule configuration."""

    VERSION = ENTRY_VERSION
    MINOR_VERSION = ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        """Initialize the flow."""
        self._pending_vacuum_entity_id: str | None = None
        self._pending_title = "Vacuum Schedule"
        self._pending_related_device_ids: tuple[str, ...] = ()
        self._pending_related_entities: tuple[str, ...] = ()
        self._pending_clean_water_status_entity_id: str | None = None
        self._pending_dirty_water_status_entity_id: str | None = None
        self._legacy_entry_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the schedule-management options flow."""
        return VacuumScheduleOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the primary vacuum entity."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_vacuum_schema(),
            )

        vacuum_entity_id = user_input[CONF_VACUUM_ENTITY_ID]
        if not self._valid_vacuum(vacuum_entity_id):
            return self.async_show_form(
                step_id="user",
                data_schema=_vacuum_schema(vacuum_entity_id),
                errors={CONF_VACUUM_ENTITY_ID: "invalid_vacuum_entity"},
            )

        if self._vacuum_already_configured(vacuum_entity_id):
            return self.async_abort(reason="already_configured")

        if legacy_entry := self._legacy_entry_for_vacuum(vacuum_entity_id):
            self._legacy_entry_id = legacy_entry.entry_id
            self._pending_vacuum_entity_id = vacuum_entity_id
            self._pending_title = legacy_entry.title
            return await self.async_step_migrate_legacy()

        self._pending_vacuum_entity_id = vacuum_entity_id
        self._pending_title = self._entity_title(vacuum_entity_id)
        self._pending_related_device_ids = ()
        self._pending_related_entities = ()
        self._pending_clean_water_status_entity_id = None
        self._pending_dirty_water_status_entity_id = None
        return await self.async_step_resources()

    async def async_step_migrate_legacy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the one-time 0.12.61 domain migration."""
        if self._legacy_entry_id is None:
            return self.async_abort(reason="legacy_entry_not_found")
        legacy_entry = self.hass.config_entries.async_get_entry(self._legacy_entry_id)
        if legacy_entry is None or legacy_entry.domain != LEGACY_DOMAIN:
            return self.async_abort(reason="legacy_entry_not_found")
        if user_input is None:
            return self.async_show_form(
                step_id="migrate_legacy",
                data_schema=vol.Schema({}),
                description_placeholders={"legacy_title": legacy_entry.title},
            )
        return self.async_create_entry(
            title=legacy_entry.title,
            data=dict(legacy_entry.data),
            options=dict(legacy_entry.options),
        )

    async def async_on_create_entry(
        self, result: ConfigFlowResult
    ) -> ConfigFlowResult:
        """Finalize a legacy-domain migration after the new entry has an id."""
        if self._legacy_entry_id is None:
            return result
        new_entry = result.get("result")
        if not isinstance(new_entry, config_entries.ConfigEntry):
            return result
        legacy_entry_id = self._legacy_entry_id
        await async_migrate_legacy_domain_storage(
            self.hass, legacy_entry_id, new_entry.entry_id
        )
        # ConfigEntry.domain is immutable; removing the obsolete entry is the
        # supported way to finish the domain rename after its data was copied.
        await self.hass.config_entries.async_remove(legacy_entry_id)
        self._legacy_entry_id = None
        return result

    async def async_step_resources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select related HA devices and optional individual entity fallbacks."""
        vacuum_entity_id = self._require_pending_vacuum()
        if user_input is not None:
            self._pending_related_device_ids = self._normalize_device_ids(
                vacuum_entity_id,
                user_input.get(CONF_RELATED_DEVICE_IDS, ()),
            )
            self._pending_related_entities = self._normalize_entity_ids(
                vacuum_entity_id,
                user_input.get(CONF_RELATED_ENTITIES, ()),
            )
            self._pending_clean_water_status_entity_id = self._normalize_optional_entity_id(
                user_input.get(CONF_CLEAN_WATER_STATUS_ENTITY_ID)
            )
            self._pending_dirty_water_status_entity_id = self._normalize_optional_entity_id(
                user_input.get(CONF_DIRTY_WATER_STATUS_ENTITY_ID)
            )
            self._autodetect_missing_dock_water_bindings(vacuum_entity_id)
            return await self.async_step_review()

        return self.async_show_form(
            step_id="resources",
            data_schema=_resources_schema(
                self._pending_related_device_ids,
                self._pending_related_entities,
                self._pending_clean_water_status_entity_id,
                self._pending_dirty_water_status_entity_id,
            ),
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review final combined discovery before creating the entry."""
        vacuum_entity_id = self._require_pending_vacuum()
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending_title,
                data=self._pending_data(vacuum_entity_id),
            )

        snapshot = discover_vacuum_snapshot(
            self.hass,
            vacuum_entity_id,
            self._pending_related_device_ids,
            self._pending_related_entities,
            self._pending_clean_water_status_entity_id,
            self._pending_dirty_water_status_entity_id,
        )
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders=self._review_placeholders(snapshot),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the primary robot for an existing entry."""
        entry = self._get_reconfigure_entry()
        current_vacuum = entry.data.get(CONF_VACUUM_ENTITY_ID)
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_vacuum_schema(current_vacuum),
            )

        vacuum_entity_id = user_input[CONF_VACUUM_ENTITY_ID]
        if not self._valid_vacuum(vacuum_entity_id):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_vacuum_schema(vacuum_entity_id),
                errors={CONF_VACUUM_ENTITY_ID: "invalid_vacuum_entity"},
            )

        if self._vacuum_already_configured(
            vacuum_entity_id, exclude_entry_id=entry.entry_id
        ):
            return self.async_abort(reason="already_configured")

        self._pending_vacuum_entity_id = vacuum_entity_id
        self._pending_title = self._entity_title(vacuum_entity_id)
        if vacuum_entity_id == current_vacuum:
            self._pending_related_device_ids = tuple(
                entry.data.get(CONF_RELATED_DEVICE_IDS, ())
            )
            self._pending_related_entities = tuple(
                entry.data.get(CONF_RELATED_ENTITIES, ())
            )
            self._pending_clean_water_status_entity_id = entry.data.get(
                CONF_CLEAN_WATER_STATUS_ENTITY_ID
            )
            self._pending_dirty_water_status_entity_id = entry.data.get(
                CONF_DIRTY_WATER_STATUS_ENTITY_ID
            )
        else:
            self._pending_related_device_ids = ()
            self._pending_related_entities = ()
            self._pending_clean_water_status_entity_id = None
            self._pending_dirty_water_status_entity_id = None
        return await self.async_step_reconfigure_resources()

    async def async_step_reconfigure_resources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update related devices and optional individual entity fallbacks."""
        vacuum_entity_id = self._require_pending_vacuum()
        if user_input is not None:
            self._pending_related_device_ids = self._normalize_device_ids(
                vacuum_entity_id,
                user_input.get(CONF_RELATED_DEVICE_IDS, ()),
            )
            self._pending_related_entities = self._normalize_entity_ids(
                vacuum_entity_id,
                user_input.get(CONF_RELATED_ENTITIES, ()),
            )
            self._pending_clean_water_status_entity_id = self._normalize_optional_entity_id(
                user_input.get(CONF_CLEAN_WATER_STATUS_ENTITY_ID)
            )
            self._pending_dirty_water_status_entity_id = self._normalize_optional_entity_id(
                user_input.get(CONF_DIRTY_WATER_STATUS_ENTITY_ID)
            )
            self._autodetect_missing_dock_water_bindings(vacuum_entity_id)
            return await self.async_step_reconfigure_review()

        return self.async_show_form(
            step_id="reconfigure_resources",
            data_schema=_resources_schema(
                self._pending_related_device_ids,
                self._pending_related_entities,
                self._pending_clean_water_status_entity_id,
                self._pending_dirty_water_status_entity_id,
            ),
        )

    async def async_step_reconfigure_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review combined discovery and save an existing entry."""
        vacuum_entity_id = self._require_pending_vacuum()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                title=self._pending_title,
                data_updates=self._pending_data(vacuum_entity_id),
                reload_even_if_entry_is_unchanged=False,
            )

        snapshot = discover_vacuum_snapshot(
            self.hass,
            vacuum_entity_id,
            self._pending_related_device_ids,
            self._pending_related_entities,
            self._pending_clean_water_status_entity_id,
            self._pending_dirty_water_status_entity_id,
        )
        return self.async_show_form(
            step_id="reconfigure_review",
            data_schema=vol.Schema({}),
            description_placeholders=self._review_placeholders(snapshot),
        )

    def _require_pending_vacuum(self) -> str:
        """Return the selected vacuum after a preceding selection step."""
        if self._pending_vacuum_entity_id is None:
            raise RuntimeError("Vacuum entity was not selected before this step")
        return self._pending_vacuum_entity_id

    def _valid_vacuum(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return entity_id.startswith("vacuum.") and state is not None

    def _vacuum_already_configured(
        self, entity_id: str, *, exclude_entry_id: str | None = None
    ) -> bool:
        for entry in self._async_current_entries():
            if exclude_entry_id and entry.entry_id == exclude_entry_id:
                continue
            if entry.data.get(CONF_VACUUM_ENTITY_ID) == entity_id:
                return True
        return False

    def _legacy_entry_for_vacuum(
        self, entity_id: str
    ) -> config_entries.ConfigEntry | None:
        """Return the pre-0.13 entry for this robot, if one still exists."""
        for entry in self.hass.config_entries.async_entries(LEGACY_DOMAIN):
            if entry.data.get(CONF_VACUUM_ENTITY_ID) == entity_id:
                return entry
        return None

    def _normalize_device_ids(
        self,
        vacuum_entity_id: str,
        values: Iterable[str] | str,
    ) -> tuple[str, ...]:
        if isinstance(values, str):
            values = (values,)

        primary_device_id: str | None = None
        source_entry = er.async_get(self.hass).async_get(vacuum_entity_id)
        if source_entry is not None:
            primary_device_id = source_entry.device_id

        return tuple(
            sorted(
                {
                    str(device_id)
                    for device_id in values
                    if device_id and str(device_id) != primary_device_id
                }
            )
        )

    @staticmethod
    def _normalize_entity_ids(
        vacuum_entity_id: str,
        values: Iterable[str] | str,
    ) -> tuple[str, ...]:
        if isinstance(values, str):
            values = (values,)
        return tuple(
            sorted(
                {
                    str(entity_id)
                    for entity_id in values
                    if entity_id and str(entity_id) != vacuum_entity_id
                }
            )
        )

    @staticmethod
    def _normalize_optional_entity_id(value: Any) -> str | None:
        """Normalize an optional single entity selector result."""
        if value in (None, ""):
            return None
        return str(value)

    def _autodetect_missing_dock_water_bindings(self, vacuum_entity_id: str) -> None:
        """Auto-bind obvious clean/dirty dock-water entities from selected devices."""
        if (
            self._pending_clean_water_status_entity_id
            and self._pending_dirty_water_status_entity_id
        ):
            return
        snapshot = discover_vacuum_snapshot(
            self.hass,
            vacuum_entity_id,
            self._pending_related_device_ids,
            self._pending_related_entities,
        )
        clean, dirty = detect_dock_water_status_entities(
            self.hass, snapshot.related_entities.all_entity_ids
        )
        if self._pending_clean_water_status_entity_id is None:
            self._pending_clean_water_status_entity_id = clean
        if self._pending_dirty_water_status_entity_id is None:
            self._pending_dirty_water_status_entity_id = dirty


    def _pending_data(self, vacuum_entity_id: str) -> dict[str, Any]:
        return {
            CONF_VACUUM_ENTITY_ID: vacuum_entity_id,
            CONF_RELATED_DEVICE_IDS: list(self._pending_related_device_ids),
            CONF_RELATED_ENTITIES: list(self._pending_related_entities),
            CONF_CLEAN_WATER_STATUS_ENTITY_ID: self._pending_clean_water_status_entity_id,
            CONF_DIRTY_WATER_STATUS_ENTITY_ID: self._pending_dirty_water_status_entity_id,
        }

    def _entity_title(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.name if state is not None else entity_id

    def _device_title(self, device_id: str | None) -> str:
        if not device_id:
            return "—"
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return f"`{device_id}`"
        name = (
            getattr(device, "name_by_user", None)
            or getattr(device, "name", None)
            or getattr(device, "model", None)
            or device_id
        )
        return f"{name} (`{device_id}`)"

    def _format_devices(self, device_ids: Iterable[str]) -> str:
        values = [self._device_title(device_id) for device_id in device_ids]
        return ", ".join(values) or "—"

    def _format_entities(self, entity_ids: Iterable[str]) -> str:
        """Format detected entities for a human-readable config-flow review."""
        values: list[str] = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.name == entity_id:
                values.append(f"`{entity_id}`")
            else:
                values.append(f"{state.name} (`{entity_id}`)")
        return ", ".join(values) or "—"

    @staticmethod
    def _support_mark(value: CapabilitySupport) -> str:
        """Render capability support without introducing language-specific text."""
        if value is CapabilitySupport.SUPPORTED:
            return "✅"
        if value is CapabilitySupport.UNSUPPORTED:
            return "➖"
        return "❔"

    def _review_placeholders(self, snapshot: VacuumSnapshot) -> dict[str, str]:
        """Build a readable review of capabilities, devices and entities."""
        capabilities = snapshot.capabilities.capabilities
        related = snapshot.related_entities
        devices = snapshot.related_devices
        return {
            "vacuum": self._entity_title(snapshot.vacuum_entity_id or ""),
            "primary_device": self._device_title(devices.primary),
            "related_devices": self._format_devices(devices.existing),
            "related_device_count": str(len(devices.existing)),
            "missing_devices": self._format_devices(devices.missing),
            "start": self._support_mark(capabilities["start"]),
            "stop": self._support_mark(capabilities["stop"]),
            "pause": self._support_mark(capabilities["pause"]),
            "return_home": self._support_mark(capabilities["return_home"]),
            "fan_mode": self._support_mark(capabilities["fan_mode"]),
            "room_cleaning": self._support_mark(capabilities["room_cleaning"]),
            "spot_cleaning": self._support_mark(capabilities["spot_cleaning"]),
            "map": self._support_mark(capabilities["map"]),
            "locate": self._support_mark(capabilities["locate"]),
            "send_command": self._support_mark(capabilities["send_command"]),
            "robot_state": self._support_mark(capabilities["robot_state"]),
            "zone_cleaning": self._support_mark(capabilities["zone_cleaning"]),
            "mop_water_mode": self._support_mark(capabilities["mop_water_mode"]),
            "repetitions": self._support_mark(capabilities["repetitions"]),
            "battery": self._format_entities(related.battery),
            "dock": self._format_entities(related.dock),
            "water": self._format_entities(related.water),
            "mop": self._format_entities(related.mop),
            "consumables": self._format_entities(related.consumables),
            "diagnostics": self._format_entities(related.diagnostics),
            "manual_entities": self._format_entities(related.manual),
            "clean_water_status": self._format_entities(
                [snapshot.dock_water.clean_water.entity_id]
                if snapshot.dock_water.clean_water.entity_id else []
            ),
            "dirty_water_status": self._format_entities(
                [snapshot.dock_water.dirty_water.entity_id]
                if snapshot.dock_water.dirty_water.entity_id else []
            ),
            "related_count": str(len(related.all_entity_ids)),
        }

class VacuumScheduleOptionsFlow(OptionsFlowWithReload):
    """Manage multiple schedule definitions."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_schedule_id: str | None = None
        self._pending_schedule_form: dict[str, Any] | None = None

    def _snapshot(self) -> VacuumSnapshot:
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None and getattr(runtime, "snapshot", None) is not None:
            return runtime.snapshot
        return discover_vacuum_snapshot(
            self.hass,
            self.config_entry.data.get(CONF_VACUUM_ENTITY_ID),
            self.config_entry.data.get(CONF_RELATED_DEVICE_IDS, ()),
            self.config_entry.data.get(CONF_RELATED_ENTITIES, ()),
            self.config_entry.data.get(CONF_CLEAN_WATER_STATUS_ENTITY_ID),
            self.config_entry.data.get(CONF_DIRTY_WATER_STATUS_ENTITY_ID),
        )

    @property
    def _vacuum_entity_id(self) -> str:
        return str(self.config_entry.data.get(CONF_VACUUM_ENTITY_ID, ""))

    def _schedules(self) -> list[ScheduleDefinition]:
        result: list[ScheduleDefinition] = []
        for item in self.config_entry.options.get(CONF_SCHEDULES, []):
            try:
                result.append(ScheduleDefinition.from_dict(item))
            except (ScheduleValidationError, TypeError, ValueError):
                continue
        return result

    def _save(self, schedules: list[ScheduleDefinition]) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        options[CONF_SCHEDULES] = [item.to_dict() for item in schedules]
        return self.async_create_entry(data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show schedule management actions."""
        menu = ["add_schedule"]
        if self._schedules():
            menu.extend(["edit_schedule", "delete_schedule"])
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect schedule calendar and cleaning details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _dates_from_text(str(user_input.get(CONF_DATES, "")))
            except ValueError:
                errors["base"] = "invalid_schedule"
            else:
                self._pending_schedule_form = dict(user_input)
                return await self.async_step_add_schedule_targets()
        return self.async_show_form(
            step_id="add_schedule",
            data_schema=_schedule_details_schema(self.hass, self._snapshot()),
            errors=errors,
        )

    async def async_step_add_schedule_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select room/area or advanced robot targets for a new schedule."""
        if self._pending_schedule_form is None:
            return self.async_abort(reason="schedule_not_found")
        target_type = str(self._pending_schedule_form.get(CONF_TARGET_TYPE, TARGET_TYPE_CLEANING_ZONES))
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                targets = _targets_from_form(target_type, user_input[CONF_TARGETS])
                schedule = _schedule_from_form(self._pending_schedule_form, targets)
                _validate_zone_execution_compatibility(
                    schedule,
                    cleaning_zones_from_options(self.config_entry.options.get(CONF_CLEANING_ZONES, [])),
                )
            except (ScheduleValidationError, ValueError):
                errors["base"] = "invalid_schedule"
            else:
                schedules = self._schedules()
                schedules.append(schedule)
                return self._save(schedules)
        data_schema, segments_available = await _schedule_targets_schema(
            self.hass, self._vacuum_entity_id, target_type,
            cleaning_zones=cleaning_zones_from_options(self.config_entry.options.get(CONF_CLEANING_ZONES, [])),
        )
        if target_type == TARGET_TYPE_CLEANING_ZONES and not segments_available:
            errors.setdefault("base", "cleaning_zones_unavailable")
        elif target_type == "segments" and not segments_available:
            errors.setdefault("base", "robot_segments_unavailable")
        return self.async_show_form(
            step_id="add_schedule_targets",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"target_type": target_type},
        )

    async def async_step_edit_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a schedule to edit."""
        schedules = self._schedules()
        if not schedules:
            return self.async_abort(reason="no_schedules")
        if user_input is not None:
            self._selected_schedule_id = str(user_input[CONF_SCHEDULE_ID])
            return await self.async_step_edit_schedule_details()
        return self.async_show_form(
            step_id="edit_schedule",
            data_schema=_schedule_select_schema(schedules),
        )

    async def async_step_edit_schedule_details(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Edit calendar/cleaning details before selecting targets."""
        schedules = self._schedules()
        current = next(
            (
                item
                for item in schedules
                if item.schedule_id == self._selected_schedule_id
            ),
            None,
        )
        if current is None:
            return self.async_abort(reason="schedule_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _dates_from_text(str(user_input.get(CONF_DATES, "")))
            except ValueError:
                errors["base"] = "invalid_schedule"
            else:
                self._pending_schedule_form = dict(user_input)
                return await self.async_step_edit_schedule_targets()

        return self.async_show_form(
            step_id="edit_schedule_details",
            data_schema=_schedule_details_schema(self.hass, self._snapshot(), _schedule_to_form(current)),
            errors=errors,
            description_placeholders={
                "schedule_name": current.name,
                "revision": str(current.revision),
            },
        )

    async def async_step_edit_schedule_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit targets and save a new revision of the selected schedule."""
        schedules = self._schedules()
        current = next(
            (
                item
                for item in schedules
                if item.schedule_id == self._selected_schedule_id
            ),
            None,
        )
        if current is None or self._pending_schedule_form is None:
            return self.async_abort(reason="schedule_not_found")
        target_type = str(self._pending_schedule_form.get(CONF_TARGET_TYPE, TARGET_TYPE_CLEANING_ZONES))
        default_targets = current.targets if target_type == current.target_type else ()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                targets = _targets_from_form(target_type, user_input[CONF_TARGETS])
                revised = _schedule_from_form(
                    self._pending_schedule_form, targets, current
                )
                _validate_zone_execution_compatibility(
                    revised,
                    cleaning_zones_from_options(self.config_entry.options.get(CONF_CLEANING_ZONES, [])),
                )
            except (ScheduleValidationError, ValueError):
                errors["base"] = "invalid_schedule"
            else:
                updated = [
                    revised if item.schedule_id == current.schedule_id else item
                    for item in schedules
                ]
                return self._save(updated)
        data_schema, segments_available = await _schedule_targets_schema(
            self.hass, self._vacuum_entity_id, target_type, default_targets,
            cleaning_zones=cleaning_zones_from_options(self.config_entry.options.get(CONF_CLEANING_ZONES, [])),
        )
        if target_type == TARGET_TYPE_CLEANING_ZONES and not segments_available:
            errors.setdefault("base", "cleaning_zones_unavailable")
        elif target_type == "segments" and not segments_available:
            errors.setdefault("base", "robot_segments_unavailable")
        return self.async_show_form(
            step_id="edit_schedule_targets",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "schedule_name": current.name,
                "target_type": target_type,
            },
        )

    async def async_step_delete_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a schedule to delete."""
        schedules = self._schedules()
        if not schedules:
            return self.async_abort(reason="no_schedules")
        if user_input is not None:
            self._selected_schedule_id = str(user_input[CONF_SCHEDULE_ID])
            return await self.async_step_delete_schedule_confirm()
        return self.async_show_form(
            step_id="delete_schedule",
            data_schema=_schedule_select_schema(schedules),
        )

    async def async_step_delete_schedule_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm schedule deletion."""
        schedules = self._schedules()
        current = next(
            (
                item
                for item in schedules
                if item.schedule_id == self._selected_schedule_id
            ),
            None,
        )
        if current is None:
            return self.async_abort(reason="schedule_not_found")
        if user_input is not None and user_input.get(CONF_CONFIRM):
            return self._save(
                [item for item in schedules if item.schedule_id != current.schedule_id]
            )
        return self.async_show_form(
            step_id="delete_schedule_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM, default=False): selector.BooleanSelector()}
            ),
            description_placeholders={"schedule_name": current.name},
        )
