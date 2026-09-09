"""Vacuum Schedule custom integration."""

from __future__ import annotations

from typing import Any, cast
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.typing import ConfigType

from .capabilities import detect_dock_water_status_entities, discover_vacuum_snapshot
from .bindings import ExecutionGateMode
from .command_executor import CommandExecutor
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_CLEAN_WATER_STATUS_ENTITY_ID,
    CONF_DIRTY_WATER_STATUS_ENTITY_ID,
    CONF_RELATED_DEVICE_IDS,
    CONF_RELATED_ENTITIES,
    CONF_VACUUM_ENTITY_ID,
    DOMAIN,
    EVENT_CAPABILITIES_REFRESHED,
    EVENT_TEST,
    SERVICE_ADDITIONAL_RUN,
    SERVICE_EMIT_TEST_EVENT,
    SERVICE_PAUSE_JOB,
    SERVICE_REBUILD_SCHEDULE,
    SERVICE_REFRESH_CAPABILITIES,
    SERVICE_RESUME_JOB,
    SERVICE_RUN_EARLY,
    SERVICE_RUN_SCHEDULE_NOW,
    SERVICE_SMART_RUN,
    SERVICE_START_JOB_NOW,
    SERVICE_SKIP_JOB,
    SERVICE_CANCEL_JOB,
    SERVICE_RECHECK_JOB,
    SERVICE_SET_SCHEDULE_ENABLED,
    SERVICE_SET_SCHEDULE_PAUSED,
    SERVICE_SET_EXECUTION_GATE,
    VERSION,
)
from .runtime import VacuumScheduleRuntime
from .scheduler_engine import SchedulerEngine
from .frontend import async_setup_frontend
from .migrations import async_migrate_entry

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]

type VacuumScheduleConfigEntry = ConfigEntry[VacuumScheduleRuntime]

_REFRESH_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str})
_SCHEDULE_ACTION_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str, vol.Required("schedule_id"): str})
_SCHEDULE_START_ACTION_SCHEMA = vol.Schema({
    vol.Required(ATTR_CONFIG_ENTRY_ID): str,
    vol.Required("schedule_id"): str,
    vol.Optional("ignore_busy_zones", default=False): bool,
})
_JOB_ACTION_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str, vol.Required("job_id"): str})
_JOB_START_ACTION_SCHEMA = vol.Schema({
    vol.Required(ATTR_CONFIG_ENTRY_ID): str,
    vol.Required("job_id"): str,
    vol.Optional("ignore_busy_zones", default=False): bool,
})
_SET_SCHEDULE_ENABLED_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str, vol.Required("schedule_id"): str, vol.Required("enabled"): bool})
_SET_SCHEDULE_PAUSED_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): str, vol.Required("schedule_id"): str, vol.Required("paused"): bool})
_SET_EXECUTION_GATE_SCHEMA = vol.Schema({
    vol.Required(ATTR_CONFIG_ENTRY_ID): str,
    vol.Required("execution_gate"): vol.In([item.value for item in ExecutionGateMode]),
    vol.Optional("disabled_until"): str,
})
_REBUILD_SCHEDULE_SCHEMA = vol.Schema({
    vol.Required(ATTR_CONFIG_ENTRY_ID): str,
    vol.Optional("restore_early_executed", default=False): bool,
})


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up integration-level actions and the schedule manager panel."""
    await async_setup_frontend(hass)

    async def _emit_test_event(_call: ServiceCall) -> None:
        """Emit the compatibility test event introduced in 0.1.x."""
        hass.bus.async_fire(
            EVENT_TEST,
            {"domain": DOMAIN, "version": VERSION},
        )

    async def _refresh_capabilities(call: ServiceCall) -> dict[str, Any]:
        """Refresh capability data for one loaded config entry."""
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_found",
                translation_placeholders={"config_entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_loaded",
                translation_placeholders={"config_entry_id": entry_id},
            )

        runtime = cast(VacuumScheduleConfigEntry, entry).runtime_data
        snapshot = runtime.refresh()
        response = snapshot.as_dict(include_raw_attributes=False)
        hass.bus.async_fire(
            EVENT_CAPABILITIES_REFRESHED,
            {"config_entry_id": entry_id, **response},
        )
        return response

    def _scheduler_for(entry_id: str) -> tuple[VacuumScheduleConfigEntry, SchedulerEngine]:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_loaded",
                translation_placeholders={"config_entry_id": entry_id},
            )
        typed = cast(VacuumScheduleConfigEntry, entry)
        scheduler = typed.runtime_data.scheduler
        if scheduler is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="config_entry_not_loaded",
                translation_placeholders={"config_entry_id": entry_id},
            )
        return typed, scheduler

    async def _additional_run(call: ServiceCall) -> dict[str, Any]:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        job = await scheduler.async_run_schedule_now(
            str(call.data["schedule_id"]), source="ha_action", user_id=call.context.user_id
        )
        return {"resolution": "additional_run", "job": job.to_dict()}

    async def _run_schedule_now(call: ServiceCall) -> dict[str, Any]:
        """Backward-compatible alias for the canonical additional_run action."""
        return await _additional_run(call)

    async def _run_early(call: ServiceCall) -> dict[str, Any]:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        job = await scheduler.async_run_schedule_early(
            str(call.data["schedule_id"]),
            source="ha_action",
            user_id=call.context.user_id,
            ignore_busy_zones=bool(call.data.get("ignore_busy_zones", False)),
        )
        return {"resolution": "scheduled_occurrence", "job": job.to_dict()}

    async def _smart_run(call: ServiceCall) -> dict[str, Any]:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        result = await scheduler.async_smart_run_schedule(
            str(call.data["schedule_id"]),
            source="ha_action",
            user_id=call.context.user_id,
            ignore_busy_zones=bool(call.data.get("ignore_busy_zones", False)),
        )
        return {
            "resolution": str(result["resolution"]),
            "job": result["job"].to_dict(),
        }

    async def _start_job_now(call: ServiceCall) -> dict[str, Any]:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        job = await scheduler.async_start_job_now(
            str(call.data["job_id"]),
            source="ha_action",
            user_id=call.context.user_id,
            ignore_busy_zones=bool(call.data.get("ignore_busy_zones", False)),
        )
        return {"job": job.to_dict()}

    async def _skip_job(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        await scheduler.async_skip_job(str(call.data["job_id"]), source="ha_action", user_id=call.context.user_id)

    async def _cancel_job(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        await scheduler.async_cancel_job(str(call.data["job_id"]), source="ha_action", user_id=call.context.user_id)

    async def _pause_job(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        await scheduler.async_pause_job(str(call.data["job_id"]), source="ha_action", user_id=call.context.user_id)

    async def _resume_job(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        await scheduler.async_resume_job(str(call.data["job_id"]), source="ha_action", user_id=call.context.user_id)

    async def _recheck_job(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        await scheduler.async_recheck_job(str(call.data["job_id"]), source="ha_action", user_id=call.context.user_id)

    async def _set_schedule_enabled(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            await scheduler.async_set_schedule_enabled(
                str(call.data["schedule_id"]),
                bool(call.data["enabled"]),
                source="ha_action",
                user_id=call.context.user_id,
            )
        except ValueError as err:
            if str(err) == "schedule_not_found":
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="schedule_not_found") from err
            raise

    async def _set_schedule_paused(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            await scheduler.async_set_schedule_paused(
                str(call.data["schedule_id"]),
                bool(call.data["paused"]),
                source="ha_action",
                user_id=call.context.user_id,
            )
        except ValueError as err:
            if str(err) == "schedule_not_found":
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="schedule_not_found") from err
            raise

    async def _set_execution_gate(call: ServiceCall) -> None:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            await scheduler.async_set_execution_gate(
                str(call.data["execution_gate"]),
                call.data.get("disabled_until"),
                source="ha_action",
                user_id=call.context.user_id,
            )
        except ValueError as err:
            key = str(err)
            if key in {"disabled_until_required", "invalid_disabled_until", "invalid_execution_gate"}:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key=key) from err
            raise

    async def _rebuild_schedule(call: ServiceCall) -> dict[str, Any]:
        _entry, scheduler = _scheduler_for(call.data[ATTR_CONFIG_ENTRY_ID])
        result = await scheduler.async_rebuild_schedule_projection(
            restore_early_executed=bool(call.data.get("restore_early_executed", False))
        )
        return {
            "rebuilt": True,
            "now": result["now"].isoformat(),
            "removed_jobs": result["removed_jobs"],
            "created_jobs": result["created_jobs"],
            "restored_early_jobs": result["restored_early_jobs"],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_EMIT_TEST_EVENT,
        _emit_test_event,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_CAPABILITIES,
        _refresh_capabilities,
        schema=_REFRESH_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADDITIONAL_RUN, _additional_run, schema=_SCHEDULE_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_EARLY, _run_early, schema=_SCHEDULE_START_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SMART_RUN, _smart_run, schema=_SCHEDULE_START_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_SCHEDULE_NOW, _run_schedule_now, schema=_SCHEDULE_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_JOB_NOW, _start_job_now, schema=_JOB_START_ACTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_SKIP_JOB, _skip_job, schema=_JOB_ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_JOB, _cancel_job, schema=_JOB_ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE_JOB, _pause_job, schema=_JOB_ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME_JOB, _resume_job, schema=_JOB_ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RECHECK_JOB, _recheck_job, schema=_JOB_ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE_ENABLED, _set_schedule_enabled, schema=_SET_SCHEDULE_ENABLED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE_PAUSED, _set_schedule_paused, schema=_SET_SCHEDULE_PAUSED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_EXECUTION_GATE, _set_execution_gate, schema=_SET_EXECUTION_GATE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REBUILD_SCHEDULE, _rebuild_schedule, schema=_REBUILD_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: VacuumScheduleConfigEntry
) -> bool:
    """Set up Vacuum Schedule from a config entry."""
    vacuum_entity_id = entry.data.get(CONF_VACUUM_ENTITY_ID)
    related_device_ids = entry.data.get(CONF_RELATED_DEVICE_IDS, [])
    related_entities = entry.data.get(CONF_RELATED_ENTITIES, [])

    clean_water_status_entity_id = entry.data.get(CONF_CLEAN_WATER_STATUS_ENTITY_ID)
    dirty_water_status_entity_id = entry.data.get(CONF_DIRTY_WATER_STATUS_ENTITY_ID)
    if clean_water_status_entity_id is None or dirty_water_status_entity_id is None:
        probe = discover_vacuum_snapshot(
            hass, vacuum_entity_id, related_device_ids, related_entities
        )
        detected_clean, detected_dirty = detect_dock_water_status_entities(
            hass, probe.related_entities.all_entity_ids
        )
        clean_water_status_entity_id = clean_water_status_entity_id or detected_clean
        dirty_water_status_entity_id = dirty_water_status_entity_id or detected_dirty
        if detected_clean or detected_dirty:
            updated_data = dict(entry.data)
            updated_data[CONF_CLEAN_WATER_STATUS_ENTITY_ID] = clean_water_status_entity_id
            updated_data[CONF_DIRTY_WATER_STATUS_ENTITY_ID] = dirty_water_status_entity_id
            hass.config_entries.async_update_entry(entry, data=updated_data)

    executor = CommandExecutor(
        hass,
        vacuum_entity_id,
        related_device_ids,
        related_entities,
        clean_water_status_entity_id,
        dirty_water_status_entity_id,
    )
    runtime = VacuumScheduleRuntime(
        hass=hass,
        entry_id=entry.entry_id,
        executor=executor,
        snapshot=executor.snapshot(),
    )
    entry.runtime_data = runtime
    entry.async_on_unload(runtime.subscribe())

    scheduler = SchedulerEngine(hass, entry)
    runtime.scheduler = scheduler
    await scheduler.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: VacuumScheduleConfigEntry
) -> bool:
    """Unload a Vacuum Schedule config entry."""
    runtime = getattr(entry, "runtime_data", None)
    scheduler = getattr(runtime, "scheduler", None)
    if scheduler is not None:
        await scheduler.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
