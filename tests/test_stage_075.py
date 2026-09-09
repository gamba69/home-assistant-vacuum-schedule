"""Acceptance contracts for Vacuum Schedule 0.7.5 physical execution protocol."""
from pathlib import Path
import sys
from datetime import datetime
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from execution_models import ExecutionAttempt, ExecutionAttemptState, ExecutionMode  # noqa: E402




def test_physical_protocol_has_adapter_observer_and_persisted_command_intent():
    assert (MODULE / "execution_adapter.py").exists()
    assert (MODULE / "execution_observer.py").exists()
    models = (MODULE / "execution_models.py").read_text()
    backend = (MODULE / "execution_backend.py").read_text()
    assert 'COMMAND_INTENT = "COMMAND_INTENT"' in models
    assert 'command_intent_at' in models
    assert 'attempt.state = ExecutionAttemptState.COMMAND_INTENT' in backend
    assert 'await self._persist_barrier()' in backend
    assert backend.index('await self._persist_barrier()') < backend.index('await self.adapter.async_start_cleaning(attempt)')


def test_real_start_is_observation_confirmed_and_never_blindly_retried():
    backend = (MODULE / "execution_backend.py").read_text()
    assert 'attempt.state = ExecutionAttemptState.START_REQUESTED' in backend
    assert 'observation.matches_target_type(attempt.target_type)' in backend
    assert 'attempt.start_confirmed_at = attempt.start_confirmed_at or now' in backend
    assert 'ambiguous_start_command' in backend
    # No automatic second physical start inside progress/reconciliation.
    progress = backend[backend.index('async def async_progress(self, attempt: ExecutionAttempt, now: datetime) -> bool', backend.index('class RealExecutionBackend')):]
    assert 'async_start_cleaning' not in progress


def test_roborock_dock_service_is_not_treated_as_completion():
    observer = (MODULE / "execution_observer.py").read_text()
    backend = (MODULE / "execution_backend.py").read_text()
    for state in ('washing_the_mop', 'emptying_the_bin', 'going_to_wash_the_mop'):
        assert state in observer
    assert 'ExecutionAttemptState.ROBOT_SERVICE' in backend
    assert 'COMPLETION_PENDING' in backend
    assert 'ROBOROCK_COMPLETION_SETTLE' in backend


def test_real_target_validation_checks_current_segments_and_map():
    adapter = (MODULE / "execution_adapter.py").read_text()
    backend = (MODULE / "execution_backend.py").read_text()
    assert 'async_get_segments' in adapter
    assert 'robot_target_missing' in adapter
    assert 'target_map_not_active' in backend
    assert 'current_map' in backend


def test_parameter_preparation_order_readback_and_compare_restore():
    adapter = (MODULE / "execution_adapter.py").read_text()
    order = '("cleaning_mode", "cleaning_route", "mop_mode", "water_mode")'
    assert order in adapter
    assert adapter.index(order) < adapter.index('fan_mode = str(params.get("fan_mode"')
    assert '_async_wait_state_value' in adapter
    assert '_async_wait_fan_speed' in adapter
    assert 'changed_externally' in adapter
    assert 'parameter_snapshot_before' in adapter
    assert 'parameters_restored' in adapter


def test_parameter_snapshot_is_preserved_across_emulated_passes():
    manager = (MODULE / "execution_manager.py").read_text()
    adapter = (MODULE / "execution_adapter.py").read_text()
    assert 'for key in ("parameter_snapshot_before", "parameters_applied")' in manager
    assert 'snapshot: dict[str, Any] = dict(attempt.metadata.get("parameter_snapshot_before", {}))' in adapter
    assert 'snapshot.setdefault' in adapter


def test_passes_prefer_one_native_command_with_emulated_fallback():
    manager = (MODULE / "execution_manager.py").read_text()
    adapter = (MODULE / "execution_adapter.py").read_text()
    models = (MODULE / "execution_models.py").read_text()
    assert 'NATIVE = "NATIVE"' in models
    assert 'EMULATED = "EMULATED"' in models
    assert 'native_repetitions_supported(target_type, targets)' in manager
    assert 'physical_attempt_total = 1' in manager
    assert 'physical_attempt_total = requested_passes' in manager
    # Roborock native segment repetitions are one APP_SEGMENT_CLEAN command.
    assert '"app_segment_clean"' in adapter
    assert '{"segments": segments, "repeat": repeats}' in adapter
    # Coordinate zones carry the requested repeat count in the same command.
    assert '"params": [[*coords, repeats] for coords in zones]' in adapter
    # Unsupported adapters still keep the restart-safe sequential fallback.
    assert 'pass_index=previous.pass_index + 1' in manager
    assert 'pass_total=previous.pass_total' in manager


def test_shared_physical_target_waits_for_all_active_logical_owners():
    manager = (MODULE / "execution_manager.py").read_text()
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    assert 'shared_target_blocked_zone_ids' in manager
    assert 'shared_target_waiting' in scheduler
    assert 'ready = launch_ready' in scheduler


def test_cancel_requires_physical_confirmation_before_zone_is_terminal():
    backend = (MODULE / "execution_backend.py").read_text()
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    real_cancel = backend[backend.index('async def async_cancel(self, attempt: ExecutionAttempt, now: datetime)', backend.index('class RealExecutionBackend')):]
    assert 'ExecutionAttemptState.CANCEL_REQUESTED' in real_cancel
    assert 'await self.adapter.async_stop()' in real_cancel
    assert 'ExecutionAttemptState.CANCELLED' in real_cancel
    cancel_job = scheduler[scheduler.index('async def async_cancel_job'):scheduler.index('async def async_pause_job')]
    assert '_finish_unstarted_zones' in cancel_job
    assert '_finish_remaining_zones' not in cancel_job


def test_pause_resume_are_real_only_and_resume_requires_observed_pause():
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    backend = (MODULE / "execution_backend.py").read_text()
    frontend = (MODULE / "frontend.py").read_text()
    assert 'async def async_pause_job' in scheduler
    assert 'async def async_resume_job' in scheduler
    assert 'job.execution_mode is not ExecutionMode.REAL' in scheduler
    assert 'observation.phase is not RobotExecutionPhase.PAUSED' in backend
    assert '"pause", "resume"' in frontend


def test_real_readiness_is_server_enforced_before_mode_switch():
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    frontend = (MODULE / "frontend.py").read_text()
    panel = (MODULE / "frontend" / "panel.js").read_text()
    assert 'async def async_real_execution_readiness' in scheduler
    assert 'real_execution_not_ready' in scheduler
    assert 'await self.async_real_execution_readiness()' in scheduler
    assert 'settings/update_real_execution' in frontend
    assert 'real-restore-settings' in panel
    assert '_realReadinessHtml' in panel


def test_dry_run_still_has_no_physical_command_capability():
    backend = (MODULE / "execution_backend.py").read_text()
    dry = backend[backend.index('class DryRunExecutionBackend'):backend.index('class RealExecutionBackend')]
    assert 'services.async_call' not in dry
    assert 'VacuumExecutionAdapter' not in dry
    assert 'self.executor' not in dry


def test_execution_attempt_extended_state_round_trip():
    now = datetime(2026, 8, 17, 12, 30)
    attempt = ExecutionAttempt.create(
        job_id='j', execution_mode=ExecutionMode.REAL,
        zone_ids=('z1',), target_type='segment', targets=('0_16',),
        cleaning_params={'passes': 2}, now=now, pass_index=2, pass_total=2,
    )
    attempt.state = ExecutionAttemptState.COMMAND_INTENT
    attempt.command_intent_at = now
    restored = ExecutionAttempt.from_dict(attempt.to_dict())
    assert restored.execution_mode is ExecutionMode.REAL
    assert restored.state is ExecutionAttemptState.COMMAND_INTENT
    assert restored.command_intent_at == now
    assert restored.pass_index == 2 and restored.pass_total == 2 and restored.final_pass


def test_frontend_exposes_physical_substate_and_pause_resume_controls():
    panel = (MODULE / "frontend" / "panel.js").read_text()
    assert '_physicalExecutionHtml(job)' in panel
    assert 'panel.execution_substate.ROBOT_SERVICE' not in panel  # localization-driven, no inline RU/EN semantics
    assert 'button("pause"' in panel
    assert 'button("resume"' in panel
    assert 'panel.physical_execution' in panel
