"""One-time migrations and deterministic repairs for persistent ledgers.

The functions in this module are intentionally side-effect scoped to the owning
store objects passed by callers. They preserve historical upgrade behavior while
keeping current statistics and water-accounting code focused on normal runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from .time_utils import as_utc
from .statistics_models import (
    build_statistics_record,
    execution_source_for_job,
    repair_record_area,
)

AREA_REPAIR_ID = "0.10.18_floor_vs_processed_area_v3"
EXECUTION_SOURCE_REPAIR_ID = "0.12.4_execution_source_v1"


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def recover_unconfirmed_water_service_after_upgrade(water: Any) -> None:
    """Recover threshold-anchored service facts missed by pre-session builds."""
    now = _iso_now()
    for tank, key in (("clean", "dock.clean_water"), ("dirty", "dock.dirty_water")):
        balance = water.clean if tank == "clean" else water.dirty
        if balance.get("last_anchor") != "sensor_threshold" or water._resource_ok(key) is not True:
            continue
        anchor = _parse_dt(balance.get("anchor_at"))
        already_pending = any(
            session.get("status") == "pending"
            and tank in _maintenance_tanks(session)
            for session in water.maintenance_sessions
        )
        if already_pending:
            continue
        later_confirmed = False
        for session in water.maintenance_sessions:
            if session.get("status") != "confirmed" or tank not in dict(session.get("interpretations") or {}):
                continue
            at = _parse_dt(water._session_time(session))
            if anchor is None or (at is not None and at >= anchor):
                later_confirmed = True
                break
        if not later_confirmed:
            water._detect_maintenance(
                tank, None, now, signal="startup_recovery_inferred"
            )


def _maintenance_tanks(session: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("tank") or "")
        for item in (session.get("detection") or {}).get("items", [])
        if str(item.get("tank") or "") in {"clean", "dirty"}
    } | {
        str(tank)
        for tank in dict(session.get("interpretations") or {})
        if str(tank) in {"clean", "dirty"}
    }


def migrate_legacy_water_maintenance(water: Any) -> None:
    """Convert pre-session water maintenance events/pending state in memory."""
    if not water.maintenance_sessions:
        by_pending: dict[str, dict[str, Any]] = {}
        for event in water.events:
            if event.get("event_type") != "TANK_SERVICE":
                continue
            tank = str(event.get("tank") or "")
            if tank not in {"clean", "dirty"}:
                continue
            pending_id = str(event.get("pending_id") or event.get("event_id") or uuid4().hex)
            session = by_pending.get(pending_id)
            if session is None:
                at = str(event.get("at") or _iso_now())
                session = {
                    "session_id": f"legacy-{pending_id}",
                    "source": "legacy",
                    "detected_at": at,
                    "occurred_at": at,
                    "status": "confirmed",
                    "detection": {"items": []},
                    "interpretations": {},
                    "note": None,
                    "revisions": [],
                    "created_at": at,
                    "updated_at": at,
                }
                by_pending[pending_id] = session
            session["interpretations"][tank] = {
                "action": event.get("action"),
                "value": event.get("value"),
                "updated_at": event.get("at"),
            }
        water.maintenance_sessions.extend(by_pending.values())

    existing_legacy_pending = {
        str(item.get("legacy_pending_id") or "")
        for session in water.maintenance_sessions
        for item in (session.get("detection") or {}).get("items", [])
        if item.get("legacy_pending_id")
    }
    for tank, pending in list(water.pending_service.items()):
        pending_id = str(pending.get("pending_id") or uuid4().hex)
        if pending_id in existing_legacy_pending:
            continue
        water.maintenance_sessions.append(
            {
                "session_id": f"detected-{pending_id}",
                "source": "detected",
                "detected_at": pending.get("returned_at")
                or pending.get("removed_at")
                or _iso_now(),
                "occurred_at": pending.get("returned_at") or _iso_now(),
                "status": "pending",
                "detection": {
                    "items": [
                        {
                            "tank": tank,
                            "removed_at": pending.get("removed_at"),
                            "returned_at": pending.get("returned_at"),
                            "legacy_pending_id": pending_id,
                            "signals": ["legacy_pending"],
                        }
                    ]
                },
                "interpretations": {},
                "note": None,
                "revisions": [],
                "created_at": pending.get("returned_at") or _iso_now(),
                "updated_at": pending.get("returned_at") or _iso_now(),
            }
        )


def migrate_water_cycle_threshold_events(water: Any) -> None:
    """Materialize missing threshold events retained only as legacy cycles."""
    existing = {
        (str(event.get("tank")), str(event.get("at")))
        for event in water.events
        if event.get("event_type") == "SENSOR_THRESHOLD"
    }
    for cycle in list(water.cycles):
        key = (str(cycle.get("tank")), str(cycle.get("end_at")))
        if key[0] not in {"clean", "dirty"} or not key[1] or key in existing:
            continue
        water.events.append(
            {
                "event_id": f"migrated-threshold-{cycle.get('cycle_id') or uuid4().hex}",
                "event_type": "SENSOR_THRESHOLD",
                "tank": key[0],
                "at": key[1],
                "observation_quality": "migrated_cycle",
                "details": {"migrated_from_cycle": cycle.get("cycle_id")},
            }
        )
        existing.add(key)


async def async_repair_statistics_area_history(
    manager: Any, terminal_jobs: list[Any], events_for_job
) -> int:
    """Repair historical floor-area derivation without rewriting other facts."""
    if manager.store.repair_applied(AREA_REPAIR_ID):
        return 0
    terminal_by_job = {str(job.job_id): job for job in terminal_jobs}
    existing_records = await manager.store.async_records()
    replacements: dict[str, dict[str, Any]] = {}
    repaired_records: dict[str, dict[str, Any]] = {}
    repaired_at = datetime.now().astimezone()
    for existing in existing_records:
        job_id = str(existing.get("job_id") or "")
        job = terminal_by_job.get(job_id)
        if job is None:
            continue
        rebuilt = build_statistics_record(
            job.to_dict(),
            events_for_job(job_id),
            recorded_at=repaired_at,
        )
        repaired, changed = repair_record_area(
            existing,
            rebuilt,
            repaired_at=repaired_at,
            repair_id=AREA_REPAIR_ID,
        )
        already_repaired = any(
            str(item.get("repair_id") or "") == AREA_REPAIR_ID
            for item in existing.get("repairs") or []
            if isinstance(item, Mapping)
        )
        if changed:
            replacements[job_id] = repaired
            repaired_records[job_id] = repaired
        elif already_repaired:
            repaired_records[job_id] = dict(existing)

    replaced = await manager.store.async_replace_records(replacements)
    if replaced:
        await manager.store.async_rebuild_cache()

    water_ready = manager.water.profile is not None
    if repaired_records and water_ready:
        await manager.water.async_repair_job_records(list(repaired_records.values()))

    # Keep the repair open if the water profile was unavailable. A later startup
    # can regenerate dependent water facts without changing statistics again.
    if not repaired_records or water_ready:
        await manager.store.async_mark_repair_applied(AREA_REPAIR_ID)
    return replaced


async def async_repair_statistics_execution_sources(
    manager: Any, terminal_jobs: list[Any]
) -> int:
    """Backfill execution-source display/filter facts from durable Job data."""
    if manager.store.repair_applied(EXECUTION_SOURCE_REPAIR_ID):
        return 0
    terminal_by_job = {str(job.job_id): job for job in terminal_jobs}
    replacements: dict[str, dict[str, Any]] = {}
    repaired_at = datetime.now().astimezone().isoformat()
    for existing in await manager.store.async_records():
        job_id = str(existing.get("job_id") or "")
        job = terminal_by_job.get(job_id)
        if job is not None:
            source = str(job.execution_source.value)
        else:
            source = execution_source_for_job(existing)
        if str(existing.get("execution_source") or "") == source:
            continue
        updated = dict(existing)
        updated["execution_source"] = source
        repairs = [
            dict(item)
            for item in existing.get("repairs") or []
            if isinstance(item, Mapping)
        ]
        repairs = [
            item
            for item in repairs
            if str(item.get("repair_id") or "") != EXECUTION_SOURCE_REPAIR_ID
        ]
        repairs.append(
            {
                "repair_id": EXECUTION_SOURCE_REPAIR_ID,
                "repaired_at": repaired_at,
                "scope": "execution_source_only",
            }
        )
        updated["repairs"] = repairs[-10:]
        replacements[job_id] = updated
    replaced = await manager.store.async_replace_records(replacements)
    await manager.store.async_mark_repair_applied(EXECUTION_SOURCE_REPAIR_ID)
    return replaced

async def async_backfill_statistics_terminal_snapshots(
    manager: Any, terminal_jobs: list[Any], events_for_job
) -> bool:
    """Import durable pre-ledger terminal Jobs that are not recorded yet."""
    changed = False
    for job in terminal_jobs:
        if manager.store.contains_job(job.job_id) or not manager._job_after_statistics_reset(job):
            continue
        record = build_statistics_record(
            job.to_dict(),
            events_for_job(job.job_id),
            recorded_at=datetime.now().astimezone(),
        )
        if await manager.store.async_append(record):
            await manager.water.async_record_job(record)
            changed = True
    return changed

def migrate_job_history_archive(
    store: Any,
    durable_jobs: list[Any],
    operational_history: list[Any],
    durable_raw: Mapping[str, Any],
) -> tuple[list[Any], list[dict[str, Any]], bool]:
    """Merge pre-durable operational history into the durable Job archive."""
    by_occurrence = {store._occurrence_namespace_key(job): job for job in durable_jobs}
    for job in operational_history:
        by_occurrence.setdefault(store._occurrence_namespace_key(job), job)
    terminal_archive = sorted(
        by_occurrence.values(),
        key=lambda item: as_utc(item.finished_at or item.planned_start),
    )
    events = [
        dict(item)
        for item in durable_raw.get("events", [])
        if isinstance(item, Mapping)
    ]
    migration_needed = not durable_raw and bool(terminal_archive)
    if migration_needed:
        for job in terminal_archive:
            events.append(
                store._event_payload(
                    job,
                    "migrated_terminal_snapshot",
                    job.finished_at or job.planned_start,
                    details={"history_schema": "pre-0.6.31"},
                )
            )
    return terminal_archive, events, migration_needed


def migrate_water_model_baseline(water: Any) -> None:
    """Freeze learned pre-epoch water calibration as the model baseline once."""
    if water.model_baseline:
        return
    water.model_baseline = dict(water.calibration or water._fresh_calibration())
    water.model_epoch = _iso_now()


WATER_CALIBRATION_REPAIR_V3_ID = "0.12.20_water_calibration_v3"


def repair_water_calibration_v3(water: Any) -> bool:
    """Rebuild pre-v3 water calibration from raw ledger facts.

    The v2 model could freeze an implausible learned scale (up to 4x) into the
    independent model baseline. V3 deliberately discards that learned baseline,
    keeps physical/maintenance facts, and replays them with stricter calibration
    acceptance. Derived water events retain their immutable base usage facts and
    are recalculated during replay.
    """
    baseline = dict(getattr(water, "model_baseline", {}) or {})
    calibration = dict(getattr(water, "calibration", {}) or {})
    version = max(
        int(baseline.get("algorithm_version") or 0),
        int(calibration.get("algorithm_version") or 0),
    )
    if version >= 3:
        return False
    fresh = dict(water._fresh_calibration())
    fresh["algorithm_version"] = 3
    fresh.update({
        "repair_id": WATER_CALIBRATION_REPAIR_V3_ID,
        "repaired_at": _iso_now(),
        "repaired_from_algorithm_version": version or None,
    })
    # Do not preserve the v2 epoch: replay all base water facts through the v3
    # acceptance rules so old bad applied scales are overwritten deterministically.
    water.model_baseline = fresh
    water.model_epoch = None
    water.calibration = dict(fresh)
    return True


WATER_CALIBRATION_REPAIR_V4_ID = "0.12.46_water_cleaning_scope_v4"


def repair_water_calibration_v4(water: Any) -> bool:
    """Reset water calibration after dry/wet scope accounting was corrected.

    Pre-v4 derived FLOOR_MOP events could be created for explicit vacuum-only
    occurrences when inherited mop/water fields remained in a weekday override
    snapshot.  Any scale learned from those cycles is therefore contaminated.
    V4 preserves raw service/sensor facts, discards learned coefficients and lets
    the repaired derived ledger replay from the model-specific bootstrap.
    """
    baseline = dict(getattr(water, "model_baseline", {}) or {})
    calibration = dict(getattr(water, "calibration", {}) or {})
    version = max(
        int(baseline.get("algorithm_version") or 0),
        int(calibration.get("algorithm_version") or 0),
    )
    if version >= 4:
        return False
    fresh = dict(water._fresh_calibration())
    fresh.update({
        "repair_id": WATER_CALIBRATION_REPAIR_V4_ID,
        "repaired_at": _iso_now(),
        "repaired_from_algorithm_version": version or None,
    })
    water.model_baseline = fresh
    water.model_epoch = None
    water.calibration = dict(fresh)
    return True
