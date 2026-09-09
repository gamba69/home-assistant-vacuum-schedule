"""Runtime robot-error recovery contracts for Vacuum Schedule 0.10.4."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))

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
from custom_components.vacuum_schedule.execution_models import (  # noqa: E402
    ExecutionAttempt,
    ExecutionAttemptState,
    ExecutionMode,
)
from custom_components.vacuum_schedule.execution_observer import (  # noqa: E402
    RobotExecutionObservation,
    RobotExecutionPhase,
)
from custom_components.vacuum_schedule.models import NormalizedVacuumState  # noqa: E402

PANEL = MODULE / "frontend" / "panel.js"


class _Adapter:
    def __init__(self, observation: RobotExecutionObservation) -> None:
        self.observation = observation

    def observe(self, now: datetime) -> RobotExecutionObservation:
        return self.observation


def _obs(
    now: datetime,
    phase: RobotExecutionPhase,
    *,
    in_cleaning: int | None = 0,
    error: str | None = None,
    session_active: bool | None = None,
    last_clean_end: datetime | None = None,
) -> RobotExecutionObservation:
    if session_active is None:
        session_active = phase in {
            RobotExecutionPhase.CLEANING,
            RobotExecutionPhase.PAUSED,
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
        } or in_cleaning not in (None, 0)
    normalized = {
        RobotExecutionPhase.CLEANING: NormalizedVacuumState.CLEANING,
        RobotExecutionPhase.PAUSED: NormalizedVacuumState.PAUSED,
        RobotExecutionPhase.IDLE: NormalizedVacuumState.IDLE,
        RobotExecutionPhase.ERROR: NormalizedVacuumState.ERROR,
    }.get(phase, NormalizedVacuumState.DOCKED)
    return RobotExecutionObservation(
        observed_at=now,
        normalized_state=normalized,
        phase=phase,
        raw_state=normalized.value,
        vendor="roborock",
        vendor_status="segment_cleaning" if phase is RobotExecutionPhase.CLEANING else phase.value.lower(),
        task_kind="segment" if phase is RobotExecutionPhase.CLEANING else None,
        session_active=bool(session_active),
        in_cleaning=in_cleaning,
        vacuum_error=error,
        raw_vacuum_error=error,
        last_clean_end=last_clean_end,
    )


def _backend(observation: RobotExecutionObservation, minutes: int = 30) -> RealExecutionBackend:
    backend = object.__new__(RealExecutionBackend)
    backend.hass = None
    backend.executor = None
    backend.clock = None
    backend.adapter = _Adapter(observation)
    backend._persist_callback = None
    backend.restore_previous_settings = False
    backend.input_provider = None
    backend.set_runtime_error_recovery_minutes(minutes)
    return backend


def _attempt(now: datetime, *, confirmed: bool = True) -> ExecutionAttempt:
    attempt = ExecutionAttempt.create(
        job_id="job",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("zone",),
        target_type="segment",
        targets=("0_17",),
        cleaning_params={"passes": 1},
        now=now,
    )
    attempt.state = ExecutionAttemptState.RUNNING if confirmed else ExecutionAttemptState.START_REQUESTED
    attempt.start_confirmed_at = now - timedelta(minutes=10) if confirmed else None
    if confirmed:
        attempt.metadata["cleaning_observed_at"] = (now - timedelta(minutes=9)).isoformat()
    return attempt




def test_confirmed_error_enters_nonterminal_recovery_and_sets_exact_deadline():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now)
    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED
    assert not attempt.terminal
    assert attempt.failure_reason is None
    assert attempt.runtime_error_at == now
    assert attempt.error_recovery_deadline_at == now + timedelta(minutes=30)
    incident = attempt.metadata["runtime_incidents"][-1]
    assert incident["status"] == "active"
    assert incident["initial_error"]["vacuum_error"] == "main_brush_jammed"
    assert backend.next_transition_at(attempt) == now + timedelta(minutes=30)


def test_error_recovery_to_cleaning_resumes_same_attempt_without_new_start():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now)
    attempt_id = attempt.attempt_id
    asyncio.run(backend.async_progress(attempt, now))

    resumed = now + timedelta(minutes=3)
    backend.adapter.observation = _obs(
        resumed, RobotExecutionPhase.CLEANING, in_cleaning=1, session_active=True
    )
    assert asyncio.run(backend.async_progress(attempt, resumed))
    assert attempt.attempt_id == attempt_id
    assert attempt.state is ExecutionAttemptState.RUNNING
    assert attempt.failure_reason is None
    assert attempt.runtime_error_at is None
    assert attempt.error_recovery_deadline_at is None
    incident = attempt.metadata["runtime_incidents"][-1]
    assert incident["status"] == "recovered"
    assert incident["duration_seconds"] == 180
    assert attempt.metadata["runtime_error_recovered_at"] == resumed.isoformat()


def test_error_clear_to_idle_does_not_guess_success_or_release_ownership():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now)
    asyncio.run(backend.async_progress(attempt, now))

    idle = now + timedelta(minutes=2)
    backend.adapter.observation = _obs(
        idle, RobotExecutionPhase.IDLE, in_cleaning=0, session_active=False
    )
    assert not asyncio.run(backend.async_progress(attempt, idle))
    assert attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED
    assert not attempt.terminal
    assert attempt.metadata["runtime_incidents"][-1]["status"] == "active"


def test_explicit_last_clean_end_leaves_error_recovery_for_completion_protocol():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now)
    asyncio.run(backend.async_progress(attempt, now))

    done = now + timedelta(minutes=2)
    backend.adapter.observation = _obs(
        done,
        RobotExecutionPhase.IDLE,
        in_cleaning=0,
        session_active=False,
        last_clean_end=done,
    )
    assert asyncio.run(backend.async_progress(attempt, done))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert attempt.failure_reason is None
    assert attempt.metadata["floor_cleaning_finished_at"] == done.isoformat()
    assert attempt.metadata["runtime_incidents"][-1]["status"] == "recovered"


def test_recovery_timeout_is_terminal_with_stable_reason_and_incident_status():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"), 30)
    attempt = _attempt(now)
    asyncio.run(backend.async_progress(attempt, now))

    deadline = now + timedelta(minutes=30)
    backend.adapter.observation = _obs(deadline, RobotExecutionPhase.ERROR, error="main_brush_jammed")
    assert asyncio.run(backend.async_progress(attempt, deadline))
    assert attempt.state is ExecutionAttemptState.FAILED
    assert attempt.failure_reason == "robot_error_recovery_timeout"
    assert attempt.completed_at == deadline
    incident = attempt.metadata["runtime_incidents"][-1]
    assert incident["status"] == "timeout"
    assert incident["duration_seconds"] == 1800


def test_prestart_error_retains_immediate_failure_semantics():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now, confirmed=False)
    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.FAILED
    assert attempt.failure_reason == "execution_failed"
    assert "runtime_incidents" not in attempt.metadata


def test_error_recovery_state_and_deadline_round_trip():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.ERROR, error="main_brush_jammed"))
    attempt = _attempt(now)
    asyncio.run(backend.async_progress(attempt, now))
    restored = ExecutionAttempt.from_dict(attempt.to_dict())
    assert restored.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED
    assert restored.runtime_error_at == now
    assert restored.error_recovery_deadline_at == now + timedelta(minutes=30)
    assert restored.metadata["runtime_incidents"][-1]["status"] == "active"


def test_source_contract_keeps_error_blocked_in_execution_lease_and_ui():
    manager = (MODULE / "execution_manager.py").read_text(encoding="utf-8")
    scheduler = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    migration = (MODULE / "migrations.py").read_text(encoding="utf-8")
    assert "ExecutionAttemptState.ROBOT_ERROR_BLOCKED" in manager
    assert "runtime_error_recovery_minutes" in scheduler
    assert 'real_execution_settings.setdefault("runtime_error_recovery_minutes", 30)' in migration
    assert "panel.active_robot_error_blocked" in panel
    assert 'String(attempt.state)==="ROBOT_ERROR_BLOCKED"' in panel
