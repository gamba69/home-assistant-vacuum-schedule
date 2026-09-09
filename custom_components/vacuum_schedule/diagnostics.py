"""Diagnostics support for Vacuum Schedule."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import VacuumScheduleConfigEntry
from homeassistant.util import dt as dt_util

from .const import CONF_SCHEDULES, VERSION
from .planner import OccurrencePlanner
from .schedule import ScheduleDefinition, ScheduleValidationError


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: VacuumScheduleConfigEntry
) -> dict[str, Any]:
    """Return config-entry diagnostics."""
    schedules: list[ScheduleDefinition] = []
    invalid_schedules = 0
    for raw in entry.options.get(CONF_SCHEDULES, []):
        try:
            schedules.append(ScheduleDefinition.from_dict(raw))
        except (ScheduleValidationError, TypeError, ValueError):
            invalid_schedules += 1

    planner = OccurrencePlanner(_hass.config.time_zone)
    preview = planner.preview(schedules, dt_util.utcnow(), count=5)

    scheduler = getattr(entry.runtime_data, "scheduler", None)
    statistics = None
    if scheduler is not None and getattr(scheduler, "statistics", None) is not None:
        stats_payload = await scheduler.statistics.async_query({}, limit=0)
        statistics = {
            "schema_version": stats_payload.get("schema_version"),
            "months": stats_payload.get("months", []),
            "total_records": stats_payload.get("total_records", 0),
            "aggregate": stats_payload.get("aggregate", {}),
            "water": stats_payload.get("water", {}),
        }

    return {
        "integration_version": VERSION,
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "calendar": {
            "schedule_count": len(schedules),
            "invalid_schedule_count": invalid_schedules,
            "preview": preview,
        },
        "runtime": {
            "executor": "universal_home_assistant_command_executor",
            "physical_commands_enabled": False,
            "snapshot": entry.runtime_data.snapshot.as_dict(),
            "scheduler": scheduler.status_payload() if scheduler is not None else None,
            "statistics": statistics,
        },
    }
