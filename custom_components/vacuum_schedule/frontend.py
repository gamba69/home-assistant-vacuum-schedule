"""Native Home Assistant frontend panel for Vacuum Schedule schedules."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import zipfile
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .capabilities import discover_vacuum_snapshot
from .bindings import (
    BindingMode,
    CapabilityBinding,
    PreflightPolicy,
    SourceType,
    SupportStatus,
    bindings_from_options,
    bindings_to_options,
)
from .cleaning_zones import CleaningZone, RobotTargetType, cleaning_zones_from_options
from .config_flow import (
    CONF_CLEANING_MODE,
    CONF_CLEANING_ROUTE,
    CONF_DATES,
    CONF_ENABLED,
    CONF_EXECUTION_WINDOW_MINUTES,
    CONF_FAN_MODE,
    CONF_LOCAL_TIME,
    CONF_MINIMUM_BATTERY_PERCENT,
    CONF_MOP_MODE,
    CONF_NAME,
    CONF_PASSES,
    CONF_PREWARNING_MINUTES,
    CONF_ZONE_EXECUTION_POLICY,
    CONF_TARGET_TYPE,
    CONF_WATER_MODE,
    CONF_WEEKDAYS,
    _CONTROL_NONE,
    _async_robot_segment_options,
    _device_control_options,
    _schedule_from_form,
    _schedule_to_form,
)
from .const import (
    CONF_CAPABILITY_BINDINGS,
    CONF_CLEAN_WATER_STATUS_ENTITY_ID,
    CONF_DIRTY_WATER_STATUS_ENTITY_ID,
    CONF_RELATED_DEVICE_IDS,
    CONF_RELATED_ENTITIES,
    CONF_CLEANING_ZONES,
    CONF_PREFLIGHT_POLICY,
    CONF_NOTIFICATION_SETTINGS,
    CONF_INTERFACE_LANGUAGE,
    CONF_EXECUTION_MODE,
    CONF_DRY_RUN_SETTINGS,
    CONF_SCHEDULES,
    CONF_WEEKDAY_OVERRIDES,
    CONF_WEEKDAY_TIMES,
    CONF_FORCE_ENABLED,
    CONF_FORCE_MAX_ADVANCE_MINUTES,
    CONF_FORCE_PRIORITY,
    CONF_FORCE_PREEMPTS_SCHEDULED,
    CONF_FORCE_CONDITION_GROUPS,
    CONF_VACUUM_ENTITY_ID,
    DEFAULT_EXECUTION_WINDOW_MINUTES,
    DEFAULT_PASSES,
    DEFAULT_PREWARNING_MINUTES,
    DEFAULT_ZONE_EXECUTION_POLICY,
    DOMAIN,
    INDEX_TO_WEEKDAY,
    SIGNAL_JOB_UPDATED,
    SIGNAL_SCHEDULER_UPDATED,
    TARGET_TYPE_CLEANING_ZONES,
    SUPPORTED_INTERFACE_LANGUAGES,
    SUPPORTED_PRESENTATION_LANGUAGES,
    VERSION,
    ZONE_EXECUTION_POLICY_COMBINED,
)
from .planner import OccurrencePlanner
from .schedule import ScheduleDefinition, ScheduleValidationError
from .notification_models import NotificationEventType, normalize_notification_settings

PANEL_COMPONENT = "vacuum-schedule-panel-0132"
# Legacy browser-cache component name: vacuum-schedule-panel-0635
PANEL_URL_PATH = "vacuum-schedule"
FRONTEND_BASE_URL = "/vacuum_schedule_frontend"
DATA_FRONTEND_STATIC_REGISTERED = f"{DOMAIN}_frontend_static_registered"



def _send_localizable_error(
    connection: ActiveConnection,
    message_id: int,
    code: str,
    detail: str | None = None,
) -> None:
    """Send a stable machine-readable error code to the presentation layer."""
    connection.send_error(message_id, code, detail or code)

def _domain_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _entry_schedules(entry: ConfigEntry) -> list[ScheduleDefinition]:
    schedules: list[ScheduleDefinition] = []
    for raw in entry.options.get(CONF_SCHEDULES, []):
        try:
            schedules.append(ScheduleDefinition.from_dict(raw))
        except (ScheduleValidationError, TypeError, ValueError):
            continue
    return schedules


def _entry_snapshot(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    runtime = getattr(entry, "runtime_data", None)
    snapshot = getattr(runtime, "snapshot", None)
    if snapshot is not None:
        return snapshot
    return discover_vacuum_snapshot(
        hass,
        entry.data.get(CONF_VACUUM_ENTITY_ID),
        entry.data.get(CONF_RELATED_DEVICE_IDS, ()),
        entry.data.get(CONF_RELATED_ENTITIES, ()),
        entry.data.get(CONF_CLEAN_WATER_STATUS_ENTITY_ID),
        entry.data.get(CONF_DIRTY_WATER_STATUS_ENTITY_ID),
    )


def _serialize_form(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    local_time = result.get(CONF_LOCAL_TIME)
    if hasattr(local_time, "isoformat"):
        result[CONF_LOCAL_TIME] = local_time.isoformat(timespec="minutes")
    result[CONF_PASSES] = int(result.get(CONF_PASSES, DEFAULT_PASSES))
    result[CONF_ZONE_EXECUTION_POLICY] = str(
        result.get(CONF_ZONE_EXECUTION_POLICY, DEFAULT_ZONE_EXECUTION_POLICY)
    )
    return result


def _new_form() -> dict[str, Any]:
    return {
        CONF_NAME: "",
        CONF_ENABLED: True,
        CONF_WEEKDAYS: [],
        CONF_DATES: "",
        CONF_LOCAL_TIME: "09:00",
        CONF_WEEKDAY_TIMES: {},
        CONF_WEEKDAY_OVERRIDES: {},
        CONF_FORCE_ENABLED: False,
        CONF_FORCE_MAX_ADVANCE_MINUTES: 0,
        CONF_FORCE_PRIORITY: 0,
        CONF_FORCE_PREEMPTS_SCHEDULED: False,
        CONF_FORCE_CONDITION_GROUPS: [],
        "force_condition_group_names": [],
        CONF_TARGET_TYPE: TARGET_TYPE_CLEANING_ZONES,
        "targets": [],
        CONF_CLEANING_MODE: _CONTROL_NONE,
        CONF_CLEANING_ROUTE: _CONTROL_NONE,
        CONF_MOP_MODE: _CONTROL_NONE,
        CONF_FAN_MODE: "",
        CONF_WATER_MODE: _CONTROL_NONE,
        CONF_PASSES: DEFAULT_PASSES,
        CONF_ZONE_EXECUTION_POLICY: DEFAULT_ZONE_EXECUTION_POLICY,
        CONF_PREWARNING_MINUTES: DEFAULT_PREWARNING_MINUTES,
        CONF_EXECUTION_WINDOW_MINUTES: DEFAULT_EXECUTION_WINDOW_MINUTES,
        CONF_MINIMUM_BATTERY_PERCENT: "",
        "minimum_start_window_minutes": "",
        "notification_policy": {},
    }


def _target_label_map(
    schedule: ScheduleDefinition, zone_names: dict[str, str]
) -> list[str]:
    if schedule.target_type == TARGET_TYPE_CLEANING_ZONES:
        return [zone_names.get(value, value) for value in schedule.targets]
    return list(schedule.targets)


def _cleaning_summary(schedule: ScheduleDefinition) -> dict[str, Any]:
    params = dict(schedule.cleaning_params)
    return {
        "fan_mode": params.get(CONF_FAN_MODE) or None,
        "cleaning_mode": params.get(CONF_CLEANING_MODE) or None,
        "cleaning_route": params.get(CONF_CLEANING_ROUTE) or None,
        "mop_mode": params.get(CONF_MOP_MODE) or None,
        "water_mode": params.get(CONF_WATER_MODE) or None,
        "passes": int(params.get(CONF_PASSES, DEFAULT_PASSES)),
        "minimum_battery_percent": params.get(CONF_MINIMUM_BATTERY_PERCENT),
        "minimum_start_window_minutes": params.get("minimum_start_window_minutes"),
    }


async def _async_enrich_job_display_data(
    hass: HomeAssistant, entry: ConfigEntry, payload: dict[str, Any]
) -> dict[str, Any]:
    """Add CleaningZone names and physical-target labels to job payloads."""
    zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
    zone_names = {zone.zone_id: zone.name for zone in zones}
    zone_by_id = {zone.zone_id: zone for zone in zones}
    for collection_name in ("active_jobs", "history"):
        for job in payload.get(collection_name, []):
            targets = [str(item) for item in job.get("targets", [])]
            labels = [zone_names.get(value, value) for value in targets]
            job["targets_display"] = labels
            for zone_id, zone_run in (job.get("zone_runs") or {}).items():
                zone = zone_by_id.get(str(zone_id))
                if zone is not None:
                    zone_run.setdefault("zone_name", zone.name)
                    zone_run.setdefault("robot_target_type", zone.robot_target_type.value)
                    zone_run.setdefault("robot_target_id", zone.robot_target_id)

            live = job.get("live")
            if isinstance(live, dict):
                live_zone_ids = [
                    str(value)
                    for value in (live.get("current_zone_ids") or [])
                    if str(value)
                ]
                if not live_zone_ids and live.get("current_zone_id"):
                    live_zone_ids = [str(live["current_zone_id"])]
                if live_zone_ids:
                    live_zone_names: list[str] = []
                    for zone_id in live_zone_ids:
                        zone_run = (job.get("zone_runs") or {}).get(zone_id) or {}
                        name = str(zone_run.get("zone_name") or zone_names.get(zone_id) or zone_id).strip()
                        if name and name not in live_zone_names:
                            live_zone_names.append(name)
                    live["current_zone_ids"] = live_zone_ids
                    live["current_zone_names"] = live_zone_names
                    if len(live_zone_ids) == 1:
                        live["current_zone_id"] = live_zone_ids[0]
                    if len(live_zone_names) == 1:
                        live["current_zone_name"] = live_zone_names[0]
            for action in job.get("user_action_history", []) or []:
                if not isinstance(action, dict):
                    continue
                if action.get("actor_name"):
                    action["actor_display"] = str(action["actor_name"])
                    continue
                user_id = str(action.get("user_id") or "").strip()
                if user_id:
                    try:
                        user = await hass.auth.async_get_user(user_id)
                    except Exception:
                        user = None
                    action["actor_display"] = str(getattr(user, "name", "") or user_id)
                elif action.get("recipient_id"):
                    action["actor_display"] = str(action.get("recipient_id"))
    return payload


async def _async_entry_editor_payload(
    hass: HomeAssistant, entry: ConfigEntry, schedule_id: str | None = None,
) -> dict[str, Any]:
    snapshot = _entry_snapshot(hass, entry)
    vacuum_entity_id = str(entry.data.get(CONF_VACUUM_ENTITY_ID, ""))
    zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
    controls = _device_control_options(hass, snapshot)
    schedules = _entry_schedules(entry)
    current = next((item for item in schedules if item.schedule_id == schedule_id), None)
    form = _new_form() if current is None else _serialize_form(_schedule_to_form(current))
    form[CONF_TARGET_TYPE] = TARGET_TYPE_CLEANING_ZONES
    form["targets"] = list(current.targets) if current is not None else []
    return {
        "entry_id": entry.entry_id, "entry_title": entry.title,
        "vacuum_entity_id": vacuum_entity_id,
        "schedule_id": current.schedule_id if current is not None else None,
        "revision": current.revision if current is not None else None,
        "form": form,
        "cleaning_zones": [
            {"value": zone.zone_id, "label": zone.name, "robot_target_type": zone.robot_target_type.value, "robot_target_id": zone.robot_target_id}
            for zone in zones
        ],
        "fan_modes": list(snapshot.capabilities.fan_speed_list), "controls": controls,
    }


async def _async_list_payload(hass: HomeAssistant) -> dict[str, Any]:
    now = dt_util.now()
    planner = OccurrencePlanner(hass.config.time_zone)
    result_entries: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        schedules = _entry_schedules(entry)
        zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
        zone_names = {zone.zone_id: zone.name for zone in zones}
        rows: list[dict[str, Any]] = []
        for schedule in schedules:
            next_occurrence = planner.next_occurrence([schedule], now, inclusive=True)
            rows.append({
                **schedule.to_dict(),
                "weekdays_codes": [INDEX_TO_WEEKDAY[index] for index in schedule.weekdays],
                "weekday_times_codes": {
                    INDEX_TO_WEEKDAY[index]: value.isoformat(timespec="minutes")
                    for index, value in sorted(schedule.weekday_times.items())
                },
                "weekday_overrides_codes": {
                    INDEX_TO_WEEKDAY[index]: dict(values)
                    for index, values in sorted(schedule.weekday_overrides.items())
                },
                "targets_display": _target_label_map(schedule, zone_names),
                "cleaning_summary": _cleaning_summary(schedule),
                "next_run": next_occurrence.planned_start.isoformat() if next_occurrence else None,
            })
        result_entries.append({"entry_id": entry.entry_id, "title": entry.title, "schedules": rows})
    return {"version": VERSION, "entries": result_entries}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/schedules/list"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_list_schedules(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return all configured schedule definitions for the panel."""
    connection.send_result(msg["id"], await _async_list_payload(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedules/editor",
        vol.Required("entry_id"): str,
        vol.Optional("schedule_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_schedule_editor(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return one schedule plus current device-backed editor choices."""
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    schedule_id = msg.get("schedule_id")
    if schedule_id and not any(
        item.schedule_id == schedule_id for item in _entry_schedules(entry)
    ):
        _send_localizable_error(connection, msg["id"], "schedule_not_found")
        return
    connection.send_result(
        msg["id"], await _async_entry_editor_payload(hass, entry, schedule_id)
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedules/save",
        vol.Required("entry_id"): str,
        vol.Optional("schedule_id"): str,
        vol.Required("schedule"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_save_schedule(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create or revise a ScheduleDefinition and reload the config entry."""
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return

    schedules = _entry_schedules(entry)
    schedule_id = msg.get("schedule_id")
    previous = None
    if schedule_id:
        previous = next(
            (item for item in schedules if item.schedule_id == schedule_id), None
        )
        if previous is None:
            _send_localizable_error(connection, msg["id"], "schedule_not_found")
            return

    form = dict(msg["schedule"])
    targets_raw = form.pop("targets", [])
    targets = [str(item).strip() for item in targets_raw if str(item).strip()]
    try:
        schedule = _schedule_from_form(form, targets, previous)
    except (ScheduleValidationError, TypeError, ValueError, KeyError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_schedule", str(err))
        return

    if schedule.zone_execution_policy == ZONE_EXECUTION_POLICY_COMBINED:
        zone_by_id = {
            zone.zone_id: zone
            for zone in cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
        }
        target_types = {
            zone_by_id[zone_id].robot_target_type.value
            for zone_id in schedule.targets
            if zone_id in zone_by_id
        }
        if len(target_types) > 1:
            _send_localizable_error(
                connection, msg["id"], "combined_execution_mixed_target_types"
            )
            return

    if previous is None:
        schedules.append(schedule)
    else:
        schedules = [
            schedule if item.schedule_id == previous.schedule_id else item
            for item in schedules
        ]

    options = dict(entry.options)
    options[CONF_SCHEDULES] = [item.to_dict() for item in schedules]
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(
        msg["id"],
        {
            "schedule_id": schedule.schedule_id,
            "revision": schedule.revision,
            "saved": True,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedules/delete",
        vol.Required("entry_id"): str,
        vol.Required("schedule_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_delete_schedule(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete one schedule definition and reload its config entry."""
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    schedules = _entry_schedules(entry)
    if not any(item.schedule_id == msg["schedule_id"] for item in schedules):
        _send_localizable_error(connection, msg["id"], "schedule_not_found")
        return

    options = dict(entry.options)
    options[CONF_SCHEDULES] = [
        item.to_dict()
        for item in schedules
        if item.schedule_id != msg["schedule_id"]
    ]
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"deleted": True})


def _entry_scheduler(hass: HomeAssistant, entry_id: str):
    entry = _domain_entry(hass, entry_id)
    if entry is None:
        return None
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "scheduler", None)



def _entity_choices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return current HA entity choices with stable registry identifiers."""
    values: list[dict[str, Any]] = []
    registry = er.async_get(hass)
    for state in hass.states.async_all():
        registry_entry = registry.async_get(state.entity_id)
        values.append(
            {
                "entity_id": state.entity_id,
                "entity_registry_id": getattr(registry_entry, "id", None),
                "name": state.name,
                "state": state.state,
                "domain": state.entity_id.split(".", 1)[0],
                "device_class": state.attributes.get("device_class"),
                "unit": state.attributes.get("unit_of_measurement"),
            }
        )
    return sorted(values, key=lambda item: (str(item["name"]).casefold(), item["entity_id"]))


def _registry_id_for_entity(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    item = er.async_get(hass).async_get(str(entity_id))
    if item is None:
        return None
    value = str(getattr(item, "id", "") or "")
    return value or None


def _entity_id_for_registry_id(
    hass: HomeAssistant, registry_id: str | None, fallback: str | None = None
) -> str | None:
    if registry_id:
        registry = er.async_get(hass)
        entities = getattr(registry, "entities", {})
        for item in getattr(entities, "values", lambda: ())():
            if str(getattr(item, "id", "")) == str(registry_id):
                current = str(getattr(item, "entity_id", "") or "")
                if current:
                    return current
    return fallback


def _enrich_zone_registry_ids(hass: HomeAssistant, raw_zone: dict[str, Any]) -> dict[str, Any]:
    """Attach stable registry IDs to all configured room/path entity conditions."""
    room = dict(raw_zone)
    for key in ("busy_sources",):
        enriched: list[dict[str, Any]] = []
        for value in room.get(key, []) or []:
            item = dict(value)
            item["entity_registry_id"] = _registry_id_for_entity(hass, item.get("entity_id"))
            enriched.append(item)
        room[key] = enriched
    paths: list[dict[str, Any]] = []
    for value in room.get("access_paths", []) or []:
        path = dict(value)
        conditions: list[dict[str, Any]] = []
        for condition in path.get("conditions", []) or []:
            item = dict(condition)
            item["entity_registry_id"] = _registry_id_for_entity(hass, item.get("entity_id"))
            conditions.append(item)
        path["conditions"] = conditions
        paths.append(path)
    room["access_paths"] = paths
    return room


async def _async_settings_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    scheduler = _entry_scheduler(hass, entry.entry_id)
    if scheduler is None:
        raise ValueError("scheduler_not_loaded")
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        runtime.refresh()
    snapshot = scheduler.input_provider.executor.snapshot()
    inputs = scheduler.input_provider.snapshot(scheduler.clock.now())
    bindings = bindings_from_options(entry.options.get(CONF_CAPABILITY_BINDINGS, {}))

    binding_rows: list[dict[str, Any]] = []
    flat = inputs.flat()
    capability_support = snapshot.capabilities.as_dict()
    for name in capability_support.get("capabilities", {}):
        bindings.setdefault(f"capability.{name}", CapabilityBinding(key=f"capability.{name}"))

    for key, binding in bindings.items():
        item = flat.get(key)
        if key.startswith("capability."):
            name = key.split(".", 1)[1]
            support = capability_support.get("capabilities", {}).get(name, "unknown")
            sources = capability_support.get("sources", {}).get(name, [])
            auto = {
                "entity_id": None,
                "attribute": None,
                "raw": support,
                "available": support == "supported",
                "candidate": sources[0] if sources else None,
                "sources": sources,
                "note": None if support == "supported" else support,
            }
        else:
            auto = scheduler.input_provider.auto_candidate_info(key)
        if binding.binding_mode is BindingMode.DISABLED:
            effective_status = SupportStatus.DISABLED.value
        elif binding.binding_mode is BindingMode.MANUAL:
            resolved_manual = _entity_id_for_registry_id(
                hass, binding.entity_registry_id, binding.entity_id
            )
            manual_ok = bool(resolved_manual and hass.states.get(resolved_manual) is not None)
            effective_status = (
                SupportStatus.MANUAL.value
                if manual_ok and (item is None or item.status == "ready")
                else SupportStatus.UNVERIFIED.value
            )
        elif key.startswith("capability."):
            effective_status = (
                SupportStatus.DETECTED.value if auto.get("available") else SupportStatus.UNSUPPORTED.value
            )
        else:
            effective_status = (
                SupportStatus.DETECTED.value
                if (auto.get("candidate") or (item is not None and item.status == "ready"))
                and (item is None or item.status == "ready")
                else SupportStatus.UNVERIFIED.value
            )
        row = binding.to_dict()
        row["resolved_entity_id"] = _entity_id_for_registry_id(
            hass, binding.entity_registry_id, binding.entity_id
        )
        inferred_source = row["resolved_entity_id"] if binding.binding_mode is BindingMode.MANUAL else auto.get("entity_id")
        row["inferred_normal_state"] = (
            scheduler.input_provider._infer_binary_normal_state(key, inferred_source)
            if key in {"dock.clean_water", "dock.dirty_water", "dock.detergent", "mop.attached"}
            else None
        )
        row["support_status"] = effective_status
        row["effective_input"] = item.to_dict() if item is not None else None
        row["auto_candidate"] = auto
        binding_rows.append(row)

    vacuum_entity_id = str(entry.data.get(CONF_VACUUM_ENTITY_ID, ""))
    try:
        segments = await _async_robot_segment_options(hass, vacuum_entity_id)
    except Exception:
        segments = []

    device_registry = dr.async_get(hass)
    related_devices: list[dict[str, Any]] = []
    for device_id in snapshot.related_devices.all_existing_device_ids:
        device = device_registry.async_get(device_id)
        related_devices.append(
            {
                "device_id": device_id,
                "name": getattr(device, "name_by_user", None) or getattr(device, "name", None) or device_id,
                "is_primary": device_id == snapshot.related_devices.primary,
            }
        )

    entity_registry = er.async_get(hass)
    vacuum_registry = entity_registry.async_get(vacuum_entity_id) if vacuum_entity_id else None
    vacuum_state = hass.states.get(vacuum_entity_id) if vacuum_entity_id else None
    vacuum_name = vacuum_state.name if vacuum_state is not None else vacuum_entity_id

    zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
    zone_payload: list[dict[str, Any]] = []
    segment_names = {item["value"]: item["label"] for item in segments}
    for zone in zones:
        data = zone.to_dict()
        for condition in data.get("busy_sources", []):
            condition["entity_id"] = _entity_id_for_registry_id(
                hass, condition.get("entity_registry_id"), condition.get("entity_id")
            ) or condition.get("entity_id")
        for path in data.get("access_paths", []):
            for condition in path.get("conditions", []):
                condition["entity_id"] = _entity_id_for_registry_id(
                    hass, condition.get("entity_registry_id"), condition.get("entity_id")
                ) or condition.get("entity_id")
        zone_input = inputs.zones.get(zone.zone_id)
        data["live"] = zone_input.to_dict() if zone_input is not None else None
        data["target_name"] = (segment_names.get(zone.robot_target_id, zone.robot_target_id) if zone.robot_target_type is RobotTargetType.SEGMENT else zone.robot_target_id)
        zone_payload.append(data)

    validation = scheduler.preflight.validation_issues()
    try:
        real_readiness = await scheduler.async_real_execution_readiness()
    except Exception as err:
        real_readiness = {
            "ready": False,
            "vendor": getattr(scheduler.execution.real.adapter, "vendor", "generic"),
            "checked_at": scheduler.clock.now().isoformat(),
            "issues": [{"code": "real_readiness_failed", "severity": "error", "detail": f"{type(err).__name__}: {err}"}],
        }
    try:
        real_runtime_status = await scheduler.async_real_execution_runtime_status()
    except Exception as err:
        real_runtime_status = {
            "available": False,
            "can_start_now": None,
            "checked_at": scheduler.clock.now().isoformat(),
            "blockers": [],
            "error": f"{type(err).__name__}: {err}",
        }
    ready_count = sum(1 for item in binding_rows if item["support_status"] in {"detected", "manual"})
    attention_count = sum(1 for item in binding_rows if item["support_status"] in {"unverified", "conflict"})
    # Global attention is intentionally a small payload independent of the
    # Statistics tab. Pending tank-service confirmation must stay visible on
    # every panel view until the user resolves it.
    water_attention = scheduler.statistics.water.payload()
    data_management = await scheduler.statistics.async_data_management_status(scheduler.store)
    return {
        "version": VERSION,
        "scheduler_now": scheduler.clock.now().isoformat(),
        "entry_id": entry.entry_id,
        "entry_title": entry.title,
        "vacuum": {
            "entity_id": vacuum_entity_id,
            "name": vacuum_name,
            "registry_id": getattr(vacuum_registry, "id", None),
            "state": snapshot.raw_state,
            "normalized_state": snapshot.normalized_state.value,
            "available": snapshot.available,
        },
        "related_devices": related_devices,
        "capabilities": snapshot.capabilities.as_dict(),
        "bindings": binding_rows,
        "policy": scheduler.input_provider.policy.to_dict(),
        "cleaning_zones": zone_payload,
        "segments": segments,
        "entities": _entity_choices(hass),
        "inputs": inputs.to_dict(),
        "execution": {
            "mode": scheduler.execution_mode.value,
            "dry_run": scheduler.dry_run_settings,
            "real": scheduler.real_execution_settings,
            "readiness": real_readiness,
            "runtime_status": real_runtime_status,
        },
        "overrides": scheduler.overrides.as_dict(),
        "attention": {
            "pending_tank_service": list(water_attention.get("pending_service", [])),
            "maintenance": dict(water_attention.get("maintenance") or {}),
        },
        "data_management": data_management,
        "interface_language": str(entry.options.get(CONF_INTERFACE_LANGUAGE, "auto") or "auto"),
        "validation": validation,
        "summary": {
            "capabilities_ready": ready_count,
            "capabilities_attention": attention_count,
            "cleaning_zones": len(zones),
            "validation_errors": sum(item.get("severity") == "error" for item in validation),
            "validation_warnings": sum(item.get("severity") == "warning" for item in validation),
        },
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_interface_language",
        vol.Required("entry_id"): str,
        vol.Required("language"): vol.In(list(SUPPORTED_INTERFACE_LANGUAGES)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_interface_language(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Persist the custom panel presentation-language override."""
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    options = dict(entry.options)
    options[CONF_INTERFACE_LANGUAGE] = str(msg["language"])
    await _async_update_options(hass, entry, options)
    connection.send_result(
        msg["id"],
        {"saved": True, "interface_language": options[CONF_INTERFACE_LANGUAGE]},
    )


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry, options: dict[str, Any]) -> None:
    hass.config_entries.async_update_entry(entry, options=options)
    scheduler = _entry_scheduler(hass, entry.entry_id)
    if scheduler is not None:
        await scheduler.async_settings_changed()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/get",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    try:
        payload = await _async_settings_payload(hass, entry)
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "settings_unavailable", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/preview_execution_mode",
        vol.Required("entry_id"): str,
        vol.Required("mode"): vol.In(["REAL", "DRY_RUN"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_preview_execution_mode(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        preview = scheduler.execution_mode_change_preview(str(msg["mode"]))
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_execution_mode", str(err))
        return
    if str(msg["mode"]).upper() == "REAL":
        runtime_status = await scheduler.async_real_execution_runtime_status()
        preview["runtime_status"] = runtime_status
        preview["readiness"] = await scheduler.async_real_execution_readiness()
        # The legacy immediate-start hint is purely temporal. Suppress it when
        # authoritative current pre-flight says that this occurrence cannot
        # actually start now, otherwise the confirmation dialog would present
        # contradictory warnings.
        if runtime_status.get("available") and runtime_status.get("can_start_now") is False:
            preview["may_start_immediately"] = False
    connection.send_result(msg["id"], preview)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_execution_mode",
        vol.Required("entry_id"): str,
        vol.Required("mode"): vol.In(["REAL", "DRY_RUN"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_execution_mode(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        result = await scheduler.async_set_execution_mode(str(msg["mode"]))
    except ValueError as err:
        detail = str(err)
        code = "real_execution_not_ready" if detail.startswith("real_execution_not_ready") else "invalid_execution_mode"
        _send_localizable_error(connection, msg["id"], code, detail)
        return
    connection.send_result(
        msg["id"],
        {"saved": True, "mode": str(msg["mode"]), "mode_change": result, "status": scheduler.status_payload()},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_dry_run",
        vol.Required("entry_id"): str,
        vol.Required("settings"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_dry_run(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        settings = await scheduler.async_update_dry_run_settings(dict(msg["settings"]))
    except (ValueError, TypeError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_dry_run_settings", str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "settings": settings, "status": scheduler.status_payload()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_real_execution",
        vol.Required("entry_id"): str,
        vol.Required("settings"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_real_execution(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        settings = await scheduler.async_update_real_execution_settings(dict(msg["settings"]))
    except (ValueError, TypeError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_real_execution_settings", str(err))
        return
    connection.send_result(
        msg["id"],
        {
            "saved": True,
            "settings": settings,
            "readiness": await scheduler.async_real_execution_readiness(),
            "status": scheduler.status_payload(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_execution",
        vol.Required("entry_id"): str,
        vol.Required("mode"): vol.In(["REAL", "DRY_RUN"]),
        vol.Required("dry_run"): dict,
        vol.Required("real"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_execution(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Persist the complete execution-settings form in one transaction."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        execution = await scheduler.async_update_execution_settings(
            mode=str(msg["mode"]),
            dry_run=dict(msg["dry_run"]),
            real=dict(msg["real"]),
        )
    except (ValueError, TypeError) as err:
        detail = str(err)
        if detail.startswith("real_execution_not_ready"):
            code = "real_execution_not_ready"
        elif detail.startswith("invalid_execution_mode"):
            code = "invalid_execution_mode"
        elif detail.startswith("invalid_dry_run_duration"):
            code = "invalid_dry_run_settings"
        else:
            code = "invalid_execution_settings"
        _send_localizable_error(connection, msg["id"], code, detail)
        return
    connection.send_result(
        msg["id"],
        {
            "saved": True,
            "execution": execution,
            "readiness": await scheduler.async_real_execution_readiness(),
            "status": scheduler.status_payload(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_policy",
        vol.Required("entry_id"): str,
        vol.Required("policy"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_policy(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    user_id = str(getattr(getattr(connection, "user", None), "id", "") or "") or None
    try:
        policy = await scheduler.async_update_preflight_policy(
            dict(msg["policy"]), source="frontend", user_id=user_id
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_preflight_policy", str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "policy": policy.to_dict()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_binding",
        vol.Required("entry_id"): str,
        vol.Required("key"): str,
        vol.Required("binding"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_binding(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    key = str(msg["key"])
    raw = dict(msg["binding"])
    raw["key"] = key
    mode = str(raw.get("binding_mode", "auto"))
    if mode == BindingMode.MANUAL.value:
        raw["entity_registry_id"] = _registry_id_for_entity(hass, raw.get("entity_id"))
    else:
        raw["entity_registry_id"] = None
    raw["source_type"] = (
        SourceType.ENTITY_ATTRIBUTE.value
        if mode == BindingMode.MANUAL.value and raw.get("attribute")
        else SourceType.ENTITY_STATE.value
        if mode == BindingMode.MANUAL.value
        else SourceType.DERIVED.value
    )
    try:
        binding = CapabilityBinding.from_dict(key, raw)
    except (ValueError, TypeError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_binding", str(err))
        return
    bindings = bindings_from_options(entry.options.get(CONF_CAPABILITY_BINDINGS, {}))
    bindings[key] = binding
    options = dict(entry.options)
    options[CONF_CAPABILITY_BINDINGS] = bindings_to_options(bindings)
    await _async_update_options(hass, entry, options)
    connection.send_result(msg["id"], {"saved": True, "binding": binding.to_dict()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update_zone",
        vol.Required("entry_id"): str,
        vol.Required("zone"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_update_zone(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found"); return
    try:
        zone = CleaningZone.from_dict(_enrich_zone_registry_ids(hass, dict(msg["zone"])))
    except (ValueError, TypeError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_cleaning_zone", str(err)); return
    zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
    # 0.7: logical zones may share a physical target; execution planning
    # deduplicates it when both zones participate in the same attempt.
    updated = [zone if item.zone_id == zone.zone_id else item for item in zones]
    if not any(item.zone_id == zone.zone_id for item in zones): updated.append(zone)
    options = dict(entry.options); options[CONF_CLEANING_ZONES] = [item.to_dict() for item in updated]
    await _async_update_options(hass, entry, options)
    connection.send_result(msg["id"], {"saved": True, "zone": zone.to_dict()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/delete_zone",
        vol.Required("entry_id"): str,
        vol.Required("zone_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_delete_zone(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found"); return
    zone_id = str(msg["zone_id"])
    schedules = _entry_schedules(entry)
    if any(zone_id in schedule.targets for schedule in schedules):
        _send_localizable_error(connection, msg["id"], "cleaning_zone_in_use"); return
    zones = cleaning_zones_from_options(entry.options.get(CONF_CLEANING_ZONES, []))
    options = dict(entry.options); options[CONF_CLEANING_ZONES] = [item.to_dict() for item in zones if item.zone_id != zone_id]
    await _async_update_options(hass, entry, options)
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/settings/rescan", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_rescan(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    if entry is None:
        _send_localizable_error(connection, msg["id"], "entry_not_found")
        return
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        runtime.refresh()
    scheduler = _entry_scheduler(hass, entry.entry_id)
    if scheduler is not None:
        await scheduler.async_settings_changed()
    connection.send_result(msg["id"], await _async_settings_payload(hass, entry))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/settings/validate", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_settings_validate(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    connection.send_result(msg["id"], {"issues": scheduler.preflight.validation_issues()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preflight/test",
        vol.Required("entry_id"): str,
        vol.Optional("job_id"): str,
        vol.Optional("zone_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_preflight_test(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    zone_id = msg.get("zone_id")
    if zone_id:
        snapshot = scheduler.input_provider.snapshot(scheduler.clock.now())
        zone = snapshot.zones.get(zone_id)
        if zone is None:
            _send_localizable_error(connection, msg["id"], "cleaning_zone_not_found")
            return
        connection.send_result(msg["id"], {"zone": zone.to_dict()})
        return
    job_id = msg.get("job_id")
    job = scheduler.store.active.get(job_id) if job_id else next(iter(scheduler.store.active.values()), None)
    if job is None:
        _send_localizable_error(connection, msg["id"], "no_active_job")
        return
    report = scheduler.preflight.current_preview(job, scheduler.clock.now())
    connection.send_result(msg["id"], report.to_dict(include_snapshot=True))


_OVERRIDE_VALUE = vol.Any(None, bool, int, float, str, list, dict)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/testing/overrides/set",
        vol.Required("entry_id"): str,
        vol.Required("target"): str,
        vol.Required("mode"): str,
        vol.Optional("value"): _OVERRIDE_VALUE,
        vol.Optional("persistent", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_testing_override_set(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        item = await scheduler.async_set_override(
            msg["target"], msg["mode"], msg.get("value"), persistent=bool(msg.get("persistent"))
        )
    except (ValueError, TypeError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_override", str(err))
        return
    connection.send_result(msg["id"], {"override": item, "status": scheduler.status_payload()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/testing/overrides/clear",
        vol.Required("entry_id"): str,
        vol.Optional("target"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_testing_override_clear(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    await scheduler.async_clear_overrides(msg.get("target"))
    connection.send_result(msg["id"], {"cleared": True, "status": scheduler.status_payload()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/testing/preset",
        vol.Required("entry_id"): str,
        vol.Required("preset"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_testing_preset(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        await scheduler.async_apply_override_preset(msg["preset"])
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_preset", str(err))
        return
    connection.send_result(msg["id"], {"applied": True, "status": scheduler.status_payload()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/scheduler/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_scheduler_status(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return scheduler state for all loaded Vacuum Schedule entries."""
    entries: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        scheduler = _entry_scheduler(hass, entry.entry_id)
        if scheduler is None:
            continue
        payload = scheduler.status_payload()
        await _async_enrich_job_display_data(hass, entry, payload)
        payload["title"] = entry.title
        entries.append(payload)
    connection.send_result(msg["id"], {"version": VERSION, "entries": entries})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/scheduler/job_details",
    vol.Required("entry_id"): str,
    vol.Required("job_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_scheduler_job_details(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the full retained trace for one active or terminal job."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    entry = _domain_entry(hass, msg["entry_id"])
    if scheduler is None or entry is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    job = scheduler.job_details_payload(msg["job_id"])
    if job is None:
        _send_localizable_error(connection, msg["id"], "job_not_found")
        return
    wrapper = {"history": [job]}
    await _async_enrich_job_display_data(hass, entry, wrapper)
    wrapper["history"][0]["statistics_context"] = await scheduler.statistics.async_job_context(msg["job_id"])
    connection.send_result(msg["id"], wrapper["history"][0])


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/get",
    vol.Required("entry_id"): str,
    vol.Optional("delivery_id"): str,
    vol.Optional("event_id"): str,
    vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    entry = _domain_entry(hass, msg["entry_id"])
    if scheduler is None or entry is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return


    connection.send_result(
        msg["id"],
        scheduler.notifications.payload(
            msg.get("delivery_id"),
            msg.get("event_id"),
            language=msg.get("language"),
        ),
    )


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/detail",
    vol.Required("entry_id"): str,
    vol.Required("event_id"): str,
    vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_detail(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    message = scheduler.notifications.message_payload(
        msg["event_id"], language=msg.get("language")
    )
    if message is None:
        _send_localizable_error(connection, msg["id"], "notification_message_not_found")
        return
    job_id = message.get("job_id")
    job_context = scheduler.notification_job_context_payload(job_id) if job_id else None
    connection.send_result(msg["id"], {"message": message, "job": job_context})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/save",
    vol.Required("entry_id"): str,
    vol.Required("settings"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    entry = _domain_entry(hass, msg["entry_id"])
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if entry is None or scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        normalized = normalize_notification_settings(msg["settings"])
    except (TypeError, ValueError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_notification_settings", str(err))
        return
    options = dict(entry.options)
    options[CONF_NOTIFICATION_SETTINGS] = normalized
    hass.config_entries.async_update_entry(entry, options=options)
    scheduler._emit_scheduler()
    connection.send_result(msg["id"], {"saved": True, **scheduler.notifications.payload()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/test_channel",
    vol.Required("entry_id"): str,
    vol.Required("recipient_id"): str,
    vol.Required("channel_id"): str,
    vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_test_channel(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        result = await scheduler.notifications.async_test_channel(
            msg["recipient_id"], msg["channel_id"], language=msg.get("language")
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "notification_test_failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/test_event",
    vol.Required("entry_id"): str,
    vol.Required("event_type"): vol.In(["prewarning", "wait_enter", "wait_reminder", "started", "finished", "start_forecast", "test"]),
    vol.Optional("notification_class", default="info"): vol.In(["info", "attention", "error"]),
    vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_test_event(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        result = await scheduler.notifications.async_test_event(
            msg["event_type"], msg["notification_class"], language=msg.get("language")
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "notification_test_failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/notifications/clear_history",
    vol.Required("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_notifications_clear_history(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    scheduler.notifications.store.clear()
    await scheduler.notifications.store.async_save()
    connection.send_result(msg["id"], {"cleared": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/jobs/action",
    vol.Required("entry_id"): str,
    vol.Required("job_id"): str,
    vol.Required("action"): vol.In(["additional_run", "start_now", "start_now_ignore_busy", "skip", "cancel", "recheck", "pause", "resume"]),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_job_action(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    user_id = str(getattr(getattr(connection, "user", None), "id", "") or "") or None
    try:
        action = msg["action"]
        if action == "additional_run":
            job = await scheduler.async_run_job_additional(
                msg["job_id"], source="frontend", user_id=user_id
            )
        elif action in {"start_now", "start_now_ignore_busy"}:
            job = await scheduler.async_start_job_now(
                msg["job_id"],
                source="frontend",
                user_id=user_id,
                ignore_busy_zones=action == "start_now_ignore_busy",
            )
        elif action == "skip":
            job = await scheduler.async_skip_job(msg["job_id"], source="frontend", user_id=user_id)
        elif action == "cancel":
            job = await scheduler.async_cancel_job(msg["job_id"], source="frontend", user_id=user_id)
        elif action == "pause":
            job = await scheduler.async_pause_job(msg["job_id"], source="frontend", user_id=user_id)
        elif action == "resume":
            job = await scheduler.async_resume_job(msg["job_id"], source="frontend", user_id=user_id)
        else:
            job = await scheduler.async_recheck_job(msg["job_id"], source="frontend", user_id=user_id)
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_job_action", str(err))
        return
    connection.send_result(msg["id"], {"ok": True, "job": job.to_dict()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/schedules/run_now",
    vol.Required("entry_id"): str,
    vol.Required("schedule_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_schedule_run_now(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    user_id = str(getattr(getattr(connection, "user", None), "id", "") or "") or None
    try:
        job = await scheduler.async_run_schedule_now(msg["schedule_id"], source="frontend", user_id=user_id)
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_schedule_action", str(err))
        return
    connection.send_result(msg["id"], {"ok": True, "job": job.to_dict()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/schedules/set_enabled",
    vol.Required("entry_id"): str,
    vol.Required("schedule_id"): str,
    vol.Required("enabled"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_schedule_set_enabled(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    user_id = str(getattr(getattr(connection, "user", None), "id", "") or "") or None
    try:
        schedule = await scheduler.async_set_schedule_enabled(
            msg["schedule_id"], bool(msg["enabled"]), source="frontend", user_id=user_id
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_schedule_action", str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "schedule": schedule.to_dict()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/schedules/set_paused",
    vol.Required("entry_id"): str,
    vol.Required("schedule_id"): str,
    vol.Required("paused"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_schedule_set_paused(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    user_id = str(getattr(getattr(connection, "user", None), "id", "") or "") or None
    try:
        schedule = await scheduler.async_set_schedule_paused(
            msg["schedule_id"], bool(msg["paused"]), source="frontend", user_id=user_id
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_schedule_action", str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "schedule": schedule.to_dict()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/scheduler/reconcile_timeline",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_scheduler_reconcile_timeline(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Explicitly restore active Jobs from schedules and current HA time."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    now = await scheduler.async_reconcile_current_timeline()
    connection.send_result(
        msg["id"],
        {"reconciled": True, "now": now.isoformat(), "status": scheduler.status_payload()},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/scheduler/rebuild_schedule",
        vol.Required("entry_id"): str,
        vol.Optional("restore_early_executed", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_scheduler_rebuild_schedule(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Recreate the future schedule projection from current definitions."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    result = await scheduler.async_rebuild_schedule_projection(
        restore_early_executed=bool(msg.get("restore_early_executed", False))
    )
    connection.send_result(
        msg["id"],
        {
            "rebuilt": True,
            "now": result["now"].isoformat(),
            "removed_jobs": result["removed_jobs"],
            "created_jobs": result["created_jobs"],
            "restored_early_jobs": result["restored_early_jobs"],
            "status": scheduler.status_payload(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/debug/advance_time",
        vol.Required("entry_id"): str,
        vol.Required("seconds"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_debug_advance_time(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded"); return
    try:
        now = await scheduler.async_advance_time(int(msg["seconds"]))
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "dry_run_required", str(err)); return
    connection.send_result(msg["id"], {"now": now.isoformat(), "status": scheduler.status_payload()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/debug/next_transition", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_debug_next_transition(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded"); return
    try:
        now = await scheduler.async_advance_to_next_transition()
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "dry_run_required", str(err)); return
    connection.send_result(msg["id"], {"now": now.isoformat(), "status": scheduler.status_payload()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/debug/reset_time", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_debug_reset_time(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded"); return
    try:
        now = await scheduler.async_reset_time()
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "dry_run_required", str(err)); return
    connection.send_result(msg["id"], {"now": now.isoformat(), "status": scheduler.status_payload()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/debug/reset_dry_run", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_debug_reset_dry_run(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded"); return
    now = await scheduler.async_reset_dry_run()
    connection.send_result(msg["id"], {"now": now.isoformat(), "status": scheduler.status_payload()})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/debug/set_execution_fault", vol.Required("entry_id"): str, vol.Required("fault"): vol.Any(None, str)}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_debug_set_execution_fault(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded"); return
    try:
        await scheduler.async_set_execution_fault(msg.get("fault"))
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], "invalid_execution_fault", str(err)); return
    connection.send_result(msg["id"], {"saved": True, "status": scheduler.status_payload()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/scheduler/subscribe"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_scheduler_subscribe(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push job/scheduler changes to the custom panel."""
    def _forward(kind: str):
        @callback
        def _handle(payload: dict[str, Any]) -> None:
            connection.send_message(
                websocket_api.event_message(
                    msg["id"], {"type": kind, **dict(payload or {})}
                )
            )

        return _handle

    unsubs = [
        async_dispatcher_connect(hass, SIGNAL_JOB_UPDATED, _forward("job_updated")),
        async_dispatcher_connect(hass, SIGNAL_SCHEDULER_UPDATED, _forward("scheduler_updated")),
    ]

    @callback
    def _unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/get",
    vol.Required("entry_id"): str,
    vol.Optional("start"): str,
    vol.Optional("end"): str,
    vol.Optional("schedule_id"): str,
    vol.Optional("zone_id"): str,
    vol.Optional("execution_mode"): vol.In(["all", "REAL", "DRY_RUN"]),
    vol.Optional("origin"): vol.In(["all", "SCHEDULED", "MANUAL", "EXTERNAL"]),
    vol.Optional("execution_source"): vol.In(["all", "SCHEDULED", "MANUAL", "FORCE", "EXTERNAL"]),
    vol.Optional("limit", default=200): vol.All(int, vol.Range(min=1, max=2000)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return long-term statistics derived from the integration-owned ledger."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    filters = {
        key: msg[key]
        for key in ("start", "end", "schedule_id", "zone_id", "execution_mode", "origin", "execution_source")
        if key in msg
    }
    try:
        payload = await scheduler.statistics.async_query(filters, limit=int(msg.get("limit", 200)))
    except (TypeError, ValueError) as err:
        _send_localizable_error(connection, msg["id"], "invalid_statistics_filter", str(err))
        return
    connection.send_result(msg["id"], payload)




@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/maintenance/get",
    vol.Required("entry_id"): str,
    vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=500)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_maintenance_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the operational maintenance journal and current synthetic levels."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    connection.send_result(msg["id"], scheduler.statistics.maintenance_payload(limit=int(msg.get("limit", 100))))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/maintenance/save",
    vol.Required("entry_id"): str,
    vol.Required("interpretations"): dict,
    vol.Optional("session_id"): str,
    vol.Optional("occurred_at"): str,
    vol.Optional("note"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_maintenance_save(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Confirm, create or revise a maintenance session."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_save_maintenance(
            msg["interpretations"],
            session_id=msg.get("session_id"),
            occurred_at=msg.get("occurred_at"),
            note=msg.get("note"),
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "maintenance": payload})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/water_service",
    vol.Required("entry_id"): str,
    vol.Required("tank"): vol.In(["clean", "dirty"]),
    vol.Required("action"): vol.In(["full", "empty", "level", "add_ml", "remove_ml", "noop", "unknown"]),
    vol.Optional("value"): vol.Coerce(float),
    vol.Optional("pending_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_water_service(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Confirm clean/dirty tank service without affecting scheduler decisions."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_service_water(
            msg["tank"],
            msg["action"],
            value=msg.get("value"),
            pending_id=msg.get("pending_id"),
        )
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], {"saved": True, "water": payload})



@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/forecast/get",
    vol.Required("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_forecast_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return trained Forecast Models independently from historical statistics."""
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await hass.async_add_executor_job(scheduler.statistics.forecast_payload)
    except Exception as err:  # keep Forecast UI failures isolated and visible
        _send_localizable_error(connection, msg["id"], "forecast_load_failed", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/forecast/archive_preview",
    vol.Required("entry_id"): str,
    vol.Required("metric"): vol.In(["time", "battery", "clean_water", "dirty_water"]),
    vol.Required("retain_days"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3650)),
})
@websocket_api.require_admin
def websocket_statistics_forecast_archive_preview(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = scheduler.statistics.forecast_archive_preview(msg["metric"], int(msg["retain_days"]))
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/forecast/archive",
    vol.Required("entry_id"): str,
    vol.Required("metric"): vol.In(["time", "battery", "clean_water", "dirty_water"]),
    vol.Required("retain_days"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3650)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_forecast_archive(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_archive_forecast_model(msg["metric"], int(msg["retain_days"]))
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/forecast/delete_archive",
    vol.Required("entry_id"): str,
    vol.Required("metric"): vol.In(["time", "battery", "clean_water", "dirty_water"]),
    vol.Required("archive_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_forecast_delete_archive(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    connection.send_result(msg["id"], await scheduler.statistics.async_delete_forecast_archive(msg["metric"], msg["archive_id"]))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/statistics/forecast/restore_archive",
    vol.Required("entry_id"): str,
    vol.Required("metric"): vol.In(["time", "battery", "clean_water", "dirty_water"]),
    vol.Required("archive_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_statistics_forecast_restore_archive(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_restore_forecast_archive(msg["metric"], msg["archive_id"])
    except ValueError as err:
        _send_localizable_error(connection, msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], payload)


_DATA_LAYER_SCHEMA = vol.All([vol.In(["execution_history", "statistics", "models", "maintenance"])], vol.Length(min=1))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/data/backup",
    vol.Required("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_data_backup(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_create_backup(scheduler.store, reason="manual")
    except (OSError, ValueError, zipfile.BadZipFile) as err:
        _send_localizable_error(connection, msg["id"], "data_backup_failed", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/data/clear",
    vol.Required("entry_id"): str,
    vol.Required("layers"): _DATA_LAYER_SCHEMA,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_data_clear(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_clear_data(scheduler.store, msg["layers"])
    except (OSError, ValueError, zipfile.BadZipFile) as err:
        _send_localizable_error(connection, msg["id"], "data_clear_failed", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/data/restore",
    vol.Required("entry_id"): str,
    vol.Required("backup_id"): str,
    vol.Required("layers"): _DATA_LAYER_SCHEMA,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_data_restore(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        payload = await scheduler.statistics.async_restore_backup(scheduler.store, msg["backup_id"], msg["layers"])
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as err:
        _send_localizable_error(connection, msg["id"], "data_restore_failed", str(err))
        return
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/data/delete_backup",
    vol.Required("entry_id"): str,
    vol.Required("backup_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_data_delete_backup(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    scheduler = _entry_scheduler(hass, msg["entry_id"])
    if scheduler is None:
        _send_localizable_error(connection, msg["id"], "scheduler_not_loaded")
        return
    try:
        deleted = await scheduler.statistics.async_delete_backup(msg["backup_id"])
    except (OSError, ValueError) as err:
        _send_localizable_error(connection, msg["id"], "data_backup_delete_failed", str(err))
        return
    connection.send_result(msg["id"], {"deleted": deleted})


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve and register the self-contained schedule manager panel.

    Static HTTP routing is registered once per Home Assistant runtime. The
    panel definition and WebSocket handlers are deliberately refreshed on
    every integration setup so an integration reload cannot leave an older
    frontend/backend pair active.
    """
    if not hass.data.get(DATA_FRONTEND_STATIC_REGISTERED):
        frontend_path = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_BASE_URL, str(frontend_path), False)]
        )
        hass.data[DATA_FRONTEND_STATIC_REGISTERED] = True

    # Register this as a real Home Assistant custom panel. A custom web
    # component must be loaded through panel_custom with a module_url;
    # registering the web-component name as a built-in panel leaves the
    # frontend with no built-in implementation to render and results in a
    # blank page. Remove any stale 0.3.4-0.3.8 registration first.
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="Vacuum Schedule",
        sidebar_icon="mdi:robot-vacuum",
        module_url=f"{FRONTEND_BASE_URL}/panel.js?v={VERSION}",
        config={"version": VERSION},
        require_admin=True,
        config_panel_domain=DOMAIN,
    )

    # async_register_command replaces handlers with the same command name, so
    # always refresh these too when the integration is reloaded.
    websocket_api.async_register_command(hass, websocket_list_schedules)
    websocket_api.async_register_command(hass, websocket_schedule_editor)
    websocket_api.async_register_command(hass, websocket_save_schedule)
    websocket_api.async_register_command(hass, websocket_delete_schedule)
    websocket_api.async_register_command(hass, websocket_settings_get)
    websocket_api.async_register_command(hass, websocket_settings_update_interface_language)
    websocket_api.async_register_command(hass, websocket_settings_update_policy)
    websocket_api.async_register_command(hass, websocket_settings_preview_execution_mode)
    websocket_api.async_register_command(hass, websocket_settings_update_execution_mode)
    websocket_api.async_register_command(hass, websocket_settings_update_dry_run)
    websocket_api.async_register_command(hass, websocket_settings_update_real_execution)
    websocket_api.async_register_command(hass, websocket_settings_update_execution)
    websocket_api.async_register_command(hass, websocket_settings_update_binding)
    websocket_api.async_register_command(hass, websocket_settings_update_zone)
    websocket_api.async_register_command(hass, websocket_settings_delete_zone)
    websocket_api.async_register_command(hass, websocket_settings_rescan)
    websocket_api.async_register_command(hass, websocket_settings_validate)
    websocket_api.async_register_command(hass, websocket_preflight_test)
    websocket_api.async_register_command(hass, websocket_testing_override_set)
    websocket_api.async_register_command(hass, websocket_testing_override_clear)
    websocket_api.async_register_command(hass, websocket_testing_preset)
    websocket_api.async_register_command(hass, websocket_scheduler_status)
    websocket_api.async_register_command(hass, websocket_scheduler_job_details)
    websocket_api.async_register_command(hass, websocket_statistics_get)
    websocket_api.async_register_command(hass, websocket_maintenance_get)
    websocket_api.async_register_command(hass, websocket_maintenance_save)
    websocket_api.async_register_command(hass, websocket_statistics_water_service)
    websocket_api.async_register_command(hass, websocket_statistics_forecast_get)
    websocket_api.async_register_command(hass, websocket_statistics_forecast_archive_preview)
    websocket_api.async_register_command(hass, websocket_statistics_forecast_archive)
    websocket_api.async_register_command(hass, websocket_statistics_forecast_delete_archive)
    websocket_api.async_register_command(hass, websocket_statistics_forecast_restore_archive)
    websocket_api.async_register_command(hass, websocket_data_backup)
    websocket_api.async_register_command(hass, websocket_data_clear)
    websocket_api.async_register_command(hass, websocket_data_restore)
    websocket_api.async_register_command(hass, websocket_data_delete_backup)
    websocket_api.async_register_command(hass, websocket_notifications_get)
    websocket_api.async_register_command(hass, websocket_notifications_detail)
    websocket_api.async_register_command(hass, websocket_notifications_save)
    websocket_api.async_register_command(hass, websocket_notifications_test_channel)
    websocket_api.async_register_command(hass, websocket_notifications_test_event)
    websocket_api.async_register_command(hass, websocket_notifications_clear_history)
    websocket_api.async_register_command(hass, websocket_job_action)
    websocket_api.async_register_command(hass, websocket_schedule_run_now)
    websocket_api.async_register_command(hass, websocket_schedule_set_enabled)
    websocket_api.async_register_command(hass, websocket_schedule_set_paused)
    websocket_api.async_register_command(hass, websocket_scheduler_reconcile_timeline)
    websocket_api.async_register_command(hass, websocket_scheduler_rebuild_schedule)
    websocket_api.async_register_command(hass, websocket_debug_advance_time)
    websocket_api.async_register_command(hass, websocket_debug_next_transition)
    websocket_api.async_register_command(hass, websocket_debug_reset_time)
    websocket_api.async_register_command(hass, websocket_debug_reset_dry_run)
    websocket_api.async_register_command(hass, websocket_debug_set_execution_fault)
    websocket_api.async_register_command(hass, websocket_scheduler_subscribe)
