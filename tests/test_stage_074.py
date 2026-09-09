"""Regression contracts for Vacuum Schedule 0.7.4 startup materialization repair."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_startup_runs_one_time_materialization_normalization_before_reconcile():
    engine = (MODULE / "scheduler_engine.py").read_text()
    start = engine[engine.index("async def async_start"):engine.index("async def async_stop")]
    assert "_migrate_pre_074_materialization()" in start
    assert start.index("_migrate_pre_074_materialization()") < start.index("await self.async_reconcile()")


def test_074_migration_rebuilds_only_unstarted_planned_jobs_without_failure():
    migrations = (MODULE / "migrations.py").read_text()
    section = migrations[migrations.index("def migrate_pre_074_materialization"):migrations.index("def cleanup_legacy_calendar_entities")]
    assert "job.state is JobState.PLANNED" in section
    assert "not job.execution_attempts" in section
    assert "engine.store.remove_active(job.job_id)" in section
    assert '"statistics_eligible": False' in section
    assert "JobState.WAIT" not in section
    assert "JobState.STARTING" not in section
    assert "JobState.RUNNING" not in section
    assert "_finish(" not in section


def test_074_dry_run_migration_opens_fresh_occurrence_namespace():
    engine = (MODULE / "scheduler_engine.py").read_text()
    migrations = (MODULE / "migrations.py").read_text()
    section = migrations[migrations.index("def migrate_pre_074_materialization"):migrations.index("def cleanup_legacy_calendar_entities")]
    assert "if engine.execution_mode is ExecutionMode.DRY_RUN:" in section
    assert "engine._dry_run_timeline_generation += 1" in section
    assert "timeline_materialization_schema" in engine


def test_nearest_occurrence_contract_remains_planned_start_only():
    engine = (MODULE / "scheduler_engine.py").read_text()
    section = engine[
        engine.index("def _timeline_candidate_occurrence"):
        engine.index("def _dematerialize_stale_future_jobs")
    ]
    assert "next_for_schedule(schedule, now, inclusive=True)" in section
    assert "current_relevant_for_schedule" not in section
    assert "planned_start >= now" in section


