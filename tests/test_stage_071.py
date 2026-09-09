"""Regression contracts for Vacuum Schedule 0.7.1 scheduling fixes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_fresh_materialization_never_uses_past_current_relevant_occurrence():
    engine = (MODULE / "scheduler_engine.py").read_text()
    section = engine[engine.index("def _candidate_occurrence"):engine.index("def _occurrence_consumed")]
    assert "current_relevant_for_schedule" not in section
    assert "next_for_schedule(schedule, now, inclusive=True)" in section
    assert "must never resurrect an occurrence whose planned_start is" in section


def test_next_job_is_materialized_immediately_but_start_guard_remains():
    engine = (MODULE / "scheduler_engine.py").read_text()
    materializer = engine[engine.index("def _materialize_missing"):engine.index("def _finish(")]
    process = engine[engine.index("async def _prepare_job"):engine.index("async def async_reconcile_current_timeline")]
    reconcile = engine[engine.index("async def async_reconcile"):engine.index("async def async_recheck_jobs")]
    assert "instant_lt(now, occurrence.warning_at)" not in materializer
    assert "JobInstance.from_occurrence(occurrence, now)" in materializer
    assert "if instant_lt(now, job.effective_start):" in process
    assert "return changed, ()" in process
    assert "Finishing a job can make the next occurrence materializable" in reconcile
    assert reconcile.count("_materialize_missing(planner, schedules, now)") >= 2


def test_pre_071_test_clock_is_reset_once_and_late_unstarted_jobs_are_removed():
    engine = (MODULE / "scheduler_engine.py").read_text()
    migrations = (MODULE / "migrations.py").read_text()
    assert "_DEBUG_STATE_SCHEMA = 1" in engine
    restore = engine[engine.index("def _restore_debug_state"):engine.index("async def _async_save_debug_state")]
    assert 'state.get("dry_run_state_schema", 0)' in restore
    assert "schema < _DEBUG_STATE_SCHEMA" in restore
    assert '"dry_run_state_schema": _DEBUG_STATE_SCHEMA' in restore
    assert "retroactive_materialization_removed_0_7_1" in migrations
    assert "job.created_at" in migrations and "job.planned_start" in migrations


