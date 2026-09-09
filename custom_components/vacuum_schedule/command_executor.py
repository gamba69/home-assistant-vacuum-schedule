"""Universal execution boundary for Vacuum Schedule.

Version 0.8.0 exposes physical commands, but only RealExecutionBackend receives this object. Dry-run has no reference to the command boundary.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant

from .capabilities import discover_vacuum_snapshot
from .const import VERSION
from .models import VacuumSnapshot


class CommandExecutor:
    """Vendor-neutral boundary around configured Home Assistant entities/actions."""

    def __init__(
        self,
        hass: HomeAssistant,
        vacuum_entity_id: str | None,
        related_device_ids: Iterable[str] = (),
        related_entities: Iterable[str] = (),
        clean_water_status_entity_id: str | None = None,
        dirty_water_status_entity_id: str | None = None,
    ) -> None:
        """Initialize the universal executor."""
        self._hass = hass
        self._vacuum_entity_id = vacuum_entity_id
        self._related_device_ids = tuple(related_device_ids)
        self._related_entities = tuple(related_entities)
        self._clean_water_status_entity_id = clean_water_status_entity_id
        self._dirty_water_status_entity_id = dirty_water_status_entity_id

    @property
    def vacuum_entity_id(self) -> str | None:
        """Return the configured primary vacuum entity id."""
        return self._vacuum_entity_id

    @property
    def related_device_ids(self) -> tuple[str, ...]:
        """Return explicitly associated Home Assistant device ids."""
        return self._related_device_ids

    @property
    def related_entities(self) -> tuple[str, ...]:
        """Return manually configured fallback entity ids."""
        return self._related_entities

    def snapshot(self) -> VacuumSnapshot:
        """Return a fresh read-only snapshot from Home Assistant state memory."""
        return discover_vacuum_snapshot(
            self._hass,
            self._vacuum_entity_id,
            self._related_device_ids,
            self._related_entities,
            self._clean_water_status_entity_id,
            self._dirty_water_status_entity_id,
        )

    async def _async_vacuum_service(self, service: str, data: dict | None = None) -> None:
        """Call one standard Home Assistant vacuum action."""
        if not self._vacuum_entity_id:
            raise RuntimeError("vacuum_entity_missing")
        payload = {"entity_id": self._vacuum_entity_id}
        if data:
            payload.update(data)
        await self._hass.services.async_call("vacuum", service, payload, blocking=True)

    async def async_start(self) -> None:
        """Start/resume the configured vacuum."""
        await self._async_vacuum_service("start")

    async def async_stop(self) -> None:
        """Stop the configured vacuum."""
        await self._async_vacuum_service("stop")

    async def async_pause(self) -> None:
        """Pause the configured vacuum."""
        await self._async_vacuum_service("pause")

    async def async_return_home(self) -> None:
        """Return the configured vacuum to its dock."""
        await self._async_vacuum_service("return_to_base")
