"""One-time compatibility migrations for persisted Vacuum Schedule state.

Keep historical upgrade transforms here so runtime scheduling and UI code stay
focused on the current data model. Compatibility parsers that are part of the
current serialization contract remain with their owning models.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from .bindings import (
    BindingMode,
    CapabilityBinding,
    PreflightPolicy,
    SourceType,
    SupportStatus,
)
from .const import (
    CONF_CAPABILITY_BINDINGS,
    CONF_CLEAN_WATER_STATUS_ENTITY_ID,
    CONF_CLEANING_ZONES,
    CONF_DIRTY_WATER_STATUS_ENTITY_ID,
    CONF_DRY_RUN_SETTINGS,
    CONF_EXECUTION_MODE,
    CONF_NOTIFICATION_SETTINGS,
    CONF_PREFLIGHT_POLICY,
    CONF_REAL_EXECUTION_SETTINGS,
    CONF_RELATED_DEVICE_IDS,
    CONF_RELATED_ENTITIES,
    CONF_ROOM_PROFILES,
    CONF_SCHEDULES,
    ENTRY_MINOR_VERSION,
    ENTRY_VERSION,
    DOMAIN,
)
from .execution_models import ExecutionMode
from .job import JobOrigin, JobState
from .notification_models import normalize_notification_settings
from .time_utils import instant_add, instant_gt


async def async_migrate_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Migrate config entries to the current single-target CleaningZone model."""
    if entry.version > ENTRY_VERSION:
        return False

    data = dict(entry.data)
    options = dict(entry.options)
    data.setdefault(CONF_RELATED_DEVICE_IDS, [])
    data.setdefault(CONF_RELATED_ENTITIES, [])
    data.setdefault(CONF_CLEAN_WATER_STATUS_ENTITY_ID, None)
    data.setdefault(CONF_DIRTY_WATER_STATUS_ENTITY_ID, None)
    options.setdefault(CONF_SCHEDULES, [])

    # Upgrades from pre-0.7 builds must never turn simulation into physical
    # execution implicitly.
    options.setdefault(CONF_EXECUTION_MODE, "DRY_RUN")
    options.setdefault(CONF_DRY_RUN_SETTINGS, {"execution_duration_seconds": 10})
    real_execution_settings = dict(options.get(CONF_REAL_EXECUTION_SETTINGS) or {})
    real_execution_settings.setdefault("restore_previous_settings", True)
    real_execution_settings.setdefault("runtime_error_recovery_minutes", 30)
    options[CONF_REAL_EXECUTION_SETTINGS] = real_execution_settings
    options.setdefault(CONF_PREFLIGHT_POLICY, PreflightPolicy().to_dict())
    options[CONF_NOTIFICATION_SETTINGS] = normalize_notification_settings(
        options.get(CONF_NOTIFICATION_SETTINGS, {})
    )

    raw_bindings = dict(options.get(CONF_CAPABILITY_BINDINGS, {}))
    for key, data_key in (
        ("dock.clean_water", CONF_CLEAN_WATER_STATUS_ENTITY_ID),
        ("dock.dirty_water", CONF_DIRTY_WATER_STATUS_ENTITY_ID),
    ):
        entity_id = data.get(data_key)
        if entity_id and key not in raw_bindings:
            raw_bindings[key] = CapabilityBinding(
                key=key,
                binding_mode=BindingMode.MANUAL,
                source_type=SourceType.ENTITY_STATE,
                entity_id=str(entity_id),
                support_status=SupportStatus.MANUAL,
                discovery_reason="migrated_from_explicit_water_binding",
            ).to_dict()

    entity_registry = er.async_get(hass)
    for key, raw_binding in list(raw_bindings.items()):
        binding = CapabilityBinding.from_dict(str(key), raw_binding)
        if (
            binding.binding_mode is BindingMode.MANUAL
            and binding.entity_id
            and not binding.entity_registry_id
        ):
            registry_entry = entity_registry.async_get(binding.entity_id)
            if registry_entry is not None:
                updated = binding.to_dict()
                updated["entity_registry_id"] = str(registry_entry.id)
                raw_bindings[str(key)] = updated
    options[CONF_CAPABILITY_BINDINGS] = raw_bindings

    def enrich_conditions(values: Any) -> list[Any]:
        result: list[Any] = []
        for raw in values or []:
            item = dict(raw) if isinstance(raw, dict) else raw
            if (
                isinstance(item, dict)
                and item.get("entity_id")
                and not item.get("entity_registry_id")
            ):
                registry_entry = entity_registry.async_get(str(item["entity_id"]))
                if registry_entry is not None:
                    item["entity_registry_id"] = str(registry_entry.id)
            result.append(item)
        return result

    def enrich_paths(values: Any) -> list[Any]:
        result: list[Any] = []
        for raw in values or []:
            if not isinstance(raw, dict):
                result.append(raw)
                continue
            path = dict(raw)
            path["conditions"] = enrich_conditions(path.get("conditions", []))
            result.append(path)
        return result

    # Convert the superseded RoomProfile many-to-many model. One old profile
    # with N targets becomes N CleaningZones sharing its old conditions.
    raw_zones = options.get(CONF_CLEANING_ZONES)
    area_to_zones: dict[str, list[str]] = {}
    target_to_zone: dict[tuple[str, str], str] = {}
    migrated_zones: list[dict[str, Any]] = []
    if isinstance(raw_zones, list) and raw_zones:
        for raw in raw_zones:
            if not isinstance(raw, dict):
                continue
            zone = dict(raw)
            zone.setdefault("nominal_area_m2", None)
            zone["busy_sources"] = enrich_conditions(zone.get("busy_sources", []))
            zone["access_paths"] = enrich_paths(zone.get("access_paths", []))
            migrated_zones.append(zone)
            key = (
                str(zone.get("robot_target_type", "segment")),
                str(zone.get("robot_target_id", "")),
            )
            if key[1]:
                target_to_zone.setdefault(key, str(zone.get("zone_id", "")))
    else:
        for raw_room in options.get(CONF_ROOM_PROFILES, []) or []:
            if not isinstance(raw_room, dict):
                continue
            targets = [
                str(value).strip()
                for value in (raw_room.get("robot_target_ids") or [])
                if str(value).strip()
            ]
            target_type = str(raw_room.get("robot_target_type") or "segment")
            room_name = str(raw_room.get("name") or "Cleaning zone").strip()
            busy = enrich_conditions(
                [
                    *(raw_room.get("busy_sources") or []),
                    *(raw_room.get("availability_sources") or []),
                ]
            )
            paths = enrich_paths(raw_room.get("access_paths", []))
            for target in targets:
                key = (target_type, target)
                zone_id = target_to_zone.get(key)
                if not zone_id:
                    zone_id = uuid5(
                        NAMESPACE_URL,
                        f"vacuum_schedule:{entry.entry_id}:{target_type}:{target}",
                    ).hex
                    target_to_zone[key] = zone_id
                    migrated_zones.append(
                        {
                            "zone_id": zone_id,
                            "name": room_name if len(targets) == 1 else f"{room_name} — {target}",
                            "robot_target_type": target_type,
                            "robot_target_id": target,
                            "control": "enabled",
                            "disabled_until": None,
                            "busy_sources": busy,
                            "access_paths": paths,
                            "nominal_area_m2": None,
                        }
                    )
                for area_id in raw_room.get("ha_area_ids", []) or []:
                    bucket = area_to_zones.setdefault(str(area_id), [])
                    if zone_id not in bucket:
                        bucket.append(zone_id)

    # Convert old schedule target forms to CleaningZone IDs. Direct HA Area
    # targets cannot be mapped safely, so they become disabled placeholders that
    # require the user to select the physical target explicitly.
    migrated_schedules: list[Any] = []
    for raw_schedule in options.get(CONF_SCHEDULES, []) or []:
        if not isinstance(raw_schedule, dict):
            migrated_schedules.append(raw_schedule)
            continue
        schedule = dict(raw_schedule)
        if not isinstance(schedule.get("weekday_times"), dict):
            legacy_time = str(schedule.get("local_time") or "09:00:00")
            weekday_index = {
                "mon": 0,
                "tue": 1,
                "wed": 2,
                "thu": 3,
                "fri": 4,
                "sat": 5,
                "sun": 6,
            }
            flexible_times: dict[str, str] = {}
            for day in schedule.get("weekdays") or []:
                text = str(day).strip().lower()
                if text in weekday_index:
                    flexible_times[str(weekday_index[text])] = legacy_time
                elif text.lstrip("-").isdigit() and 0 <= int(text) <= 6:
                    flexible_times[str(int(text))] = legacy_time
            schedule["weekday_times"] = flexible_times
        if not isinstance(schedule.get("weekday_overrides"), dict):
            schedule["weekday_overrides"] = {}
        schedule.setdefault("force_enabled", False)
        schedule.setdefault("force_max_advance_minutes", 0)
        schedule.setdefault("force_priority", 0)
        schedule.setdefault("force_preempts_scheduled", False)
        if not isinstance(schedule.get("force_condition_groups"), list):
            schedule["force_condition_groups"] = []
        if not isinstance(schedule.get("force_condition_group_names"), list):
            schedule["force_condition_group_names"] = []

        target_type = str(schedule.get("target_type") or "areas")
        old_targets = [
            str(value).strip()
            for value in (schedule.get("targets") or [])
            if str(value).strip()
        ]
        if target_type == "cleaning_zones":
            migrated_schedules.append(schedule)
            continue

        zone_ids: list[str] = []
        unresolved_legacy_target = False
        if target_type == "areas":
            for area_id in old_targets:
                mapped = area_to_zones.get(area_id, [])
                if mapped:
                    for zone_id in mapped:
                        if zone_id not in zone_ids:
                            zone_ids.append(zone_id)
                    continue
                physical_type = "segment"
                placeholder_target = f"__migration_target_required__:{area_id}"
                key = (physical_type, placeholder_target)
                zone_id = target_to_zone.get(key)
                if not zone_id:
                    zone_id = uuid5(
                        NAMESPACE_URL,
                        f"vacuum_schedule:{entry.entry_id}:migration-area:{area_id}",
                    ).hex
                    target_to_zone[key] = zone_id
                    migrated_zones.append(
                        {
                            "zone_id": zone_id,
                            "name": str(area_id),
                            "robot_target_type": physical_type,
                            "robot_target_id": placeholder_target,
                            "control": "disabled",
                            "disabled_until": None,
                            "busy_sources": [],
                            "access_paths": [],
                            "nominal_area_m2": None,
                        }
                    )
                zone_ids.append(zone_id)
                unresolved_legacy_target = True
        elif target_type in {"segments", "zone"}:
            physical_type = "segment" if target_type == "segments" else "zone"
            for target in old_targets:
                key = (physical_type, target)
                zone_id = target_to_zone.get(key)
                if not zone_id:
                    zone_id = uuid5(
                        NAMESPACE_URL,
                        f"vacuum_schedule:{entry.entry_id}:{physical_type}:{target}",
                    ).hex
                    target_to_zone[key] = zone_id
                    migrated_zones.append(
                        {
                            "zone_id": zone_id,
                            "name": target,
                            "robot_target_type": physical_type,
                            "robot_target_id": target,
                            "control": "enabled",
                            "disabled_until": None,
                            "busy_sources": [],
                            "access_paths": [],
                            "nominal_area_m2": None,
                        }
                    )
                if zone_id not in zone_ids:
                    zone_ids.append(zone_id)

        if zone_ids:
            schedule["target_type"] = "cleaning_zones"
            schedule["targets"] = zone_ids
            if unresolved_legacy_target:
                schedule["enabled"] = False
            schedule["revision"] = max(1, int(schedule.get("revision", 1))) + 1
        migrated_schedules.append(schedule)

    options[CONF_CLEANING_ZONES] = migrated_zones
    options.pop(CONF_ROOM_PROFILES, None)
    options[CONF_SCHEDULES] = migrated_schedules

    if (
        entry.version != ENTRY_VERSION
        or entry.minor_version != ENTRY_MINOR_VERSION
        or options != dict(entry.options)
        or data != dict(entry.data)
    ):
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=ENTRY_VERSION,
            minor_version=ENTRY_MINOR_VERSION,
        )
    return True


def migrate_pre_071_runtime_state(engine: Any, *, debug_state_schema: int) -> bool:
    """Normalize persisted pre-0.7.1 scheduler development state once."""
    state = engine.store.debug_state
    try:
        schema = int(state.get("dry_run_state_schema", 0))
    except (TypeError, ValueError):
        schema = 0
    if schema >= debug_state_schema:
        return False

    now = engine.clock.now()
    for job in list(engine.store.active.values()):
        if (
            job.origin is JobOrigin.SCHEDULED
            and not job.execution_attempts
            and job.state in (JobState.PLANNED, JobState.WAIT)
            and instant_gt(
                job.created_at,
                instant_add(job.planned_start, timedelta(minutes=1)),
            )
        ):
            engine.store.append_event(
                job,
                "dematerialized",
                now,
                details={"reason": "retroactive_materialization_removed_0_7_1"},
            )
            engine.store.remove_active(job.job_id)

    engine.clock.reset()
    engine._capture_debug_state()
    return True


def migrate_pre_074_materialization(
    engine: Any, *, timeline_materialization_schema: int
) -> bool:
    """Normalize stale unstarted PLANNED placeholders from pre-0.7.4 builds."""
    if engine._timeline_materialization_schema >= timeline_materialization_schema:
        return False

    now = engine.clock.now()
    if engine.execution_mode is ExecutionMode.DRY_RUN:
        engine._dry_run_timeline_generation += 1

    for job in list(engine.store.active.values()):
        if (
            job.origin is JobOrigin.SCHEDULED
            and job.state is JobState.PLANNED
            and not job.execution_attempts
        ):
            engine.store.append_event(
                job,
                "timeline_reconciled",
                now,
                details={
                    "reason": "pre_0_7_4_planned_placeholder_normalized",
                    "statistics_eligible": False,
                },
            )
            engine.dependency_index.remove_job(job.job_id)
            engine.store.remove_active(job.job_id)

    engine._timeline_materialization_schema = timeline_materialization_schema
    engine._capture_debug_state()
    return True

def cleanup_legacy_calendar_entities(hass: HomeAssistant, entry: Any) -> None:
    """Remove obsolete per-schedule calendar entities from early builds."""
    registry = er.async_get(hass)
    aggregate_unique_id = f"{entry.entry_id}_schedule_calendar"
    per_schedule_prefix = f"{entry.entry_id}_schedule_"

    for registry_entry in list(
        registry.entities.get_entries_for_config_entry_id(entry.entry_id)
    ):
        if (
            registry_entry.domain == "calendar"
            and registry_entry.platform == DOMAIN
            and registry_entry.unique_id.startswith(per_schedule_prefix)
            and registry_entry.unique_id != aggregate_unique_id
        ):
            registry.async_remove(registry_entry.entity_id)

    aggregate_entity_id = registry.async_get_entity_id(
        "calendar", DOMAIN, aggregate_unique_id
    )
    if aggregate_entity_id is None:
        return
    aggregate_entry = registry.async_get(aggregate_entity_id)
    if (
        aggregate_entry is not None
        and aggregate_entry.disabled_by == RegistryEntryDisabler.INTEGRATION
    ):
        registry.async_update_entity(aggregate_entity_id, disabled_by=None)

