"""Logical Home Assistant device metadata for Vacuum Schedule."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, VERSION


def scheduler_device_info(entry) -> DeviceInfo:
    """Return the single logical service device shared by all integration entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Vacuum Schedule — {entry.title}",
        manufacturer="Vacuum Schedule",
        model="Vacuum scheduling service",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="homeassistant://vacuum-schedule",
    )
