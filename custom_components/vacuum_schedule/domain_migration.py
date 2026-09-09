"""One-time migration from the pre-0.13 Vacuum Scheduler domain.

Home Assistant config-entry domains are immutable.  The 0.13 rename therefore
creates a new ``vacuum_schedule`` entry and explicitly moves integration-owned
storage from the legacy entry id before the old config entry is removed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any
import zipfile

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

LEGACY_DOMAIN = "vacuum_scheduler"
_STORE_VERSION = 1

# Every non-monthly Store owned by 0.12.61.  Keep this list explicit so a domain
# rename cannot accidentally copy unrelated Home Assistant storage.
_STORE_SUFFIXES: tuple[str, ...] = (
    "jobs",
    "job_history",
    "notifications",
    "external_execution",
    "statistics.index",
    "statistics.cache",
    "statistics.charging",
    "statistics.control",
    "statistics.models",
    "statistics.water",
    "statistics.water.ledger",
    "statistics.water.state",
)



def _migrate_backup_archives_sync(
    source_directory: Path,
    target_directory: Path,
    legacy_entry_id: str,
    new_entry_id: str,
) -> list[str]:
    """Copy legacy statistics backups into the new public namespace."""
    if not source_directory.exists():
        return []
    target_directory.mkdir(parents=True, exist_ok=True)
    migrated: list[str] = []
    pattern = f"vacuum-scheduler-{legacy_entry_id}-*.zip"
    for source_path in sorted(source_directory.glob(pattern)):
        try:
            with zipfile.ZipFile(source_path, "r") as source_archive:
                manifest = json.loads(source_archive.read("manifest.json").decode("utf-8"))
                if str(manifest.get("entry_id") or "") != str(legacy_entry_id):
                    continue
                members = {
                    name: source_archive.read(name)
                    for name in source_archive.namelist()
                    if name != "manifest.json"
                }
            target_name = source_path.name.replace(
                f"vacuum-scheduler-{legacy_entry_id}-",
                f"vacuum-schedule-{new_entry_id}-",
                1,
            )
            manifest["entry_id"] = new_entry_id
            manifest["backup_id"] = target_name
            target_path = target_directory / target_name
            with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as target_archive:
                target_archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                for name, payload in members.items():
                    target_archive.writestr(name, payload)
            migrated.append(target_name)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            # A broken user-created backup must not prevent the domain migration.
            continue
    return migrated


async def _async_migrate_backup_archives(
    hass: HomeAssistant, legacy_entry_id: str, new_entry_id: str
) -> list[str]:
    config = getattr(hass, "config", None)
    if config is not None and callable(getattr(config, "path", None)):
        source_directory = Path(config.path("vacuum_scheduler_backups"))
        target_directory = Path(config.path("vacuum_schedule_backups"))
    else:
        source_directory = Path("/config/vacuum_scheduler_backups")
        target_directory = Path("/config/vacuum_schedule_backups")
    runner = getattr(hass, "async_add_executor_job", None)
    if callable(runner):
        return await runner(
            _migrate_backup_archives_sync,
            source_directory,
            target_directory,
            legacy_entry_id,
            new_entry_id,
        )
    return await asyncio.to_thread(
        _migrate_backup_archives_sync,
        source_directory,
        target_directory,
        legacy_entry_id,
        new_entry_id,
    )

def _key(domain: str, entry_id: str, suffix: str) -> str:
    return f"{domain}.{entry_id}.{suffix}"


async def _copy_store(
    hass: HomeAssistant,
    *,
    source_key: str,
    target_key: str,
) -> bool:
    """Copy one Store payload if the legacy payload exists."""
    source = Store(hass, _STORE_VERSION, source_key)
    payload = await source.async_load()
    if payload is None:
        return False
    target = Store(hass, _STORE_VERSION, target_key)
    await target.async_save(payload)
    return True


async def async_migrate_legacy_domain_storage(
    hass: HomeAssistant,
    legacy_entry_id: str,
    new_entry_id: str,
) -> dict[str, Any]:
    """Move all integration-owned 0.12.61 Store data into the 0.13 namespace.

    The old stores are intentionally left untouched until Home Assistant removes
    the legacy config entry.  This makes the copy operation restart-safe: if the
    flow is interrupted, running it again simply overwrites the new copies with
    the same source payloads.
    """
    copied: list[str] = []
    for suffix in _STORE_SUFFIXES:
        if await _copy_store(
            hass,
            source_key=_key(LEGACY_DOMAIN, legacy_entry_id, suffix),
            target_key=_key(DOMAIN, new_entry_id, suffix),
        ):
            copied.append(suffix)

    # Statistics ledger rows are split into monthly stores.  The authoritative
    # index tells us exactly which chunks exist, so no filesystem scanning is
    # needed and no arbitrary .storage keys are touched.
    index = Store(
        hass,
        _STORE_VERSION,
        _key(LEGACY_DOMAIN, legacy_entry_id, "statistics.index"),
    )
    raw_index = await index.async_load() or {}
    months = []
    if isinstance(raw_index, Mapping):
        months = sorted({str(value) for value in raw_index.get("months", []) if value})
    for month in months:
        suffix = f"statistics.{month}"
        if await _copy_store(
            hass,
            source_key=_key(LEGACY_DOMAIN, legacy_entry_id, suffix),
            target_key=_key(DOMAIN, new_entry_id, suffix),
        ):
            copied.append(suffix)

    migrated_backups = await _async_migrate_backup_archives(
        hass, legacy_entry_id, new_entry_id
    )

    return {
        "legacy_domain": LEGACY_DOMAIN,
        "domain": DOMAIN,
        "legacy_entry_id": legacy_entry_id,
        "new_entry_id": new_entry_id,
        "copied_stores": copied,
        "migrated_backups": migrated_backups,
    }
