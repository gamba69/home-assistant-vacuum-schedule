"""Regression contracts for Vacuum Schedule 0.7.4 timeline recovery."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_normal_and_explicit_materialization_are_non_retroactive():
    engine = (MODULE / "scheduler_engine.py").read_text()
    normal = engine[engine.index("def _candidate_occurrence"):engine.index("def _occurrence_consumed")]
    explicit = engine[engine.index("def _timeline_candidate_occurrence"):engine.index("def _dematerialize_stale_future_jobs")]
    assert "current_relevant_for_schedule" not in normal
    assert "next_for_schedule(schedule, now, inclusive=True)" in normal
    assert "current_relevant_for_schedule(schedule, now)" not in explicit
    assert "next_for_schedule(schedule, now, inclusive=True)" in explicit
    assert "deadline_at" in explicit and "must never make a past occurrence eligible" in explicit


def test_reset_time_starts_new_dry_run_timeline_and_reconciles_jobs():
    engine = (MODULE / "scheduler_engine.py").read_text()
    reset = engine[engine.index("async def async_reset_time"):engine.index("async def async_reset_dry_run")]
    reconcile = engine[engine.index("async def async_reconcile_current_timeline"):engine.index("async def async_reconcile(self)")]
    assert "async_reconcile_current_timeline(reset_test_clock=True)" in reset
    assert "self.clock.reset()" in reconcile
    assert "self._dry_run_timeline_generation += 1" in reconcile
    assert '"timeline_reconciled"' in reconcile
    assert '"statistics_eligible": False' in reconcile


def test_dry_run_history_is_generation_namespaced_not_deleted_by_time_reset():
    store = (MODULE / "job_store.py").read_text()
    engine = (MODULE / "scheduler_engine.py").read_text()
    assert '"dry_run_timeline_generation"' in store
    assert "_occurrence_namespace_key" in store
    assert "dry_run_timeline_generation: int | None = None" in store
    reset = engine[engine.index("async def async_reset_time"):engine.index("async def async_reset_dry_run")]
    assert "clear_dry_run_data" not in reset


def test_started_execution_is_protected_by_both_explicit_and_normal_reconciliation():
    engine = (MODULE / "scheduler_engine.py").read_text()
    explicit = engine[engine.index("async def async_reconcile_current_timeline"):engine.index("async def async_reconcile(self)")]
    normal = engine[engine.index("def _dematerialize_stale_future_jobs"):engine.index("def _materialize_missing")]
    assert "started_attempt = bool(job.execution_attempts)" in explicit
    assert "reset_test_clock and job.execution_mode is ExecutionMode.DRY_RUN" in explicit
    assert "if job.execution_attempts or job.state in (JobState.STARTING, JobState.RUNNING):" in normal


def test_explicit_recovery_endpoint_remains_for_internal_timeline_reset_compatibility():
    frontend = (MODULE / "frontend.py").read_text()
    panel = (MODULE / "frontend" / "panel.js").read_text()
    assert 'f"{DOMAIN}/scheduler/reconcile_timeline"' in frontend
    assert "websocket_scheduler_reconcile_timeline" in frontend
    assert "async_reconcile_current_timeline()" in frontend
    # The current UI replaces the old status-page restore button with the clearer
    # operator-facing schedule projection rebuild action.
    assert "button.rebuild-schedule" in panel
    assert "vacuum_schedule/scheduler/rebuild_schedule" in panel
    assert "panel.refresh_future_jobs_confirm" in panel


