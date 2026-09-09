"""Acceptance contracts for Vacuum Schedule 0.8.0 restart/recovery hardening."""
from pathlib import Path
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from execution_models import ExecutionAttempt, ExecutionAttemptState, ExecutionMode  # noqa: E402




def test_startup_recovery_precedes_live_callbacks_and_normal_reconcile():
    engine=(MODULE / "scheduler_engine.py").read_text()
    start=engine[engine.index("async def async_start(self)"):engine.index("async def async_stop(self)")]
    assert start.index("await self._async_startup_recovery()") < start.index("self._subscribe_zone_inputs()")
    assert start.index("await self._async_startup_recovery()") < start.index("self._subscribe_notification_actions()")
    assert start.index("await self._async_startup_recovery()") < start.index("await self.async_reconcile()")
    assert start.index("await self._async_startup_recovery()") < start.index("async_track_time_interval")


def test_post_command_recovery_is_observation_only_and_never_restarts_robot():
    manager=(MODULE / "execution_manager.py").read_text()
    section=manager[manager.index("async def async_recover_job"):manager.index("async def async_progress_job")]
    assert "ExecutionAttemptState.COMMAND_INTENT" in section
    assert "ExecutionAttemptState.START_REQUESTED" in section
    assert "await backend.async_progress(attempt, now)" in section
    # Only CREATED/PREPARING may call async_start in recovery.
    start_call=section.index("await backend.async_start(attempt, now)")
    precommand_guard=section.index("ExecutionAttemptState.PREPARING")
    assert precommand_guard < start_call
    after_start=section[start_call + len("await backend.async_start(attempt, now)"):]
    assert "async_start_cleaning" not in after_start


def test_terminal_attempts_are_resynchronized_to_zones():
    manager=(MODULE / "execution_manager.py").read_text()
    progress=manager[manager.index("async def async_progress_job"):manager.index("async def async_cancel_job")]
    assert "changed |= self._sync_attempt_to_zones(job, attempt, now)" in progress
    assert progress.index("changed |= self._sync_attempt_to_zones(job, attempt, now)") > progress.index("if not attempt.terminal")


def test_multi_pass_crash_window_repairs_missing_successor_marker():
    manager=(MODULE / "execution_manager.py").read_text()
    assert "def _repair_stale_next_pass_marker" in manager
    assert 'attempt.metadata.pop("next_pass_attempt_id", None)' in manager
    assert 'str(next_id) not in job.execution_attempts' in manager


def test_offline_backfill_is_terminal_and_never_a_physical_catchup_queue():
    engine=(MODULE / "scheduler_engine.py").read_text()
    section=engine[engine.index("def _backfill_offline_occurrences"):engine.index("async def _async_startup_recovery")]
    assert "JobReason.MISSED_WHILE_OFFLINE.value" in section
    assert 'recovered.metadata["suppress_notifications"] = True' not in section  # set centrally in constructor
    assert 'recovered.finish(JobResult.FAILED' in section
    assert 'self.store.archive(recovered)' in section
    assert "async_start_ready_zones" not in section
    assert "async_start_cleaning" not in section
    assert 'current_recovered += 1' in section


def test_offline_backfill_requires_persisted_anchor_and_matching_revision():
    engine=(MODULE / "scheduler_engine.py").read_text()
    assert "def _startup_anchor_jobs" in engine
    section=engine[engine.index("def _backfill_offline_occurrences"):engine.index("async def _async_startup_recovery")]
    assert "for schedule_id, anchor in anchors.items()" in section
    assert "schedule.revision != anchor.schedule_revision" in section


def test_historical_recovery_reason_is_localized_and_notifications_are_suppressed():
    job=(MODULE / "job.py").read_text()
    notify=(MODULE / "notification_manager.py").read_text()
    assert 'MISSED_WHILE_OFFLINE = "missed_while_offline"' in job
    assert 'job.metadata.get("suppress_notifications")' in notify
    for lang in ("en", "ru"):
        catalog=(MODULE / "frontend" / "localization" / f"{lang}.json").read_text()
        assert 'common.reason.missed_while_offline' in catalog


def test_missing_confirmation_deadline_is_rebuilt_without_command_retry():
    manager=(MODULE / "execution_manager.py").read_text()
    section=manager[manager.index("async def async_recover_job"):manager.index("async def async_progress_job")]
    assert "startup_rebuilt_start_deadline" in section
    assert "REAL_START_TIMEOUT" in section
    assert "await backend.async_progress(attempt, now)" in section


def test_command_intent_round_trip_keeps_at_most_once_barrier():
    now=datetime(2026,8,17,12,0)
    attempt=ExecutionAttempt.create(
        job_id="j", execution_mode=ExecutionMode.REAL, zone_ids=("z",),
        target_type="segment", targets=("1",), cleaning_params={}, now=now
    )
    attempt.state=ExecutionAttemptState.COMMAND_INTENT
    attempt.command_intent_at=now
    restored=ExecutionAttempt.from_dict(attempt.to_dict())
    assert restored.state is ExecutionAttemptState.COMMAND_INTENT
    assert restored.command_intent_at == now




def test_expired_precommand_recovery_cannot_emit_a_late_start():
    engine=(MODULE / "scheduler_engine.py").read_text()
    manager=(MODULE / "execution_manager.py").read_text()
    startup=engine[engine.index("async def _async_startup_recovery"):engine.index("def _subscribe_zone_inputs")]
    recovery=manager[manager.index("async def async_recover_job"):manager.index("async def async_progress_job")]
    assert "precommand_abort_reason" in startup
    assert "JobReason.DEADLINE_EXPIRED.value" in startup
    assert "JobReason.DISPLACED_BY_NEXT_OCCURRENCE.value" not in startup
    assert "if precommand_abort_reason:" in recovery
    abort_branch=recovery[recovery.index("if precommand_abort_reason:"):recovery.index("else:\n                    attempt.metadata[\"startup_recovered_before_command\"]")]
    assert "async_start(attempt" not in abort_branch
    assert "ExecutionAttemptState.FAILED" in abort_branch


def test_deferred_offline_backfill_runs_before_future_materialization_after_recovered_execution_finishes():
    engine=(MODULE / "scheduler_engine.py").read_text()
    reconcile=engine[engine.index("async def async_reconcile(self)"):engine.index("async def async_recheck_jobs")]
    assert "_pending_offline_anchors" in engine
    assert "def _continue_offline_backfill" in engine
    marker="# Finishing a recovered execution can reveal additional occurrences"
    tail=reconcile[reconcile.index(marker):]
    assert tail.index("_continue_offline_backfill(planner, schedules, now)") < tail.index("_materialize_missing(planner, schedules, now)")
