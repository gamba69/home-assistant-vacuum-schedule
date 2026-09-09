"""Read-only schedule calendar for Vacuum Schedule."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import VacuumScheduleConfigEntry
from .const import CONF_CLEANING_ZONES, CONF_SCHEDULES, DOMAIN
from .cleaning_zones import cleaning_zones_from_options
from .device import scheduler_device_info
from .planner import OccurrencePlanner
from .migrations import cleanup_legacy_calendar_entities
from .schedule import Occurrence, ScheduleDefinition, ScheduleValidationError
from .time_utils import instant_add, instant_gt, instant_le, instant_lt

_MIN_DISPLAY_DURATION = timedelta(minutes=1)


def _load_schedules(entry: VacuumScheduleConfigEntry) -> list[ScheduleDefinition]:
    schedules: list[ScheduleDefinition] = []
    for raw in entry.options.get(CONF_SCHEDULES, []):
        try:
            schedules.append(ScheduleDefinition.from_dict(raw))
        except (ScheduleValidationError, TypeError, ValueError):
            continue
    return schedules


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VacuumScheduleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the single read-only Vacuum Schedule calendar."""
    cleanup_legacy_calendar_entities(hass, entry)
    async_add_entities([VacuumScheduleCalendar(entry)])


class VacuumScheduleCalendar(CalendarEntity):
    """Expose all planned occurrences in one Home Assistant calendar."""

    _attr_has_entity_name = True
    _attr_translation_key = "schedule_calendar"
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, entry: VacuumScheduleConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_schedule_calendar"
        self._attr_device_info = scheduler_device_info(entry)

    def _schedules(self) -> list[ScheduleDefinition]:
        return _load_schedules(self._entry)

    def _planner(self) -> OccurrencePlanner:
        return OccurrencePlanner(self.hass.config.time_zone)

    @staticmethod
    def _display_end(occurrence: Occurrence) -> datetime:
        if instant_gt(occurrence.deadline_at, occurrence.planned_start):
            return occurrence.deadline_at
        return instant_add(occurrence.planned_start, _MIN_DISPLAY_DURATION)

    def _event_description(self, occurrence: Occurrence) -> str:
        params = dict(occurrence.cleaning_params)
        zone_names = {
            zone.zone_id: zone.name
            for zone in cleaning_zones_from_options(
                self._entry.options.get(CONF_CLEANING_ZONES, [])
            )
        }
        targets = ", ".join(zone_names.get(target, target) for target in occurrence.targets)
        edit_path = (
            f"/vacuum-schedule?entry={self._entry.entry_id}"
            f"&schedule={occurrence.schedule_id}"
        )
        lines = [
            f"Cleaning zones: {targets}",
            f"Warning: {occurrence.warning_at.isoformat()}",
            f"Deadline: {occurrence.deadline_at.isoformat()}",
        ]
        if params:
            compact = ", ".join(
                f"{key}={value}"
                for key, value in params.items()
                if value not in (None, "") and not key.endswith("_entity_id")
            )
            if compact:
                lines.append(f"Cleaning: {compact}")
        lines.extend(
            [
                f"Edit schedule: {edit_path}",
                f"Occurrence ID: {occurrence.occurrence_id}",
            ]
        )
        return "\n".join(lines)

    def _as_calendar_event(self, occurrence: Occurrence) -> CalendarEvent:
        return CalendarEvent(
            start=occurrence.planned_start,
            end=self._display_end(occurrence),
            summary=occurrence.schedule_name,
            description=self._event_description(occurrence),
            uid=occurrence.occurrence_id,
        )

    @property
    def event(self) -> CalendarEvent | None:
        schedules = self._schedules()
        if not schedules:
            return None

        now = dt_util.now()
        planner = self._planner()
        previous = planner.previous_occurrence(schedules, now, inclusive=True)
        if previous is not None:
            display_end = self._display_end(previous)
            if instant_le(previous.planned_start, now) and instant_lt(now, display_end):
                return self._as_calendar_event(previous)

        upcoming = planner.next_occurrence(schedules, now, inclusive=True)
        return self._as_calendar_event(upcoming) if upcoming is not None else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        schedules = self._schedules()
        if not schedules or instant_le(end_date, start_date):
            return []

        max_window = max(
            (schedule.execution_window_minutes for schedule in schedules),
            default=0,
        )
        search_start = instant_add(start_date, -timedelta(minutes=max_window))
        occurrences = self._planner().between(schedules, search_start, end_date)
        return [
            self._as_calendar_event(item)
            for item in occurrences
            if instant_gt(self._display_end(item), start_date) and instant_lt(item.planned_start, end_date)
        ]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
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
