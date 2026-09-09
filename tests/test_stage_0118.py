"""Water-resource operational semantics for Vacuum Schedule 0.11.9."""
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
core = sys.modules.setdefault("homeassistant.core", ModuleType("homeassistant.core"))
core.HomeAssistant = getattr(core, "HomeAssistant", object)
core.Event = getattr(core, "Event", object)
core.EventStateChangedData = getattr(core, "EventStateChangedData", object)
core.callback = getattr(core, "callback", lambda fn: fn)
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
storage = sys.modules.setdefault("homeassistant.helpers.storage", ModuleType("homeassistant.helpers.storage"))
if not hasattr(storage, "Store"):
    class Store:
        def __init__(self, *args, **kwargs):
            pass
    storage.Store = Store
entity_component = sys.modules.setdefault(
    "homeassistant.helpers.entity_component", ModuleType("homeassistant.helpers.entity_component")
)
entity_component.DATA_INSTANCES = getattr(entity_component, "DATA_INSTANCES", "entity_components")
entity_registry = sys.modules.setdefault(
    "homeassistant.helpers.entity_registry", ModuleType("homeassistant.helpers.entity_registry")
)
entity_registry.async_get = getattr(entity_registry, "async_get", lambda hass: None)
device_registry = sys.modules.setdefault(
    "homeassistant.helpers.device_registry", ModuleType("homeassistant.helpers.device_registry")
)
device_registry.async_get = getattr(device_registry, "async_get", lambda hass: None)
dispatcher = sys.modules.setdefault(
    "homeassistant.helpers.dispatcher", ModuleType("homeassistant.helpers.dispatcher")
)
dispatcher.async_dispatcher_send = getattr(dispatcher, "async_dispatcher_send", lambda *a, **k: None)
event = sys.modules.setdefault("homeassistant.helpers.event", ModuleType("homeassistant.helpers.event"))
event.async_track_state_change_event = getattr(event, "async_track_state_change_event", lambda *a, **k: (lambda: None))
event.async_track_time_interval = getattr(event, "async_track_time_interval", lambda *a, **k: (lambda: None))

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
from custom_components.vacuum_schedule.statistics_manager import StatisticsManager  # noqa: E402
from custom_components.vacuum_schedule.water_statistics import WaterStatistics  # noqa: E402


class _Adapter:
    def __init__(self, observation):
        self.observation = observation

    def observe(self, now):
        return self.observation


class _CleanWaterProvider:
    def __init__(self, ok: bool):
        self.ok = ok
        self.bindings = {
            "dock.clean_water": SimpleNamespace(binding_mode=SimpleNamespace(value="manual")),
        }

    def snapshot(self, now, *, allow_test_overrides=True):
        item = SimpleNamespace(
            effective_available=True,
            effective_value=self.ok,
            source_entity_id="binary_sensor.clean_water",
            status="ready",
        )
        return SimpleNamespace(values={"dock.clean_water": item})


def _attempt(now: datetime) -> ExecutionAttempt:
    attempt = ExecutionAttempt.create(
        job_id="job",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("zone",),
        target_type="segment",
        targets=("17",),
        cleaning_params={"mop_mode": "standard", "passes": 1},
        now=now,
    )
    attempt.state = ExecutionAttemptState.RUNNING
    attempt.start_confirmed_at = now - timedelta(minutes=10)
    attempt.metadata["cleaning_observed_at"] = (now - timedelta(minutes=9)).isoformat()
    return attempt


def _obs(
    now: datetime,
    *,
    in_cleaning: int = 1,
    vacuum_error: str | None = None,
    dock_error: str | None = None,
) -> RobotExecutionObservation:
    return RobotExecutionObservation(
        observed_at=now,
        normalized_state=NormalizedVacuumState.ERROR,
        phase=RobotExecutionPhase.ERROR,
        vendor="roborock",
        vendor_status="error",
        session_active=False,
        in_cleaning=in_cleaning,
        vacuum_error=vacuum_error,
        dock_error=dock_error,
        raw_vacuum_error=vacuum_error,
        raw_dock_error=dock_error,
        service_activity=False,
    )


def _backend(observation, provider) -> RealExecutionBackend:
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




def test_generic_error_with_known_clean_water_blocker_is_resource_wait_not_runtime_error():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, in_cleaning=1), _CleanWaterProvider(False))
    attempt = _attempt(now)

    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
    assert attempt.failure_reason is None
    assert attempt.error_recovery_deadline_at is None
    assert attempt.metadata["resource_blockers"] == ["clean_water_insufficient"]


def test_specific_non_resource_error_is_not_hidden_by_empty_water_sensor():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    backend = _backend(
        _obs(now, in_cleaning=0, vacuum_error="main_brush_jammed"),
        _CleanWaterProvider(False),
    )
    attempt = _attempt(now)

    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED
    assert attempt.error_recovery_deadline_at == now + timedelta(minutes=30)


def test_post_clean_empty_water_completes_successfully_instead_of_waiting_or_failing():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    backend = _backend(_obs(now, in_cleaning=0), _CleanWaterProvider(False))
    attempt = _attempt(now)

    assert asyncio.run(backend.async_progress(attempt, now))
    assert attempt.state is ExecutionAttemptState.COMPLETED
    assert attempt.failure_reason is None
    assert attempt.metadata["post_clean_resource_blockers"] == ["clean_water_insufficient"]
    assert attempt.metadata["post_clean_service_incomplete"] is True
    assert attempt.metadata["completion_evidence"] == "floor_done_resource_service_unavailable"


def test_pending_clean_maintenance_makes_synthetic_forecast_unknown_fail_open_input():
    manager = object.__new__(StatisticsManager)
    manager.water = SimpleNamespace(
        clean={
            "known": True,
            "estimate_ml_eq": 0.0,
            "capacity_ml": 4000.0,
            "state_confidence": "high",
        },
        dirty={
            "known": True,
            "estimate_ml_eq": 4000.0,
            "capacity_ml": 4000.0,
            "state_confidence": "high",
        },
        pending_service={"clean": {"session_id": "pending-clean"}},
    )

    clean = manager.forecast_resource_state("clean_water")
    dirty = manager.forecast_resource_state("dirty_water")

    assert clean["known"] is False
    assert clean["available_value"] is None
    assert clean["maintenance_pending"] is True
    assert clean["state_confidence"] == "pending_maintenance"
    assert dirty["known"] is True
    assert dirty["maintenance_pending"] is False


def test_water_operational_update_invokes_scheduler_wakeup_hook():
    water = object.__new__(WaterStatistics)
    water.hass = object()
    water.entry_id = "entry"
    calls: list[str] = []
    water._operational_update_callback = lambda: calls.append("reconcile")

    water._fire_attention_updated("water_state_changed", operational=True)
    assert calls == ["reconcile"]


def test_scheduler_registers_water_operational_reconcile_hook():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert "self.statistics.water.set_operational_update_callback(self._request_zone_reconcile)" in source


def _water_for_presence_regression() -> WaterStatistics:
    water = object.__new__(WaterStatistics)
    from custom_components.vacuum_schedule.water_statistics import S8_PRO_ULTRA_PROFILE

    water.profile = S8_PRO_ULTRA_PROFILE
    water.events = []
    water.cycles = []
    water.maintenance_sessions = []
    water.pending_service = {}
    water.processed_job_ids = set()
    water.calibration = water._fresh_calibration()
    water.model_baseline = dict(water.calibration)
    water.model_epoch = None
    water.water_state_baseline = {}
    water.clean = water._new_balance("clean")
    water.dirty = water._new_balance("dirty")
    water._last_resource_ok = {"clean": True, "dirty": None}
    water._presence_absent_since = {}
    return water


def test_removed_clean_tank_does_not_create_empty_threshold_while_absent():
    now = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
    water = _water_for_presence_regression()
    water._presence_absent_since = {"clean": now.isoformat()}
    water._resource_ok = lambda key: False if key == "dock.clean_water" else None

    changed = water._update_resource_thresholds(now)

    assert changed is False
    assert water.events == []
    assert water._last_resource_ok["clean"] is True


def test_noop_after_remove_insert_preserves_previous_clean_water_balance():
    base = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    now = base + timedelta(minutes=10)
    water = _water_for_presence_regression()
    clean = water._new_balance("clean")
    clean.update({
        "known": True,
        "estimate_ml_eq": 2100.0,
        "lower_ml_eq": 2000.0,
        "upper_ml_eq": 2200.0,
        "last_anchor": "approximate_level",
        "anchor_at": base.isoformat(),
        "state_confidence": "low",
    })
    water.water_state_baseline = {
        "at": base.isoformat(),
        "clean": clean,
        "dirty": water._new_balance("dirty"),
    }
    threshold_at = now - timedelta(seconds=2)
    water.events = [{
        "event_id": "removal-threshold",
        "event_type": "SENSOR_THRESHOLD",
        "tank": "clean",
        "at": threshold_at.isoformat(),
        "observation_quality": "live_resource_threshold",
        "details": {"source_key": "dock.clean_water"},
    }]
    water.maintenance_sessions = [{
        "session_id": "remove-insert",
        "source": "detected",
        "detected_at": now.isoformat(),
        "occurred_at": now.isoformat(),
        "status": "confirmed",
        "detection": {"items": [{"tank": "clean", "removed_at": (now-timedelta(seconds=5)).isoformat(), "returned_at": now.isoformat()}]},
        "interpretations": {"clean": {"action": "noop", "value": None}},
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }]

    water._rebuild_from_ledger()
    assert water.clean["estimate_ml_eq"] == 0.0

    assert water._discard_recent_presence_threshold("clean", now) is True
    water._rebuild_from_ledger()

    assert water.clean["known"] is True
    assert water.clean["estimate_ml_eq"] == 2100.0
    assert water.clean["state_confidence"] == "low"
