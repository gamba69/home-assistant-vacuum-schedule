"""Observation-driven physical completion contracts for Vacuum Schedule 0.11.9."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))

pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)
ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
ha_helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
ha_entity_component = sys.modules.setdefault(
    "homeassistant.helpers.entity_component", ModuleType("homeassistant.helpers.entity_component")
)
ha_entity_component.DATA_INSTANCES = "entity_components"
ha_entity_registry = sys.modules.setdefault(
    "homeassistant.helpers.entity_registry", ModuleType("homeassistant.helpers.entity_registry")
)
ha_entity_registry.async_get = lambda hass: None

from custom_components.vacuum_schedule.execution_backend import (  # noqa: E402
    ROBOROCK_COMPLETION_SETTLE,
    ROBOROCK_POST_AUTO_EMPTY_SETTLE,
    RealExecutionBackend,
)
from custom_components.vacuum_schedule.execution_models import (  # noqa: E402
    ExecutionAttempt,
    ExecutionAttemptState,
    ExecutionMode,
)
from custom_components.vacuum_schedule.execution_observer import (  # noqa: E402
    RobotExecutionObservation,
    RobotExecutionObserver,
    RobotExecutionPhase,
    _dock_service_activity,
)
from custom_components.vacuum_schedule.models import NormalizedVacuumState  # noqa: E402


class _Adapter:
    def __init__(self, observation: RobotExecutionObservation) -> None:
        self.observation = observation

    def observe(self, now: datetime) -> RobotExecutionObservation:
        return self.observation


def _backend(observation: RobotExecutionObservation) -> RealExecutionBackend:
    backend = object.__new__(RealExecutionBackend)
    backend.hass = None
    backend.executor = None
    backend.clock = None
    backend.adapter = _Adapter(observation)
    backend._persist_callback = None
    backend.restore_previous_settings = False
    backend.input_provider = None
    backend.set_runtime_error_recovery_minutes(30)
    return backend


def _attempt(now: datetime) -> ExecutionAttempt:
    attempt = ExecutionAttempt.create(
        job_id="job",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("zone",),
        target_type="segment",
        targets=("0_17",),
        cleaning_params={"mop_mode": "standard", "passes": 1},
        now=now,
    )
    attempt.state = ExecutionAttemptState.RUNNING
    attempt.start_confirmed_at = now - timedelta(minutes=10)
    attempt.metadata["cleaning_observed_at"] = (now - timedelta(minutes=9)).isoformat()
    return attempt


def _obs(
    now: datetime,
    phase: RobotExecutionPhase,
    *,
    normalized: NormalizedVacuumState | None = None,
    last_clean_end: datetime | None = None,
    in_cleaning: int | None = 0,
    service_activity: bool | None = None,
    vendor_status: str | None = None,
) -> RobotExecutionObservation:
    if normalized is None:
        normalized = {
            RobotExecutionPhase.CLEANING: NormalizedVacuumState.CLEANING,
            RobotExecutionPhase.RETURNING: NormalizedVacuumState.RETURNING,
            RobotExecutionPhase.SERVICE: NormalizedVacuumState.DOCKED,
            RobotExecutionPhase.IDLE: NormalizedVacuumState.DOCKED,
        }.get(phase, NormalizedVacuumState.IDLE)
    if service_activity is None:
        service_activity = phase is RobotExecutionPhase.SERVICE
    session_active = phase in {
        RobotExecutionPhase.CLEANING,
        RobotExecutionPhase.PAUSED,
        RobotExecutionPhase.SERVICE,
        RobotExecutionPhase.SERVICE_BLOCKED,
    }
    resolved_vendor_status = vendor_status or {
        RobotExecutionPhase.CLEANING: "segment_cleaning",
        RobotExecutionPhase.RETURNING: "returning_home",
        RobotExecutionPhase.SERVICE: "washing_the_mop",
        RobotExecutionPhase.IDLE: "charging",
    }.get(phase, phase.value.lower())
    return RobotExecutionObservation(
        observed_at=now,
        normalized_state=normalized,
        phase=phase,
        raw_state=normalized.value,
        vendor="roborock",
        vendor_status=resolved_vendor_status,
        task_kind="segment" if phase is RobotExecutionPhase.CLEANING else None,
        session_active=session_active,
        in_cleaning=in_cleaning,
        last_clean_end=last_clean_end,
        service_activity=bool(service_activity),
        auto_empty_activity=resolved_vendor_status == "emptying_the_bin",
    )


def test_last_clean_end_marks_floor_done_but_returning_does_not_complete():
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    done = now
    backend = _backend(_obs(now, RobotExecutionPhase.RETURNING, last_clean_end=done))
    attempt = _attempt(now)

    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE
    assert not attempt.terminal
    assert attempt.metadata["floor_cleaning_finished_at"] == done.isoformat()
    assert "completion_evidence" not in attempt.metadata


def test_docked_before_service_is_not_accepted_as_terminal():
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(
        _obs(now, RobotExecutionPhase.IDLE, normalized=NormalizedVacuumState.DOCKED, last_clean_end=now)
    )
    attempt = _attempt(now)

    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert not attempt.terminal
    assert backend.next_transition_at(attempt) == now + ROBOROCK_COMPLETION_SETTLE


def test_service_after_floor_done_requires_quiet_settle_before_completion():
    floor_done = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(floor_done, RobotExecutionPhase.RETURNING, last_clean_end=floor_done))
    attempt = _attempt(floor_done)
    asyncio.run(backend.async_progress(attempt, floor_done))

    service_at = floor_done + timedelta(seconds=4)
    backend.adapter.observation = _obs(
        service_at, RobotExecutionPhase.SERVICE, last_clean_end=floor_done, in_cleaning=1
    )
    assert asyncio.run(backend.async_progress(attempt, service_at))
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE
    assert not attempt.terminal
    assert attempt.metadata.get("post_clean_service_observed_at") == service_at.isoformat()

    docked_at = floor_done + timedelta(seconds=12)
    backend.adapter.observation = _obs(
        docked_at,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, docked_at))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert not attempt.terminal
    assert attempt.completion_candidate_at == docked_at

    due = docked_at + ROBOROCK_COMPLETION_SETTLE
    backend.adapter.observation = _obs(
        due,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, due))
    assert attempt.state is ExecutionAttemptState.COMPLETED
    assert attempt.completed_at == due
    assert attempt.metadata["completion_evidence"] == "floor_done_and_dock_quiescent"


def test_confirmed_auto_empty_uses_only_short_final_quiet_debounce():
    floor_done = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(floor_done, RobotExecutionPhase.RETURNING, last_clean_end=floor_done))
    attempt = _attempt(floor_done)
    asyncio.run(backend.async_progress(attempt, floor_done))

    emptying = floor_done + timedelta(seconds=5)
    backend.adapter.observation = _obs(
        emptying,
        RobotExecutionPhase.SERVICE,
        last_clean_end=floor_done,
        in_cleaning=1,
        vendor_status="emptying_the_bin",
    )
    assert asyncio.run(backend.async_progress(attempt, emptying))
    assert attempt.metadata["post_clean_auto_empty_observed_at"] == emptying.isoformat()

    quiet = emptying + timedelta(seconds=8)
    backend.adapter.observation = _obs(
        quiet,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, quiet))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert backend.next_transition_at(attempt) == quiet + ROBOROCK_POST_AUTO_EMPTY_SETTLE

    almost = quiet + ROBOROCK_POST_AUTO_EMPTY_SETTLE - timedelta(seconds=1)
    backend.adapter.observation = _obs(
        almost,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert not asyncio.run(backend.async_progress(attempt, almost))
    assert not attempt.terminal

    due = quiet + ROBOROCK_POST_AUTO_EMPTY_SETTLE
    backend.adapter.observation = _obs(
        due,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, due))
    assert attempt.state is ExecutionAttemptState.COMPLETED


def test_optional_dock_diagnostics_participate_in_service_ownership():
    assert _dock_service_activity(
        "charging", dust_collection_status="collecting"
    ) == (True, True)
    assert _dock_service_activity(
        "charging", wash_status="washing"
    ) == (True, False)
    assert _dock_service_activity(
        "charging", wash_status="idle", dust_collection_status="idle"
    ) == (False, False)
    assert _dock_service_activity(
        "charging", wash_status="ready", dust_collection_status=False
    ) == (False, False)


def test_mid_clean_service_does_not_authorize_first_dock_after_final_floor_done():
    base = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(base, RobotExecutionPhase.SERVICE, in_cleaning=1))
    attempt = _attempt(base)
    assert asyncio.run(backend.async_progress(attempt, base))
    assert "post_clean_service_observed_at" not in attempt.metadata

    resumed = base + timedelta(seconds=5)
    backend.adapter.observation = _obs(
        resumed, RobotExecutionPhase.CLEANING, in_cleaning=1
    )
    asyncio.run(backend.async_progress(attempt, resumed))

    floor_done = base + timedelta(seconds=20)
    backend.adapter.observation = _obs(
        floor_done,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, floor_done))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert not attempt.terminal
    assert "post_clean_service_observed_at" not in attempt.metadata

    final_service = base + timedelta(seconds=23)
    backend.adapter.observation = _obs(
        final_service, RobotExecutionPhase.SERVICE, last_clean_end=floor_done, in_cleaning=1
    )
    assert asyncio.run(backend.async_progress(attempt, final_service))
    assert attempt.metadata["post_clean_service_observed_at"] == final_service.isoformat()

    final_dock = base + timedelta(seconds=30)
    backend.adapter.observation = _obs(
        final_dock,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, final_dock))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert not attempt.terminal
    due = final_dock + ROBOROCK_COMPLETION_SETTLE
    backend.adapter.observation = _obs(
        due,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, due))
    assert attempt.state is ExecutionAttemptState.COMPLETED


def test_late_last_clean_end_promotes_service_that_really_happened_after_floor_done():
    # The diagnostic entity can publish last_clean_end after the dock service
    # event.  The *timestamp carried by last_clean_end* is what matters: here
    # floor cleaning ended one second before the SERVICE observation, so that
    # already-seen service is legitimately post-clean once the late diagnostic
    # arrives.
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, RobotExecutionPhase.SERVICE, in_cleaning=1))
    attempt = _attempt(now)
    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.metadata.get("post_clean_service_observed_at") is None
    assert attempt.metadata.get("latest_service_observed_at") == now.isoformat()

    docked_at = now + timedelta(seconds=8)
    actual_floor_done = now - timedelta(seconds=1)
    backend.adapter.observation = _obs(
        docked_at,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=actual_floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, docked_at))
    assert attempt.metadata.get("floor_cleaning_finished_at") == actual_floor_done.isoformat()
    assert attempt.metadata.get("post_clean_service_observed_at") == now.isoformat()
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    due = docked_at + ROBOROCK_COMPLETION_SETTLE
    backend.adapter.observation = _obs(
        due,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=actual_floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, due))
    assert attempt.state is ExecutionAttemptState.COMPLETED


def test_missing_service_telemetry_uses_bounded_roborock_fallback():
    now = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(
        _obs(now, RobotExecutionPhase.IDLE, normalized=NormalizedVacuumState.IDLE, last_clean_end=now)
    )
    attempt = _attempt(now)
    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING

    almost = now + ROBOROCK_COMPLETION_SETTLE - timedelta(seconds=1)
    backend.adapter.observation = _obs(
        almost, RobotExecutionPhase.IDLE, normalized=NormalizedVacuumState.IDLE, last_clean_end=now
    )
    assert not asyncio.run(backend.async_progress(attempt, almost))
    assert not attempt.terminal

    due = now + ROBOROCK_COMPLETION_SETTLE
    backend.adapter.observation = _obs(
        due, RobotExecutionPhase.IDLE, normalized=NormalizedVacuumState.IDLE, last_clean_end=now
    )
    assert asyncio.run(backend.async_progress(attempt, due))
    assert attempt.state is ExecutionAttemptState.COMPLETED
    assert attempt.metadata["completion_evidence"] == "settle_timeout_fallback"


def test_late_auto_empty_reclaims_lease_during_post_service_quiet_window():
    floor_done = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(floor_done, RobotExecutionPhase.RETURNING, last_clean_end=floor_done))
    attempt = _attempt(floor_done)
    asyncio.run(backend.async_progress(attempt, floor_done))

    wash = floor_done + timedelta(seconds=5)
    backend.adapter.observation = _obs(wash, RobotExecutionPhase.SERVICE, last_clean_end=floor_done, in_cleaning=1)
    assert asyncio.run(backend.async_progress(attempt, wash))

    quiet = floor_done + timedelta(seconds=20)
    backend.adapter.observation = _obs(
        quiet,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, quiet))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert attempt.completion_candidate_at == quiet

    # Auto-empty starts later, before the quiet settle expires. The attempt must
    # remain owned and the previous completion candidate must be discarded.
    emptying = quiet + timedelta(seconds=45)
    backend.adapter.observation = _obs(
        emptying,
        RobotExecutionPhase.SERVICE,
        last_clean_end=floor_done,
        in_cleaning=1,
        vendor_status="emptying_the_bin",
    )
    assert asyncio.run(backend.async_progress(attempt, emptying))
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE
    assert attempt.completion_candidate_at is None
    assert not attempt.terminal

    final_quiet = emptying + timedelta(seconds=15)
    backend.adapter.observation = _obs(
        final_quiet,
        RobotExecutionPhase.IDLE,
        normalized=NormalizedVacuumState.DOCKED,
        last_clean_end=floor_done,
        in_cleaning=0,
    )
    assert asyncio.run(backend.async_progress(attempt, final_quiet))
    assert attempt.state is ExecutionAttemptState.COMPLETION_PENDING
    assert attempt.completion_candidate_at == final_quiet


def test_observer_dependency_contract_exposes_diagnostics_and_primary_vacuum():
    observer = object.__new__(RobotExecutionObserver)
    observer.executor = SimpleNamespace(vacuum_entity_id="vacuum.robot")
    entries = (
        SimpleNamespace(entity_id="sensor.robot_status", translation_key="status", unique_id="status"),
        SimpleNamespace(entity_id="sensor.robot_last_clean_end", translation_key="last_clean_end", unique_id="last_clean_end"),
        SimpleNamespace(entity_id="sensor.robot_total_area", translation_key="total_cleaning_area", unique_id="total_cleaning_area"),
    )
    observer._related_registry_entries = lambda: entries
    dependencies = observer.observation_dependencies()
    assert dependencies == (
        "sensor.robot_last_clean_end",
        "sensor.robot_status",
        "vacuum.robot",
    )


def test_scheduler_subscribes_observation_dependencies_and_coalesces_events():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert "self.execution.real.observation_dependencies()" in source
    assert "def _request_zone_reconcile" in source
    assert "while self._started and self._zone_reconcile_requested" in source
