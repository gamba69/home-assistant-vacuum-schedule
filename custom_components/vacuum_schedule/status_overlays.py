"""Transient scheduler-level overlays for frontend status payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_EXECUTION_LEASE_BUSY = "execution_lease_busy"


def _force_waits_only_for_lease(force_status: Mapping[str, Any] | None) -> bool:
    """Return whether an early Job could start now except for the lease."""
    if not isinstance(force_status, Mapping):
        return False
    if str(force_status.get("window_state") or "") != "IN_WINDOW":
        return False
    if not bool(force_status.get("conditions_matched")):
        return False
    if not tuple(force_status.get("ready_zone_ids") or ()):
        return False

    blockers = {
        str(item)
        for item in force_status.get("preflight_blockers", ()) or ()
        if item
    }
    if blockers - {_EXECUTION_LEASE_BUSY}:
        return False

    protection = force_status.get("plan_protection")
    if isinstance(protection, Mapping) and not bool(protection.get("allowed")):
        return False
    return True


def _lease_is_relevant_now(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    runnable_job_ids: Iterable[str],
    force_status: Mapping[str, Any] | None,
) -> bool:
    """Limit the lease warning to Jobs requesting execution right now."""
    if str(job_id) in {str(item) for item in runnable_job_ids}:
        return True

    current_blockers = {
        str(item) for item in payload.get("current_blockers", ()) or () if item
    }
    if (
        str(payload.get("state") or "") == "WAIT"
        and _EXECUTION_LEASE_BUSY in current_blockers
    ):
        return True

    return _force_waits_only_for_lease(force_status)


def apply_execution_lease_overlay(
    payload: dict[str, Any],
    *,
    job_id: str,
    lease_owner: tuple[str, str] | None,
    runnable_job_ids: Iterable[str] = (),
    force_status: Mapping[str, Any] | None = None,
    owner_schedule_name: str | None = None,
    owner_attempt_state: str | None = None,
) -> bool:
    """Expose another Job's active Execution Lease without changing Pre-flight.

    The physical robot may correctly report ``docked`` during completion
    verification. Scheduler availability is a separate fact, so it is merged
    only into the transient status payload and never persisted as a sensor or
    zone blocker.
    """
    if lease_owner is None or str(lease_owner[0]) == str(job_id):
        return False
    if not _lease_is_relevant_now(
        payload,
        job_id=job_id,
        runnable_job_ids=runnable_job_ids,
        force_status=force_status,
    ):
        return False

    blockers = [str(item) for item in payload.get("current_blockers", ()) if item]
    if _EXECUTION_LEASE_BUSY not in blockers:
        blockers.append(_EXECUTION_LEASE_BUSY)
    payload["current_blockers"] = blockers
    if str(payload.get("current_preflight_decision") or "") != "FAIL":
        payload["current_preflight_decision"] = "WAIT"
    payload["execution_lease"] = {
        "owner_job_id": str(lease_owner[0]),
        "owner_attempt_id": str(lease_owner[1]),
        "owner_schedule_name": owner_schedule_name,
        "owner_attempt_state": owner_attempt_state,
    }
    return True
