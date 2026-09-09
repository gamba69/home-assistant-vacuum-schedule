"""Data models for Vacuum Schedule's capability layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CapabilitySupport(StrEnum):
    """Describe whether a generic capability is known to be supported."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class NormalizedVacuumState(StrEnum):
    """Vendor-neutral vacuum state used by the scheduler boundary."""

    UNAVAILABLE = "unavailable"
    CLEANING = "cleaning"
    DOCKED = "docked"
    IDLE = "idle"
    PAUSED = "paused"
    RETURNING = "returning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class RelatedDeviceSet:
    """Home Assistant devices participating in capability/entity discovery."""

    primary: str | None = None
    configured: tuple[str, ...] = ()
    existing: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def all_existing_device_ids(self) -> tuple[str, ...]:
        """Return primary and configured existing devices in deterministic order."""
        values = set(self.existing)
        if self.primary:
            values.add(self.primary)
        return tuple(sorted(values))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "primary": self.primary,
            "configured": list(self.configured),
            "existing": list(self.existing),
            "missing": list(self.missing),
            "all_existing_device_ids": list(self.all_existing_device_ids),
        }


@dataclass(slots=True, frozen=True)
class RelatedEntitySet:
    """Detected and manually selected Home Assistant entities related to the robot."""

    manual: tuple[str, ...] = ()
    battery: tuple[str, ...] = ()
    dock: tuple[str, ...] = ()
    water: tuple[str, ...] = ()
    mop: tuple[str, ...] = ()
    consumables: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def all_entity_ids(self) -> tuple[str, ...]:
        """Return all unique related entity ids in deterministic order."""
        values = {
            *self.manual,
            *self.battery,
            *self.dock,
            *self.water,
            *self.mop,
            *self.consumables,
            *self.diagnostics,
        }
        return tuple(sorted(values))

    def as_dict(self) -> dict[str, list[str]]:
        """Return a JSON-serializable representation."""
        return {
            "manual": list(self.manual),
            "battery": list(self.battery),
            "dock": list(self.dock),
            "water": list(self.water),
            "mop": list(self.mop),
            "consumables": list(self.consumables),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(slots=True, frozen=True)
class CapabilityReport:
    """Capabilities visible through the generic Home Assistant boundary."""

    capabilities: dict[str, CapabilitySupport] = field(default_factory=dict)
    supported_features_raw: int = 0
    fan_speed_list: tuple[str, ...] = ()
    sources: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def supported(self) -> tuple[str, ...]:
        """Return names of known supported capabilities."""
        return tuple(
            sorted(
                name
                for name, support in self.capabilities.items()
                if support is CapabilitySupport.SUPPORTED
            )
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "capabilities": {
                name: support.value for name, support in sorted(self.capabilities.items())
            },
            "supported_features_raw": self.supported_features_raw,
            "fan_speed_list": list(self.fan_speed_list),
            "sources": {
                name: list(values) for name, values in sorted(self.sources.items())
            },
        }


@dataclass(slots=True, frozen=True)
class RoomMappingInfo:
    """Describe the room/area mapping boundary without invoking the source entity."""

    clean_area_support: CapabilitySupport
    source: str = "home_assistant_vacuum_clean_area"
    scheduler_mapping_configured: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "clean_area_support": self.clean_area_support.value,
            "source": self.source,
            "scheduler_mapping_configured": self.scheduler_mapping_configured,
        }


@dataclass(slots=True, frozen=True)
class StatusEntityBinding:
    """Read-only state of an explicitly configured Home Assistant status entity."""

    entity_id: str | None = None
    exists: bool = False
    available: bool = False
    state: str | None = None
    device_class: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "entity_id": self.entity_id,
            "exists": self.exists,
            "available": self.available,
            "state": self.state,
            "device_class": self.device_class,
        }


@dataclass(slots=True, frozen=True)
class DockWaterBindings:
    """Explicit clean/dirty-water status sources used by future pre-flight checks."""

    clean_water: StatusEntityBinding = field(default_factory=StatusEntityBinding)
    dirty_water: StatusEntityBinding = field(default_factory=StatusEntityBinding)

    @property
    def all_entity_ids(self) -> tuple[str, ...]:
        """Return configured source entity ids."""
        return tuple(
            entity_id
            for entity_id in (
                self.clean_water.entity_id,
                self.dirty_water.entity_id,
            )
            if entity_id
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "clean_water": self.clean_water.as_dict(),
            "dirty_water": self.dirty_water.as_dict(),
        }


@dataclass(slots=True, frozen=True)
class VacuumSnapshot:
    """Read-only snapshot of the configured robot."""

    configured: bool
    vacuum_entity_id: str | None
    entity_exists: bool
    available: bool
    raw_state: str | None
    normalized_state: NormalizedVacuumState
    raw_attributes: dict[str, Any]
    capabilities: CapabilityReport
    related_devices: RelatedDeviceSet
    related_entities: RelatedEntitySet
    room_mapping: RoomMappingInfo
    dock_water: DockWaterBindings = field(default_factory=DockWaterBindings)

    def as_dict(self, *, include_raw_attributes: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        result: dict[str, Any] = {
            "configured": self.configured,
            "vacuum_entity_id": self.vacuum_entity_id,
            "entity_exists": self.entity_exists,
            "available": self.available,
            "raw_state": self.raw_state,
            "normalized_state": self.normalized_state.value,
            "capabilities": self.capabilities.as_dict(),
            "related_devices": self.related_devices.as_dict(),
            "related_entities": self.related_entities.as_dict(),
            "room_mapping": self.room_mapping.as_dict(),
            "dock_water": self.dock_water.as_dict(),
        }
        if include_raw_attributes:
            result["raw_attributes"] = self.raw_attributes
        return result
