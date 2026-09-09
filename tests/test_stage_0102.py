"""Independent Job/WAIT and Scheduler Arbiter contracts for 0.10.7."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobOrigin  # noqa: E402
from scheduler_arbiter import ArbiterCandidate, SchedulerArbiter  # noqa: E402


def candidate(
    job_id: str,
    *,
    origin: JobOrigin = JobOrigin.SCHEDULED,
    planned: datetime,
    manual_at: datetime | None = None,
    release_at: datetime | None = None,
) -> ArbiterCandidate:
    return ArbiterCandidate(
        job_id=job_id,
        origin=origin,
        planned_start=planned,
        manual_triggered_at=manual_at,
        manual_release_at=release_at,
        ready_zone_ids=("zone",),
    )




def test_manual_candidate_wins_over_due_scheduled_candidate():
    now = datetime(2026, 8, 23, 12, 0)
    arbiter = SchedulerArbiter()
    scheduled = candidate("scheduled", planned=now - timedelta(hours=1))
    manual = candidate(
        "manual",
        origin=JobOrigin.MANUAL,
        planned=now,
        manual_at=now,
    )
    assert arbiter.select([scheduled, manual], lease_owner=None).job_id == "manual"


def test_manual_release_of_scheduled_occurrence_also_gets_manual_priority():
    now = datetime(2026, 8, 23, 12, 0)
    arbiter = SchedulerArbiter()
    due = candidate("due", planned=now - timedelta(hours=2))
    released = candidate("released", planned=now + timedelta(hours=2), release_at=now)
    assert arbiter.select([due, released], lease_owner=None).job_id == "released"


def test_earliest_runnable_scheduled_job_wins_deterministically():
    now = datetime(2026, 8, 23, 12, 0)
    arbiter = SchedulerArbiter()
    later = candidate("b", planned=now - timedelta(minutes=5))
    earlier = candidate("a", planned=now - timedelta(minutes=30))
    assert arbiter.select([later, earlier], lease_owner=None).job_id == "a"


def test_busy_execution_lease_prevents_any_second_start_selection():
    now = datetime(2026, 8, 23, 12, 0)
    arbiter = SchedulerArbiter()
    runnable = candidate("a", planned=now)
    assert arbiter.select([runnable], lease_owner=("owner-job", "attempt")) is None


def test_engine_prepares_every_job_before_one_global_arbitration():
    source = (MODULE / "scheduler_engine.py").read_text()
    reconcile = source[source.index("async def async_reconcile(self)"):source.index("async def async_recheck_jobs")]
    assert "ready_by_job" in reconcile
    assert "await self._prepare_job(job, now)" in reconcile
    assert "force_ready_by_job = self._evaluate_force_candidates(now)" in reconcile
    assert "await self._arbitrate_jobs(ready_by_job, force_ready_by_job, now)" in reconcile
    assert reconcile.index("await self._prepare_job(job, now)") < reconcile.index("force_ready_by_job = self._evaluate_force_candidates(now)")
    assert reconcile.index("force_ready_by_job = self._evaluate_force_candidates(now)") < reconcile.index("await self._arbitrate_jobs(ready_by_job, force_ready_by_job, now)")


def test_successor_no_longer_finishes_wait_and_store_allows_multiple_occurrences():
    engine = (MODULE / "scheduler_engine.py").read_text()
    store = (MODULE / "job_store.py").read_text()
    prepare = engine[engine.index("async def _prepare_job"):engine.index("def _set_execution_lease_wait")]
    startup = engine[engine.index("async def _async_startup_recovery"):engine.index("def _subscribe_zone_inputs")]
    assert "successor_due" not in prepare
    assert "DISPLACED_BY_NEXT_OCCURRENCE" not in prepare
    assert "DISPLACED_BY_NEXT_OCCURRENCE" not in startup
    assert "active_scheduled_job_exists" not in store


def test_dependency_recheck_uses_global_reconcile_so_priority_is_not_bypassed():
    source = (MODULE / "scheduler_engine.py").read_text()
    section = source[source.index("async def async_recheck_jobs"):source.index("async def async_settings_changed")]
    assert "await self.async_reconcile()" in section
    assert "_prepare_job(" not in section
