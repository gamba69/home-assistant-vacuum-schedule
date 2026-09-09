"""Regression coverage for force-priority direction in 0.12.47."""
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


def test_higher_numeric_force_priority_always_wins_between_forced_candidates() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    low_preemptive = _candidate(
        "low-preemptive",
        kind="forced",
        planned=now + timedelta(hours=1),
        priority=10,
        preempts=True,
    )
    high_normal = _candidate(
        "high-normal",
        kind="forced",
        planned=now + timedelta(hours=2),
        priority=100,
        preempts=False,
    )
    assert arbiter.select([low_preemptive, high_normal], lease_owner=None).job_id == "high-normal"
    assert [item.job_id for item in arbiter.ordered([low_preemptive, high_normal])] == [
        "high-normal",
        "low-preemptive",
    ]


def test_highest_priority_preemptive_force_can_outrank_due_scheduled() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    due = _candidate("due", kind="scheduled", planned=now - timedelta(minutes=5))
    low_normal = _candidate(
        "low-normal",
        kind="forced",
        planned=now + timedelta(hours=1),
        priority=10,
        preempts=False,
    )
    high_preemptive = _candidate(
        "high-preemptive",
        kind="forced",
        planned=now + timedelta(hours=2),
        priority=100,
        preempts=True,
    )
    assert arbiter.select([due, low_normal, high_preemptive], lease_owner=None).job_id == "high-preemptive"


def test_manual_still_outranks_every_force_priority() -> None:
    now = datetime(2026, 9, 7, 17, 0)
    arbiter = SchedulerArbiter()
    manual = _candidate("manual", kind="manual", planned=now)
    forced = _candidate(
        "forced",
        kind="forced",
        planned=now + timedelta(hours=1),
        priority=1_000_000,
        preempts=True,
    )
    assert arbiter.select([forced, manual], lease_owner=None).job_id == "manual"

def test_committed_progressive_force_preserves_original_priority_and_preemption() -> None:
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    start = source.index("            if is_force:")
    end = source.index("                # The real backend persists COMMAND_INTENT", start)
    block = source[start:end]
    assert 'existing_force = selected_job.metadata.get("force_execution")' in block
    assert 'existing_force.get("priority", selected.force_priority)' in block
    assert 'existing_force.get("preempts_scheduled", selected.preempts_scheduled)' in block
    assert 'existing_force.get("selected_at") or now.isoformat()' in block
