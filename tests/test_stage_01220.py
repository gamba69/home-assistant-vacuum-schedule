"""Water-model v3 repair, robust calibration, and forecast sample rebuilding."""
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
core = sys.modules.setdefault("homeassistant.core", ModuleType("homeassistant.core"))
core.HomeAssistant = getattr(core, "HomeAssistant", object)
core.Event = getattr(core, "Event", object)
core.EventStateChangedData = getattr(core, "EventStateChangedData", object)
core.callback = getattr(core, "callback", lambda fn: fn)
sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
for name in (
    "homeassistant.helpers",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.dispatcher",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.entity_component",
):
    sys.modules.setdefault(name, ModuleType(name))
import homeassistant.helpers.device_registry as dr  # noqa: E402
import homeassistant.helpers.entity_registry as er  # noqa: E402
import homeassistant.helpers.dispatcher as dispatcher  # noqa: E402
import homeassistant.helpers.event as event  # noqa: E402
import homeassistant.helpers.storage as storage  # noqa: E402
import homeassistant.helpers.entity_component as entity_component  # noqa: E402
entity_component.DATA_INSTANCES = getattr(entity_component, "DATA_INSTANCES", "entity_components")

dr.async_get = getattr(dr, "async_get", lambda hass: None)
er.async_get = getattr(er, "async_get", lambda hass: None)
dispatcher.async_dispatcher_send = getattr(dispatcher, "async_dispatcher_send", lambda *a, **k: None)
event.async_track_state_change_event = getattr(event, "async_track_state_change_event", lambda *a, **k: (lambda: None))
event.async_track_time_interval = getattr(event, "async_track_time_interval", lambda *a, **k: (lambda: None))
if not hasattr(storage, "Store"):
    class Store:
        def __init__(self, *args, **kwargs):
            pass
    storage.Store = Store

from custom_components.vacuum_schedule.forecast_models import new_active_branch  # noqa: E402
from custom_components.vacuum_schedule.statistics_manager import StatisticsManager  # noqa: E402
from custom_components.vacuum_schedule.storage_migrations import repair_water_calibration_v3  # noqa: E402
from custom_components.vacuum_schedule.water_statistics import (  # noqa: E402
    S8_PRO_ULTRA_PROFILE,
    WaterStatistics,
)


def _water() -> WaterStatistics:
    water = object.__new__(WaterStatistics)
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
    water.model_repair_applied = False
    return water


def _maintenance(session_id: str, at: datetime, tank: str, action: str, value=None, *, source="manual"):
    return {
        "session_id": session_id,
        "source": source,
        "detected_at": at.isoformat() if source == "detected" else None,
        "occurred_at": at.isoformat(),
        "status": "confirmed",
        "detection": {"items": [{"tank": tank}]} if source == "detected" else {"items": []},
        "interpretations": {tank: {"action": action, "value": value}},
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }


def _usage(event_id: str, at: datetime, tank: str, base: float):
    return {
        "event_id": event_id,
        "event_type": "FLOOR_MOP" if tank == "clean" else "MOP_WASH",
        "tank": tank,
        "at": at.isoformat(),
        "job_id": event_id,
        "estimated_ml_eq": base * 4.0,
        "observation_quality": "derived_from_robot_facts",
        "details": {"base_ml_eq": base, "applied_scale": 4.0},
    }


def test_v3_repair_discards_frozen_4x_scale_and_replays_user_level_anchor():
    base = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    water = _water()
    water.model_baseline = {
        **water._fresh_calibration(),
        "algorithm_version": 2,
        "clean_scale": 4.0,
        "clean_cycle_scale_samples": [4.0, 4.0],
    }
    water.calibration = dict(water.model_baseline)
    # An old false short "full -> empty" cycle must no longer teach 4x.
    water.maintenance_sessions = [
        _maintenance("full", base, "clean", "full"),
        _maintenance("real-level", base + timedelta(hours=2), "clean", "level", 70),
    ]
    water.events = [
        _usage("short-old", base + timedelta(minutes=10), "clean", 100.0),
        {
            "event_id": "threshold",
            "event_type": "SENSOR_THRESHOLD",
            "tank": "clean",
            "at": (base + timedelta(minutes=20)).isoformat(),
            "observation_quality": "live_resource_threshold",
            "details": {"source_key": "dock.clean_water"},
        },
        _usage("job-1", base + timedelta(hours=3), "clean", 200.0),
        _usage("job-2", base + timedelta(hours=4), "clean", 200.0),
    ]

    assert repair_water_calibration_v3(water) is True
    water._rebuild_from_ledger()

    assert water.calibration["algorithm_version"] == 3
    assert water.calibration["clean_scale"] == 1.0
    assert water.calibration["clean_rejected_calibration_samples"] >= 1
    assert water.clean["estimate_ml_eq"] == 2050.0  # 70% of 3500 minus 2x200 ml.
    assert next(e for e in water.events if e["event_id"] == "job-1")["estimated_ml_eq"] == 200.0


def test_manual_level_corrections_train_from_real_anchor_delta():
    base = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    water = _water()
    water.maintenance_sessions = [
        _maintenance("level-100", base, "clean", "level", 100),
        _maintenance("level-85", base + timedelta(hours=1), "clean", "level", 85),
    ]
    # Actual drop is 525 ml while bootstrap predicts 500 ml -> scale 1.05.
    water.events = [_usage("job", base + timedelta(minutes=30), "clean", 500.0)]
    water._rebuild_from_ledger()

    assert abs(water.calibration["clean_scale"] - 1.05) < 1e-9
    observations = water.calibration["clean_scale_observations"]
    assert observations[-1]["source"] == "manual_level"
    assert observations[-1]["weight"] == 3
    assert water._model_quality("clean") == "low"
    # The user's latest correction remains the authoritative current state.
    assert water.clean["estimate_ml_eq"] == 2975.0


def test_implausible_short_sensor_cycle_is_rejected_instead_of_clamped():
    base = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    water = _water()
    water.maintenance_sessions = [_maintenance("full", base, "clean", "full")]
    water.events = [
        _usage("tiny", base + timedelta(minutes=5), "clean", 100.0),
        {
            "event_id": "threshold",
            "event_type": "SENSOR_THRESHOLD",
            "tank": "clean",
            "at": (base + timedelta(minutes=10)).isoformat(),
            "details": {"source_key": "dock.clean_water"},
        },
    ]
    water._rebuild_from_ledger()

    assert water.calibration["clean_scale"] == 1.0
    assert water.calibration["clean_full_cycles"] == 0
    assert water.cycles[-1]["accepted_for_calibration"] is False
    assert water.cycles[-1]["quality"] == "rejected_insufficient_usage"


def test_water_forecast_generation_is_rebuilt_from_repaired_job_usage():
    manager = object.__new__(StatisticsManager)
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    manager._forecast_branches = {
        "time": new_active_branch(now=now, generation=7),
        "battery": new_active_branch(now=now, generation=4),
        "clean_water": new_active_branch(now=now, generation=3, samples=[{"job_id": "old", "job_value": 1400.0, "at": now.isoformat()}]),
        "dirty_water": new_active_branch(now=now, generation=5, samples=[{"job_id": "old", "job_value": 1200.0, "at": now.isoformat()}]),
    }
    manager.water = SimpleNamespace(
        job_usage_summaries=lambda ids: {
            "job-1": {"available": True, "clean_used_ml_eq": 350.0, "dirty_gained_ml_eq": 120.0}
        }
    )
    saved = []

    async def _save():
        saved.append(True)

    manager._async_save_models = _save
    record = {
        "job_id": "job-1",
        "schedule_id": "schedule",
        "schedule_name": "Kitchen",
        "execution_mode": "REAL",
        "origin": "SCHEDULED",
        "result": "SUCCESS",
        "finished_at": now.isoformat(),
        "cleaning_params_snapshot": {"mop_mode": "standard", "water_mode": "standard", "passes": 1},
        "zones": [{"zone_id": "kitchen", "zone_name": "Kitchen", "result": "SUCCESS", "nominal_area_m2": 10.0}],
    }

    asyncio.run(manager._async_rebuild_water_forecasts_after_calibration_repair([record]))

    assert manager._forecast_branches["time"]["generation"] == 7
    assert manager._forecast_branches["clean_water"]["generation"] == 4
    assert manager._forecast_branches["dirty_water"]["generation"] == 6
    assert manager._forecast_branches["clean_water"]["samples"][0]["job_value"] == 350.0
    assert manager._forecast_branches["dirty_water"]["samples"][0]["job_value"] == 120.0
    assert saved == [True]


def _save_test_water():
    water = _water()
    calls = []

    async def _save():
        calls.append("save")

    water.async_save = _save
    water._rebuild_from_ledger = lambda: calls.append("rebuild")
    water._fire_attention_updated = lambda *args, **kwargs: calls.append("signal")
    return water, calls


def test_detected_maintenance_initial_confirmation_is_not_a_revision():
    water, calls = _save_test_water()
    detected_at = datetime(2026, 9, 5, 17, 5, tzinfo=timezone.utc).isoformat()
    session = water._detect_maintenance(
        "dirty", None, detected_at, signal="resource_recovered"
    )

    asyncio.run(
        water.async_save_maintenance(
            {"dirty": {"action": "empty"}},
            session_id=session["session_id"],
        )
    )

    assert session["status"] == "confirmed"
    assert session["interpretations"]["dirty"]["action"] == "empty"
    assert session["revisions"] == []
    assert calls == ["rebuild", "save", "signal"]


def test_unchanged_confirmed_maintenance_save_is_a_true_noop():
    water, calls = _save_test_water()
    occurred_at = datetime(2026, 9, 5, 17, 5, tzinfo=timezone.utc)
    session = _maintenance(
        "confirmed", occurred_at, "dirty", "empty", source="detected"
    )
    session["note"] = "Tank emptied"
    session["revisions"] = []
    water.maintenance_sessions = [session]

    asyncio.run(
        water.async_save_maintenance(
            {"dirty": {"action": "empty"}},
            session_id=session["session_id"],
            note=" Tank emptied ",
        )
    )

    assert session["revisions"] == []
    assert calls == []


def test_real_maintenance_edit_creates_one_complete_revision():
    water, calls = _save_test_water()
    occurred_at = datetime(2026, 9, 5, 17, 5, tzinfo=timezone.utc)
    session = _maintenance("confirmed", occurred_at, "dirty", "empty")
    session["note"] = None
    session["revisions"] = []
    water.maintenance_sessions = [session]

    asyncio.run(
        water.async_save_maintenance(
            {"dirty": {"action": "level", "value": 80}},
            session_id=session["session_id"],
            occurred_at=(occurred_at + timedelta(minutes=1)).isoformat(),
            note="Corrected level",
        )
    )

    assert len(session["revisions"]) == 1
    previous = session["revisions"][0]["previous"]
    assert previous["interpretations"]["dirty"]["action"] == "empty"
    assert previous["occurred_at"] == occurred_at.isoformat()
    assert session["interpretations"]["dirty"]["action"] == "level"
    assert session["interpretations"]["dirty"]["value"] == 80.0
    assert session["occurred_at"] == (occurred_at + timedelta(minutes=1)).isoformat()
    assert calls == ["rebuild", "save", "signal"]


def test_old_pending_confirmation_revision_is_removed():
    water = _water()
    session = _maintenance(
        "legacy-false-revision",
        datetime(2026, 9, 5, 17, 5, tzinfo=timezone.utc),
        "dirty",
        "empty",
        source="detected",
    )
    real_revision = {
        "revision_id": "real",
        "changed_at": "2026-09-05T17:07:00+00:00",
        "previous": {
            "status": "confirmed",
            "interpretations": {"dirty": {"action": "level", "value": 90}},
        },
    }
    session["revisions"] = [
        {
            "revision_id": "false-initial-confirmation",
            "changed_at": "2026-09-05T17:06:00+00:00",
            "previous": {"status": "pending", "interpretations": {}},
        },
        real_revision,
    ]
    water.maintenance_sessions = [session]

    assert water._remove_false_initial_confirmation_revisions() == 1
    assert session["revisions"] == [real_revision]
