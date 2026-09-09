"""Runtime container for Vacuum Schedule."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .command_executor import CommandExecutor
from .const import SIGNAL_RUNTIME_UPDATED
from .models import VacuumSnapshot


@dataclass(slots=True)
class VacuumScheduleRuntime:
    """Runtime data stored in ConfigEntry.runtime_data."""

    hass: HomeAssistant
    entry_id: str
    executor: CommandExecutor
    snapshot: VacuumSnapshot
    scheduler: Any | None = None

    def refresh(self) -> VacuumSnapshot:
        """Refresh the in-memory snapshot and notify integration entities."""
        self.snapshot = self.executor.snapshot()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_RUNTIME_UPDATED}_{self.entry_id}", self.snapshot
        )
        return self.snapshot

    def subscribe(self) -> Callable[[], None]:
        """Subscribe to current robot-related state changes."""
        entity_ids = {
            *self.snapshot.related_entities.all_entity_ids,
            *self.snapshot.dock_water.all_entity_ids,
        }
        if self.executor.vacuum_entity_id:
            entity_ids.add(self.executor.vacuum_entity_id)

        if not entity_ids:
            return lambda: None

        @callback
        def _state_changed(_event: Event[EventStateChangedData]) -> None:
            self.refresh()

        return async_track_state_change_event(
            self.hass,
            sorted(entity_ids),
            _state_changed,
        )
