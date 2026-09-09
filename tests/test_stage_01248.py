"""Regression coverage for contextual Force admission in 0.12.48."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobOrigin  # noqa: E402
from scheduler_arbiter import ArbiterCandidate, SchedulerArbiter  # noqa: E402


def _candidate(
    job_id: str,
    *,
    kind: str,
    planned: datetime,
    priority: int = 0,
    preempts: bool = False,
) -> ArbiterCandidate:
    return ArbiterCandidate(
        job_id=job_id,
        origin=JobOrigin.MANUAL if kind == "manual" else JobOrigin.SCHEDULED,
        planned_start=planned,
        manual_triggered_at=planned if kind == "manual" else None,
        manual_release_at=None,
        ready_zone_ids=("zone",),
        candidate_class=kind,
        force_priority=priority,
        preempts_scheduled=preempts,
    )


def test_no_due_scheduled_all_forces_compete_by_numeric_priority() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    high_normal = _candidate(
        "force-100-normal", kind="forced", planned=now + timedelta(hours=2), priority=100
    )
    lower_preemptive = _candidate(
        "force-90-important",
        kind="forced",
        planned=now + timedelta(hours=1),
        priority=90,
        preempts=True,
    )
    assert arbiter.select([lower_preemptive, high_normal], lease_owner=None).job_id == "force-100-normal"
    assert [item.job_id for item in arbiter.ordered([lower_preemptive, high_normal])] == [
        "force-100-normal",
        "force-90-important",
    ]


def test_due_scheduled_admits_only_preemptive_force_ahead_of_plan() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    due = _candidate("due-plan", kind="scheduled", planned=now - timedelta(minutes=5))
    high_normal = _candidate(
        "force-100-normal", kind="forced", planned=now + timedelta(hours=2), priority=100
    )
    lower_preemptive = _candidate(
        "force-90-important",
        kind="forced",
        planned=now + timedelta(hours=1),
        priority=90,
        preempts=True,
    )
    assert arbiter.select([due, high_normal, lower_preemptive], lease_owner=None).job_id == "force-90-important"
    assert [item.job_id for item in arbiter.ordered([due, high_normal, lower_preemptive])] == [
        "force-90-important",
        "due-plan",
        "force-100-normal",
    ]


def test_due_scheduled_chooses_highest_priority_among_important_forces() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    due = _candidate("due-plan", kind="scheduled", planned=now - timedelta(minutes=5))
    normal_1000 = _candidate(
        "normal-1000", kind="forced", planned=now + timedelta(hours=3), priority=1000
    )
    important_80 = _candidate(
        "important-80", kind="forced", planned=now + timedelta(hours=1), priority=80, preempts=True
    )
    important_90 = _candidate(
        "important-90", kind="forced", planned=now + timedelta(hours=2), priority=90, preempts=True
    )
    assert arbiter.select(
        [normal_1000, important_80, due, important_90], lease_owner=None
    ).job_id == "important-90"


def test_due_scheduled_wins_when_no_force_is_important() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    due = _candidate("due-plan", kind="scheduled", planned=now - timedelta(minutes=5))
    force = _candidate(
        "force-1000-normal", kind="forced", planned=now + timedelta(hours=1), priority=1000
    )
    assert arbiter.select([force, due], lease_owner=None).job_id == "due-plan"


def test_manual_still_outranks_due_plan_and_important_force() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    manual = _candidate("manual", kind="manual", planned=now)
    due = _candidate("due-plan", kind="scheduled", planned=now - timedelta(minutes=5))
    important = _candidate(
        "important", kind="forced", planned=now + timedelta(hours=1), priority=1_000_000, preempts=True
    )
    assert arbiter.select([important, due, manual], lease_owner=None).job_id == "manual"


def test_engine_rewrites_force_selected_marker_to_contextual_arbiter_result() -> None:
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'selected_force_id = (' in source
    assert 'self._force_status["selected_candidate_job_id"] = selected_force_id' in source
    assert 'evaluation["selected"] = bool(' in source
    assert '"preemptive_force_by_priority_when_scheduled_due"' in source
