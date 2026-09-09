"""Live Force execution contracts for Vacuum Schedule 0.10.7."""
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
    candidate_class: str,
    planned: datetime,
    force_priority: int = 0,
    origin: JobOrigin = JobOrigin.SCHEDULED,
    manual_at: datetime | None = None,
) -> ArbiterCandidate:
    return ArbiterCandidate(
        job_id=job_id,
        origin=origin,
        planned_start=planned,
        manual_triggered_at=manual_at,
        manual_release_at=None,
        ready_zone_ids=("zone",),
        candidate_class=candidate_class,
        force_priority=force_priority,
    )




def test_arbiter_priority_manual_then_due_scheduled_then_forced():
    now = datetime(2026, 8, 23, 20, 0)
    arbiter = SchedulerArbiter()
    forced = _candidate("force", candidate_class="forced", planned=now + timedelta(hours=2), force_priority=999)
    due = _candidate("due", candidate_class="scheduled", planned=now - timedelta(minutes=5))
    manual = _candidate(
        "manual", candidate_class="manual", planned=now,
        origin=JobOrigin.MANUAL, manual_at=now,
    )
    assert arbiter.select([forced, due], lease_owner=None).job_id == "due"
    assert arbiter.select([forced, due, manual], lease_owner=None).job_id == "manual"


def test_forced_candidates_sort_by_priority_then_planned_start():
    now = datetime(2026, 8, 23, 20, 0)
    arbiter = SchedulerArbiter()
    low = _candidate("low", candidate_class="forced", planned=now + timedelta(minutes=30), force_priority=10)
    high_late = _candidate("high-late", candidate_class="forced", planned=now + timedelta(hours=2), force_priority=20)
    high_early = _candidate("high-early", candidate_class="forced", planned=now + timedelta(hours=1), force_priority=20)
    assert arbiter.select([low, high_late, high_early], lease_owner=None).job_id == "high-early"


def test_force_probe_is_read_only_and_force_loser_never_enters_wait():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    evaluate = source[source.index("def _force_readiness"):source.index("def _set_execution_lease_wait")]
    assert ".transition(ZoneJobState.WAIT" not in evaluate
    assert ".finish(" not in evaluate
    assert "async_start_ready_zones" not in evaluate
    arbitrate = source[source.index("async def _arbitrate_jobs"):source.index("async def async_reconcile_current_timeline")]
    assert 'if candidate.candidate_class == "forced":' in arbitrate
    assert "continue" in arbitrate
    assert "self._set_execution_lease_wait" in arbitrate
    # Explicit contract: the force branch skips before the normal lease-WAIT path.
    force_branch = arbitrate.index('if candidate.candidate_class == "forced":')
    wait_call = arbitrate.rindex("self._set_execution_lease_wait")
    assert force_branch < wait_call


def test_selected_force_is_same_occurrence_and_persists_audit_snapshot():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    arbitrate = source[source.index("async def _arbitrate_jobs"):source.index("async def async_reconcile_current_timeline")]
    assert 'selected_job.metadata["force_execution"] = force_execution' in arbitrate
    assert '"planned_start": existing_force.get("planned_start") or selected_job.planned_start.isoformat()' in arbitrate
    assert 'existing_force.get("advance_seconds_at_selection", advance_seconds)' in arbitrate
    assert 'existing_force.get("condition_evaluation", {})' in arbitrate
    assert '"force_execution_started"' in arbitrate
    assert "manual_release_at =" not in arbitrate
    assert "uuid4" not in arbitrate

def test_force_start_uses_execution_manager_and_existing_at_most_once_barrier():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    arbitrate = engine[engine.index("async def _arbitrate_jobs"):engine.index("async def async_reconcile_current_timeline")]
    assert "await self.execution.async_start_ready_zones(" in arbitrate
    assert "await self._persist_barrier()" in backend
    assert "COMMAND_INTENT" in backend



def test_committed_force_releases_same_job_for_progressive_followup_without_manual_priority():
    job_source = (MODULE / "job.py").read_text(encoding="utf-8")
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    effective = job_source[job_source.index("def effective_start"):job_source.index("def record_user_action")]
    assert 'self.metadata.get("force_execution")' in effective
    assert 'force.get("selected_at")' in effective
    assert "self.manual_release_at" in effective
    arbitrate = engine[engine.index("async def _arbitrate_jobs"):engine.index("async def async_reconcile_current_timeline")]
    assert 'and instant_lt(now, job.planned_start)' in arbitrate
    assert 'candidate_class="forced"' in arbitrate
    assert 'force_priority=int(force_execution.get("priority", 0) or 0)' in arbitrate

def test_frontend_no_longer_claims_force_is_evaluation_only():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    ru = (MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8")
    assert "panel.force_executed" in panel
    assert "panel.force_deferred_scheduled" in panel
    assert "Досрочный физический запуск ещё не выполняется" not in ru
