"""Diagnostic sensor platform for Vacuum Schedule."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import VacuumScheduleConfigEntry
from .const import CONF_SCHEDULES, SIGNAL_RUNTIME_UPDATED, SIGNAL_SCHEDULER_UPDATED
from .device import scheduler_device_info
from .models import VacuumSnapshot
from .planner import OccurrencePlanner
from .schedule import ScheduleDefinition, ScheduleValidationError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumScheduleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up read-only diagnostic entities for the capability layer."""
    async_add_entities(
        [
            VacuumScheduleStatusSensor(entry),
            VacuumScheduleRobotStateSensor(entry),
            VacuumScheduleCapabilitiesSensor(entry),
            VacuumScheduleRelatedDevicesSensor(entry),
            VacuumScheduleRelatedEntitiesSensor(entry),
            VacuumScheduleNextRunSensor(entry),
            VacuumScheduleNextWarningSensor(entry),
            VacuumScheduleNextDeadlineSensor(entry),
            VacuumSchedulePlannedOccurrencesSensor(entry),
            VacuumScheduleEngineStateSensor(entry),
            VacuumScheduleActiveJobsSensor(entry),
            VacuumScheduleLastResultSensor(entry),
        ]
    )


class _VacuumScheduleSnapshotSensor(SensorEntity):
    """Base class for sensors backed by the current read-only snapshot."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: VacuumScheduleConfigEntry, suffix: str) -> None:
        """Initialize a snapshot-backed sensor."""
        self._entry = entry
        self._snapshot = entry.runtime_data.snapshot
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = scheduler_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_RUNTIME_UPDATED}_{self._entry.entry_id}",
                self._handle_snapshot,
            )
        )

    @callback
    def _handle_snapshot(self, snapshot: VacuumSnapshot) -> None:
        """Handle a refreshed snapshot in the Home Assistant event loop."""
        self._snapshot = snapshot
        self.async_write_ha_state()


class VacuumScheduleStatusSensor(_VacuumScheduleSnapshotSensor):
    """Expose the current capability-layer health state."""

    _attr_entity_category = None

    _attr_translation_key = "status"
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        """Initialize the status sensor."""
        super().__init__(entry, "status")

    @property
    def native_value(self) -> str:
        """Return the capability-layer health state."""
        if not self._snapshot.configured:
            return "needs_configuration"
        if not self._snapshot.available:
            return "unavailable"
        return "ready"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return concise integration diagnostics."""
        return {
            "vacuum_entity_id": self._snapshot.vacuum_entity_id,
            "physical_commands_enabled": False,
            "executor": "universal_home_assistant_command_executor",
        }


class VacuumScheduleRobotStateSensor(_VacuumScheduleSnapshotSensor):
    """Expose the normalized robot state in the regular integration UI."""

    _attr_entity_category = None

    _attr_translation_key = "robot_state"
    _attr_icon = "mdi:robot-vacuum-variant"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        """Initialize the robot-state sensor."""
        super().__init__(entry, "robot_state")

    @property
    def native_value(self) -> str:
        """Return the normalized vendor-neutral robot state."""
        return self._snapshot.normalized_state.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw source information for troubleshooting."""
        return {
            "vacuum_entity_id": self._snapshot.vacuum_entity_id,
            "raw_vacuum_state": self._snapshot.raw_state,
        }


class VacuumScheduleCapabilitiesSensor(_VacuumScheduleSnapshotSensor):
    """Expose discovered capabilities persistently in Home Assistant."""

    _attr_translation_key = "capabilities"
    _attr_icon = "mdi:list-status"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        """Initialize the capability sensor."""
        super().__init__(entry, "capabilities")

    @property
    def native_value(self) -> int:
        """Return the number of capabilities known to be supported."""
        return len(self._snapshot.capabilities.supported)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full capability report."""
        report = self._snapshot.capabilities
        return {
            "supported": list(report.supported),
            "capabilities": report.as_dict()["capabilities"],
            "fan_speed_list": list(report.fan_speed_list),
            "supported_features_raw": report.supported_features_raw,
            "sources": {name: list(values) for name, values in report.sources.items()},
        }


class VacuumScheduleRelatedDevicesSensor(_VacuumScheduleSnapshotSensor):
    """Expose the primary and explicitly associated Home Assistant devices."""

    _attr_translation_key = "related_devices"
    _attr_icon = "mdi:devices"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        """Initialize the related-device sensor."""
        super().__init__(entry, "related_devices")

    @property
    def native_value(self) -> int:
        """Return the number of explicitly associated devices."""
        return len(self._snapshot.related_devices.existing)

    def _device_name(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None
        return (
            getattr(device, "name_by_user", None)
            or getattr(device, "name", None)
            or getattr(device, "model", None)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device ids and current Home Assistant names."""
        devices = self._snapshot.related_devices
        return {
            **devices.as_dict(),
            "primary_name": self._device_name(devices.primary),
            "related_device_names": {
                device_id: self._device_name(device_id)
                for device_id in devices.existing
            },
        }


class VacuumScheduleRelatedEntitiesSensor(_VacuumScheduleSnapshotSensor):
    """Expose auto-detected and manually supplied robot-related entities."""

    _attr_translation_key = "related_entities"
    _attr_icon = "mdi:connection"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        """Initialize the related-entity sensor."""
        super().__init__(entry, "related_entities")

    @property
    def native_value(self) -> int:
        """Return the number of discovered related entities."""
        return len(self._snapshot.related_entities.all_entity_ids)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detected entities grouped by purpose."""
        return self._snapshot.related_entities.as_dict()

class _VacuumScheduleCalendarSensor(SensorEntity):
    """Base sensor for the read-only stage-0.3 calendar preview."""

    _attr_has_entity_name = True

    def __init__(self, entry: VacuumScheduleConfigEntry, suffix: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = scheduler_device_info(entry)

    def _schedules(self) -> list[ScheduleDefinition]:
        schedules: list[ScheduleDefinition] = []
        for raw in self._entry.options.get(CONF_SCHEDULES, []):
            try:
                schedules.append(ScheduleDefinition.from_dict(raw))
            except (ScheduleValidationError, TypeError, ValueError):
                continue
        return schedules

    def _preview(self) -> dict[str, object]:
        planner = OccurrencePlanner(self.hass.config.time_zone)
        return planner.preview(self._schedules(), dt_util.utcnow(), count=5)

    async def async_added_to_hass(self) -> None:
        """Refresh the preview periodically as wall-clock time advances."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._handle_time,
                timedelta(minutes=1),
            )
        )

    @callback
    def _handle_time(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @staticmethod
    def _as_datetime(value: object) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(str(value))


class VacuumScheduleNextRunSensor(_VacuumScheduleCalendarSensor):
    """Expose the next planned schedule occurrence."""

    _attr_translation_key = "next_run"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "next_run")

    @property
    def native_value(self) -> datetime | None:
        item = self._preview().get("next")
        return self._as_datetime(item.get("planned_start") if isinstance(item, dict) else None)


class VacuumScheduleNextWarningSensor(_VacuumScheduleCalendarSensor):
    """Expose the warning time of the next occurrence."""

    _attr_translation_key = "next_warning"
    _attr_icon = "mdi:bell-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "next_warning")

    @property
    def native_value(self) -> datetime | None:
        item = self._preview().get("next")
        return self._as_datetime(item.get("warning_at") if isinstance(item, dict) else None)


class VacuumScheduleNextDeadlineSensor(_VacuumScheduleCalendarSensor):
    """Expose the deadline of the next occurrence."""

    _attr_translation_key = "next_deadline"
    _attr_icon = "mdi:calendar-alert"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "next_deadline")

    @property
    def native_value(self) -> datetime | None:
        item = self._preview().get("next")
        return self._as_datetime(item.get("deadline_at") if isinstance(item, dict) else None)


class VacuumSchedulePlannedOccurrencesSensor(_VacuumScheduleCalendarSensor):
    """Expose a compact preview of the next five calendar occurrences."""

    _attr_translation_key = "planned_occurrences"
    _attr_icon = "mdi:calendar-multiple"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "planned_occurrences")

    @property
    def native_value(self) -> int:
        upcoming = self._preview().get("next_occurrences", [])
        return len(upcoming) if isinstance(upcoming, list) else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        preview = self._preview()
        return {
            "timezone": preview.get("timezone"),
            "previous": preview.get("previous"),
            "current_relevant": preview.get("current_relevant"),
            "next_occurrences": preview.get("next_occurrences", []),
        }



class _VacuumScheduleEngineSensor(SensorEntity):
    """Base class for compact scheduler operational sensors."""

    _attr_has_entity_name = True

    def __init__(self, entry: VacuumScheduleConfigEntry, suffix: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = scheduler_device_info(entry)

    def _status(self) -> dict[str, Any]:
        scheduler = getattr(self._entry.runtime_data, "scheduler", None)
        return scheduler.status_payload() if scheduler is not None else {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _updated(payload: dict[str, Any]) -> None:
            if payload.get("entry_id") == self._entry.entry_id:
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_SCHEDULER_UPDATED, _updated)
        )


class VacuumScheduleEngineStateSensor(_VacuumScheduleEngineSensor):
    """Expose the scheduler operating mode/state."""

    _attr_translation_key = "scheduler_state"
    _attr_icon = "mdi:state-machine"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "scheduler_state")

    @property
    def native_value(self) -> str:
        return "active" if self._status() else "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status()
        return {
            "mode": status.get("mode"),
            "next_transition": status.get("next_transition"),
            "watchdog_seconds": status.get("watchdog_seconds"),
            "clock": status.get("clock"),
        }


class VacuumScheduleActiveJobsSensor(_VacuumScheduleEngineSensor):
    """Expose active scheduler job counts without creating one entity per job."""

    _attr_translation_key = "active_jobs"
    _attr_icon = "mdi:progress-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "active_jobs")

    @property
    def native_value(self) -> int:
        return int(self._status().get("counts", {}).get("active", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status()
        return {
            "counts": status.get("counts", {}),
            "active_jobs": status.get("active_jobs", []),
        }


class VacuumScheduleLastResultSensor(_VacuumScheduleEngineSensor):
    """Expose the most recent terminal JobInstance result."""

    _attr_translation_key = "last_job_result"
    _attr_icon = "mdi:history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        super().__init__(entry, "last_job_result")

    @property
    def native_value(self) -> str:
        history = self._status().get("history", [])
        return str(history[0].get("result") or "none") if history else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self._status().get("history", [])
        return {"job": history[0] if history else None}
