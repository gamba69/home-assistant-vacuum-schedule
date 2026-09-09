"""Read-only start readiness and durable, debounced notification transitions.

This layer never decides whether execution is permitted. It consumes the live
pre-flight result and the occurrence's existing start window.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

try:
    from .time_utils import as_utc
except ImportError:  # Isolated, Home Assistant-free regression tests.
    from time_utils import as_utc

STABILITY_SECONDS = 120
ATTENTION_CODES = frozenset({
    "clean_water_insufficient", "dirty_water_full", "detergent_unavailable",
    "mop_not_attached", "vacuum_error", "vacuum_unavailable", "dock_unavailable",
    "invalid_configuration", "required_capability_missing", "invalid_target",
    "forecast_clean_water_insufficient", "forecast_dirty_water_insufficient",
})
UNKNOWN_CODES = frozenset({
    "zone_state_unknown", "room_state_unknown", "room_unavailable",
    "battery_unavailable", "dnd_unavailable", "dock_clean_water_unavailable",
    "dock_dirty_water_unavailable", "dock_detergent_unavailable",
    "mop_attached_unavailable", "mop_unavailable",
})
RISK_CODES = frozenset({"insufficient_time_window", "forecast_time_insufficient"})
ADMIN_CODES = frozenset({"global_disabled", "global_disabled_until", "schedule_paused", "zone_disabled", "zone_disabled_until"})


def canonical_codes(codes: Any) -> tuple[str, ...]:
    return tuple(sorted({
        "vacuum_busy" if str(code).removeprefix("synthetic_") == "execution_lease_busy"
        else str(code).removeprefix("synthetic_")
        for code in codes if code
    }))


def has_started(job: Any) -> bool:
    return bool(job.actual_start or any(run.actual_start for run in job.zone_runs.values()))


def readiness(job: Any, now: datetime) -> dict[str, Any] | None:
    if job.terminal or has_started(job) or as_utc(now) < as_utc(job.warning_at):
        return None
    if as_utc(now) >= as_utc(job.deadline_at):
        return None
    codes = canonical_codes(job.current_blockers)
    if set(codes) & ADMIN_CODES:
        status = "disabled"
    elif set(codes) & RISK_CODES:
        status = "risk"
    elif set(codes) & ATTENTION_CODES:
        status = "attention"
    elif set(codes) & UNKNOWN_CODES or not job.current_preflight_decision:
        status = "unknown"
    elif codes:
        status = "blocked"
    else:
        status = "ready"
    return {"status": status, "blockers": list(codes), "deadline_at": job.deadline_at.isoformat()}


def forecast_candidate(job: Any, now: datetime, policy: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    """Advance the current candidate; return at most one stable transition.

    ``state`` is persisted in the notification store, separately from jobs.
    Only current observations survive a restart; no transition queue is replayed.
    """
    view = readiness(job, now)
    if view is None or view["status"] == "disabled" or str(policy.start_forecast_mode) == "off":
        state.pop("due_at", None)
        state.pop("candidate", None)
        return None
    status = view["status"]
    closing = (
        policy.start_forecast_deadline_minutes > 0
        and as_utc(now) >= as_utc(job.deadline_at) - timedelta(minutes=policy.start_forecast_deadline_minutes)
    )
    kind = "closing" if closing and not state.get("closing_sent") else status
    signature = [kind, view["blockers"]]
    if state.get("candidate") != signature:
        state["candidate"] = signature
        state["since"] = now.isoformat()
    if state.get("announced") == signature:
        state.pop("due_at", None)
        return None
    previous_kind = (state.get("announced") or [None])[0]
    improved = status == "blocked" and previous_kind in {"attention", "unknown", "risk"}
    important = kind in {"attention", "unknown", "risk", "closing", "ready"} or improved
    if str(policy.start_forecast_mode) == "important" and not important:
        state.pop("due_at", None)
        return None
    since = as_utc(datetime.fromisoformat(state["since"]))
    # A known time boundary needs no sensor debounce.
    due = as_utc(now) if kind == "closing" else since + timedelta(seconds=STABILITY_SECONDS)
    if state.get("last_at"):
        due = max(due, as_utc(datetime.fromisoformat(state["last_at"])) + timedelta(seconds=policy.start_forecast_interval_seconds))
    if due >= as_utc(job.deadline_at):
        # No delayed notification may outlive the start window.
        state.pop("due_at", None)
        return None
    if as_utc(now) < due:
        state["due_at"] = due.isoformat()
        return None
    state.pop("due_at", None)
    state["announced"] = signature
    state["last_at"] = now.isoformat()
    state["sequence"] = int(state.get("sequence", 0)) + 1
    if kind == "closing":
        state["closing_sent"] = True
        # Countdown is one message, not a temporary status that produces a
        # second identical blocker notification on the following reconciliation.
        state["announced"] = [status, view["blockers"]]
    return {**view, "kind": "improved" if improved and kind != "closing" else kind, "sequence": state["sequence"]}
