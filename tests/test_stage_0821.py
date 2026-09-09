"""Regression and behavioral contracts for Vacuum Schedule 0.9.0."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))

# Load the execution modules as a package without executing the integration
# __init__.py (the isolated test environment intentionally has no Home Assistant).
pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)
ha = ModuleType("homeassistant")
ha_helpers = ModuleType("homeassistant.helpers")
ha_entity_component = ModuleType("homeassistant.helpers.entity_component")
ha_entity_component.DATA_INSTANCES = "entity_components"
ha_entity_registry = ModuleType("homeassistant.helpers.entity_registry")
ha_entity_registry.async_get = lambda hass: None
sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.entity_component", ha_entity_component)
sys.modules.setdefault("homeassistant.helpers.entity_registry", ha_entity_registry)

from custom_components.vacuum_schedule.execution_backend import RealExecutionBackend  # noqa: E402
from custom_components.vacuum_schedule.execution_models import ExecutionAttempt, ExecutionAttemptState, ExecutionMode  # noqa: E402
from custom_components.vacuum_schedule.execution_observer import RobotExecutionObservation, RobotExecutionPhase  # noqa: E402
from custom_components.vacuum_schedule.models import NormalizedVacuumState  # noqa: E402

PANEL = MODULE / "frontend" / "panel.js"


def _attempt(now: datetime) -> ExecutionAttempt:
    attempt = ExecutionAttempt.create(
        job_id="job",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("zone",),
        target_type="segment",
        targets=("0_17",),
        cleaning_params={"mop_mode": "fast", "passes": 1},
        now=now,
    )
    attempt.state = ExecutionAttemptState.ROBOT_SERVICE
    attempt.start_confirmed_at = now - timedelta(minutes=10)
    return attempt


class _Adapter:
    def __init__(self, observation: RobotExecutionObservation) -> None:
        self._observation = observation

    def observe(self, now: datetime) -> RobotExecutionObservation:
        return self._observation


class _ResourceProvider:
    def __init__(self, *, dirty_ok: bool) -> None:
        self.bindings = {
            "dock.dirty_water": SimpleNamespace(binding_mode=SimpleNamespace(value="manual")),
        }
        self._dirty_ok = dirty_ok
        self.allow_flags: list[bool] = []

    def snapshot(self, now: datetime, *, allow_test_overrides: bool = True):
        self.allow_flags.append(allow_test_overrides)
        item = SimpleNamespace(
            effective_available=True,
            effective_value=self._dirty_ok,
            source_entity_id="binary_sensor.dirty_water",
            status="ready",
        )
        return SimpleNamespace(values={"dock.dirty_water": item})


def _backend(observation: RobotExecutionObservation, provider=None) -> RealExecutionBackend:
    # Bypass the production constructor so these tests exercise only the state
    # machine under test without requiring a complete Home Assistant entity component.
    backend = object.__new__(RealExecutionBackend)
    backend.hass = None
    backend.executor = None
    backend.clock = None
    backend.adapter = _Adapter(observation)
    backend._persist_callback = None
    backend.restore_previous_settings = False
    backend.input_provider = provider
    backend.set_runtime_error_recovery_minutes(30)
    return backend


def _obs(now: datetime, *, phase: RobotExecutionPhase, service=True, in_cleaning=1,
         dock_error=None, raw_dock_error=None, blockers=()) -> RobotExecutionObservation:
    return RobotExecutionObservation(
        observed_at=now,
        normalized_state=NormalizedVacuumState.DOCKED if service else NormalizedVacuumState.ERROR,
        phase=phase,
        vendor="roborock",
        vendor_status="washing_the_mop" if service else "error",
        task_kind=None,
        session_active=service or phase in {RobotExecutionPhase.CLEANING, RobotExecutionPhase.SERVICE_BLOCKED},
        in_cleaning=in_cleaning,
        dock_error=dock_error,
        raw_dock_error=raw_dock_error or dock_error,
        resource_blockers=tuple(blockers),
        service_activity=service,
    )




def test_runtime_resource_check_disables_dry_run_overrides():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    provider = _ResourceProvider(dirty_ok=False)
    backend = _backend(_obs(now, phase=RobotExecutionPhase.SERVICE), provider)
    blockers = backend._configured_resource_blockers(_attempt(now), now)
    assert blockers == ("dirty_water_full",)
    assert provider.allow_flags == [False]


def test_washing_mop_with_confirmed_dirty_tank_becomes_service_blocked_not_failed():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    # Simulate an opaque dock error: the observer cannot map it, but the configured
    # resource sensor authoritatively says the dirty-water tank needs service.
    observation = _obs(
        now,
        phase=RobotExecutionPhase.ERROR,
        service=True,
        in_cleaning=1,
        dock_error="dock_status_38",
        raw_dock_error="dock_status_38",
    )
    backend = _backend(observation, _ResourceProvider(dirty_ok=False))
    attempt = _attempt(now)
    changed = asyncio.run(backend.async_progress(attempt, now))
    assert changed
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
    assert attempt.failure_reason is None
    assert attempt.metadata["resource_blockers"] == ["dirty_water_full"]
    assert attempt.metadata["suppressed_service_error"]["raw_dock_error"] == "dock_status_38"


def test_post_clean_resource_block_preserves_success_and_does_not_fail_job():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    backend = _backend(
        _obs(now, phase=RobotExecutionPhase.SERVICE, service=True, in_cleaning=0),
        _ResourceProvider(dirty_ok=False),
    )
    attempt = _attempt(now)
    attempt.metadata["cleaning_observed_at"] = (now - timedelta(minutes=5)).isoformat()
    changed = asyncio.run(backend.async_progress(attempt, now))
    assert changed
    assert attempt.state is ExecutionAttemptState.COMPLETED
    assert attempt.failure_reason is None
    assert attempt.metadata["post_clean_resource_blockers"] == ["dirty_water_full"]
    assert attempt.metadata["post_clean_service_incomplete"] is True
    assert attempt.metadata["completion_evidence"] == "floor_done_resource_service_unavailable"


def test_preclean_service_block_never_guesses_success_from_in_cleaning_zero():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    backend = _backend(
        _obs(now, phase=RobotExecutionPhase.SERVICE, service=True, in_cleaning=0),
        _ResourceProvider(dirty_ok=False),
    )
    attempt = _attempt(now)
    # No cleaning_observed_at and no last_clean_end: resource trouble happened
    # before we ever observed actual floor cleaning.
    changed = asyncio.run(backend.async_progress(attempt, now))
    assert changed
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
    assert attempt.completed_at is None


def test_confirmed_runtime_error_is_recoverable_and_keeps_diagnostics():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    observation = _obs(
        now,
        phase=RobotExecutionPhase.ERROR,
        service=False,
        in_cleaning=0,
        dock_error="fan_motor_fault",
    )
    backend = _backend(observation, None)
    attempt = _attempt(now)
    changed = asyncio.run(backend.async_progress(attempt, now))
    assert changed
    assert attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED
    assert attempt.failure_reason is None
    assert attempt.metadata["physical_error"]["dock_error"] == "fan_motor_fault"
    assert attempt.error_recovery_deadline_at == now + timedelta(minutes=30)


def test_prestart_hard_physical_error_still_fails():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    observation = _obs(
        now, phase=RobotExecutionPhase.ERROR, service=False, in_cleaning=0,
        dock_error="fan_motor_fault",
    )
    backend = _backend(observation, None)
    attempt = _attempt(now)
    attempt.start_confirmed_at = None
    attempt.state = ExecutionAttemptState.START_REQUESTED
    changed = asyncio.run(backend.async_progress(attempt, now))
    assert changed
    assert attempt.state is ExecutionAttemptState.FAILED
    assert attempt.failure_reason == "execution_failed"


def test_service_blocked_state_round_trips():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    attempt = _attempt(now)
    attempt.state = ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
    attempt.metadata["resource_blockers"] = ["dirty_water_full"]
    restored = ExecutionAttempt.from_dict(attempt.to_dict())
    assert restored.state is ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
    assert restored.metadata["resource_blockers"] == ["dirty_water_full"]


def test_structural_readiness_and_runtime_status_are_separate_contracts():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    assert "async def async_real_execution_readiness" in engine
    assert "async def async_real_execution_runtime_status" in engine
    assert "self.preflight.evaluate(" in engine[engine.index("async def async_real_execution_runtime_status"): ]
    assert '"runtime_status": real_runtime_status' in frontend
    assert 'runtime_status = await scheduler.async_real_execution_runtime_status()' in frontend
    assert 'preview["runtime_status"] = runtime_status' in frontend
    assert 'preview["may_start_immediately"] = False' in frontend


def test_settings_ui_keeps_runtime_preview_and_promotes_only_attention_blockers():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_runtimeAttentionBlockers" in panel
    assert "panel.action_required" in panel
    assert "panel.real_mode_runtime_blocked_warning" in panel
    assert 'return this._quietStatusHtml(value,tone,"resource-state")' in panel
    assert ".quiet-status-bad .quiet-status-dot" in panel
    assert "resource-runtime-${runtimeTone}" in panel
    assert "_bindingRuntimeTone" in panel


def test_execution_history_surfaces_resource_and_hard_error_diagnostics():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_attemptDiagnosticHtml" in panel
    assert "panel.resource_blockers" in panel
    assert "panel.robot_error_detail" in panel
    assert "panel.dock_error_detail" in panel
    manager = (MODULE / "execution_manager.py").read_text(encoding="utf-8")
    assert "ExecutionAttemptState.ROBOT_SERVICE_BLOCKED" in manager
    assert "JobReason.RESOURCE_BLOCKED_UNTIL_DEADLINE.value" in manager
    assert "instant_ge(now, job.deadline_at)" in manager


