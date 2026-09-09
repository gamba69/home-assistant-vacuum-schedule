"""Regression coverage for relevance-scoped Scheduler lease status."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from status_overlays import apply_execution_lease_overlay  # noqa: E402


LEASE = ("running-job", "attempt-1")


def _apply(payload, *, force=None, runnable=()):
    return apply_execution_lease_overlay(
        payload,
        job_id="future-job",
        lease_owner=LEASE,
        runnable_job_ids=runnable,
        force_status=force,
        owner_schedule_name="Спальня",
        owner_attempt_state="RUNNING",
    )


def test_future_job_without_an_immediate_release_does_not_show_lease_busy():
    payload = {
        "state": "PLANNED",
        "current_blockers": [],
        "current_preflight_decision": "PASS",
    }

    assert not _apply(payload)
    assert payload["current_blockers"] == []
    assert payload["current_preflight_decision"] == "PASS"
    assert "execution_lease" not in payload


def test_force_window_with_unmatched_conditions_keeps_force_reason_primary():
    payload = {
        "state": "PLANNED",
        "current_blockers": [],
        "current_preflight_decision": "PASS",
    }
    force = {
        "window_state": "IN_WINDOW",
        "conditions_matched": False,
        "ready_zone_ids": [],
        "preflight_blockers": [],
        "plan_protection": None,
    }

    assert not _apply(payload, force=force)
    assert payload["current_blockers"] == []
    assert "execution_lease" not in payload


def test_force_candidate_ready_except_for_lease_shows_busy():
    payload = {
        "state": "PLANNED",
        "current_blockers": [],
        "current_preflight_decision": "PASS",
    }
    force = {
        "window_state": "IN_WINDOW",
        "conditions_matched": True,
        "ready_zone_ids": ["kitchen"],
        "preflight_blockers": ["execution_lease_busy"],
        "plan_protection": {"allowed": True},
    }

    assert _apply(payload, force=force)
    assert payload["current_blockers"] == ["execution_lease_busy"]
    assert payload["current_preflight_decision"] == "WAIT"


def test_force_candidate_with_another_blocker_does_not_blame_lease():
    payload = {
        "state": "PLANNED",
        "current_blockers": ["zone_busy"],
        "current_preflight_decision": "WAIT",
    }
    force = {
        "window_state": "IN_WINDOW",
        "conditions_matched": True,
        "ready_zone_ids": ["kitchen"],
        "preflight_blockers": ["zone_busy", "execution_lease_busy"],
        "plan_protection": None,
    }

    assert not _apply(payload, force=force)
    assert payload["current_blockers"] == ["zone_busy"]
    assert "execution_lease" not in payload


def test_due_or_manual_runnable_job_shows_busy():
    payload = {
        "state": "PLANNED",
        "current_blockers": [],
        "current_preflight_decision": "PASS",
    }

    assert _apply(payload, runnable=("future-job",))
    assert payload["current_blockers"] == ["execution_lease_busy"]


def test_persisted_wait_for_lease_remains_visible_before_next_arbiter_snapshot():
    payload = {
        "state": "WAIT",
        "current_blockers": ["execution_lease_busy"],
        "current_preflight_decision": "WAIT",
    }

    assert _apply(payload)
    assert payload["execution_lease"]["owner_schedule_name"] == "Спальня"


def test_scheduler_passes_live_force_and_runnable_context_to_overlay():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert "runnable_job_ids=runnable_job_ids" in engine
    assert "force_status=force" in engine
