"""Regression coverage for 0.12.46 water-scope and model repair."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from cleaning_scope import (  # noqa: E402
    canonicalize_effective_cleaning_params,
    cleaning_scope,
)
from forecast_models import (  # noqa: E402
    ForecastPolicyConfig,
    compact_forecast_sample,
    estimate_from_samples,
)
from schedule import ScheduleDefinition  # noqa: E402


def _sample(value: float, *, mode: str = "vac_and_mop", water: str = "weak", job: str = "j"):
    return {
        "sample_id": job,
        "job_id": job,
        "schedule_id": "s",
        "schedule_name": "Room",
        "at": "2026-09-07T10:00:00+03:00",
        "result": "SUCCESS",
        "params": {"cleaning_mode": mode, "mop_mode": "standard", "water_mode": water, "passes": 1},
        "job_value": value,
        "zone_ids": ["room"],
        "zones": [{"zone_id": "room", "zone_name": "Room", "value": value, "result": "SUCCESS"}],
    }


def test_explicit_vacuum_mode_wins_over_inherited_mop_and_water_fields():
    params = {"cleaning_mode": "vacuum", "mop_mode": "standard", "water_mode": "weak", "passes": 1}
    scope = cleaning_scope(params)
    assert scope.kind == "dry"
    normalized = canonicalize_effective_cleaning_params(params)
    assert normalized["mop_mode"] == ""
    assert normalized["water_mode"] == ""


def test_weekday_vacuum_override_clears_wet_base_fields_only_in_occurrence_snapshot():
    schedule = ScheduleDefinition.create(
        name="Mixed",
        enabled=True,
        dates=[],
        weekdays=[1],
        local_time="10:00",
        target_type="cleaning_zones",
        targets=["room"],
        cleaning_params={
            "cleaning_mode": "vac_and_mop",
            "mop_mode": "standard",
            "water_mode": "weak",
            "passes": 1,
        },
        weekday_overrides={1: {"cleaning_mode": "vacuum"}},
    )
    effective = schedule.effective_cleaning_params_for_date(date(2026, 9, 8))  # Tuesday
    assert effective["cleaning_mode"] == "vacuum"
    assert effective["mop_mode"] == ""
    assert effective["water_mode"] == ""
    assert schedule.cleaning_params["mop_mode"] == "standard"


def test_water_forecast_for_explicit_dry_job_is_deterministic_zero():
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=25, lookback_days=90)
    samples = [_sample(300, job="a"), _sample(350, job="b"), _sample(400, job="c")]
    estimate = estimate_from_samples(
        samples,
        zone_ids=["room"],
        cleaning_params={"cleaning_mode": "vacuum", "mop_mode": "standard", "water_mode": "weak", "passes": 1},
        policy=policy,
        metric="clean_water",
    )
    assert estimate["available"] is True
    assert estimate["basis"] == "dry_cleaning"
    assert estimate["forecast_value"] == 0.0
    assert estimate["raw_percentile_value"] == 0.0


def test_water_forecast_does_not_borrow_a_different_wet_profile_by_zone_only():
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=0, lookback_days=90)
    samples = [_sample(300, water="weak", job="a"), _sample(320, water="weak", job="b"), _sample(340, water="weak", job="c")]
    estimate = estimate_from_samples(
        samples,
        zone_ids=["room"],
        cleaning_params={"cleaning_mode": "vac_and_mop", "mop_mode": "standard", "water_mode": "strong", "passes": 1},
        policy=policy,
        metric="clean_water",
    )
    assert estimate["available"] is False
    assert estimate["basis"] == "insufficient_matching_water_profile"


def test_dry_and_unknown_jobs_do_not_train_water_forecast_samples():
    base = {
        "job_id": "job",
        "schedule_id": "s",
        "schedule_name": "Room",
        "execution_mode": "REAL",
        "origin": "SCHEDULED",
        "result": "SUCCESS",
        "finished_at": "2026-09-07T10:00:00+03:00",
        "zones": [{"zone_id": "room", "zone_name": "Room", "result": "SUCCESS", "nominal_area_m2": 10}],
    }
    dry = {**base, "cleaning_params_snapshot": {"cleaning_mode": "vacuum", "mop_mode": "standard", "water_mode": "weak"}}
    unknown = {**base, "job_id": "unknown", "cleaning_params_snapshot": {"cleaning_mode": "default"}}
    assert compact_forecast_sample(dry, "clean_water", water_usage={"available": True, "clean_used_ml_eq": 200}) is None
    assert compact_forecast_sample(unknown, "clean_water", water_usage={"available": True, "clean_used_ml_eq": 200}) is None


def _install_ha_stubs():
    sys.path.insert(0, str(ROOT))
    pkg = ModuleType("custom_components.vacuum_schedule")
    pkg.__path__ = [str(MODULE)]
    sys.modules.setdefault("custom_components.vacuum_schedule", pkg)
    core = sys.modules.setdefault("homeassistant.core", ModuleType("homeassistant.core"))
    core.HomeAssistant = getattr(core, "HomeAssistant", object)
    core.callback = getattr(core, "callback", lambda fn: fn)
    sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
    for name in (
        "homeassistant.helpers", "homeassistant.helpers.device_registry", "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.dispatcher", "homeassistant.helpers.event", "homeassistant.helpers.storage",
    ):
        sys.modules.setdefault(name, ModuleType(name))
    import homeassistant.helpers.device_registry as dr
    import homeassistant.helpers.entity_registry as er
    import homeassistant.helpers.dispatcher as dispatcher
    import homeassistant.helpers.event as event
    import homeassistant.helpers.storage as storage
    dr.async_get = getattr(dr, "async_get", lambda hass: None)
    er.async_get = getattr(er, "async_get", lambda hass: None)
    dispatcher.async_dispatcher_send = getattr(dispatcher, "async_dispatcher_send", lambda *a, **k: None)
    event.async_track_state_change_event = getattr(event, "async_track_state_change_event", lambda *a, **k: (lambda: None))
    if not hasattr(storage, "Store"):
        class Store:
            def __init__(self, *args, **kwargs):
                pass
        storage.Store = Store


def _water_instance():
    _install_ha_stubs()
    from custom_components.vacuum_schedule.water_statistics import S8_PRO_ULTRA_PROFILE, WaterStatistics
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
    return water


def _water_record(job_id: str, params: dict, *, area=20.0, washes=None):
    return {
        "job_id": job_id,
        "execution_mode": "REAL",
        "origin": "SCHEDULED",
        "finished_at": "2026-09-07T10:00:00+03:00",
        "cleaning_params_snapshot": params,
        "area": {"processed_m2": area, "physical_cleaned_m2": area},
        "water_facts": {"wash_events": list(washes or [])},
    }


def test_synthetic_meter_does_not_create_floor_water_for_explicit_dry_job():
    water = _water_instance()
    record = _water_record("dry", {"cleaning_mode": "vacuum", "mop_mode": "standard", "water_mode": "weak", "passes": 1})
    assert water._record_job_usage(record) is True
    assert not [e for e in water.events if e["event_type"] == "FLOOR_MOP"]
    assert water.job_usage_summary("dry")["clean_used_ml_eq"] == 0.0


def test_synthetic_meter_records_floor_water_for_wet_job_and_uncertainty_for_unknown_scope():
    water = _water_instance()
    wet = _water_record("wet", {"cleaning_mode": "vac_and_mop", "mop_mode": "standard", "water_mode": "weak", "passes": 1})
    unknown = _water_record("unknown", {"cleaning_mode": "default", "passes": 1})
    water._record_job_usage(wet)
    water._record_job_usage(unknown)
    floor = [e for e in water.events if e["event_type"] == "FLOOR_MOP" and e["job_id"] == "wet"]
    assert len(floor) == 1
    assert floor[0]["details"]["base_ml_eq"] == 130.0
    uncertainty = [e for e in water.events if e["event_type"] == "WATER_UNCERTAINTY" and e["job_id"] == "unknown"]
    assert uncertainty and uncertainty[0]["details"]["reason"] == "cleaning_scope_unresolved"


def test_clean_component_model_splits_floor_and_wash_only_after_diverse_cycles():
    water = _water_instance()
    # Synthetic full cycles generated by true floor scale 0.8 and wash scale 1.2.
    rows = [
        (2500.0, 1250.0),
        (2000.0, 1583.3333333333),
        (3000.0, 916.6666666667),
        (1500.0, 1916.6666666667),
    ]
    water.calibration["clean_component_cycles"] = [
        {"floor_base_ml_eq": floor, "wash_base_ml_eq": wash, "capacity_ml_eq": 3500.0}
        for floor, wash in rows
    ]
    assert water._update_clean_component_model() is True
    assert water.calibration["clean_component_model_trained"] is True
    assert abs(water.calibration["clean_floor_scale"] - 0.8) < 0.02
    assert abs(water.calibration["clean_wash_scale"] - 1.2) < 0.02


def test_preflight_and_execution_use_shared_scope_contract_in_source():
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    adapter = (MODULE / "execution_adapter.py").read_text(encoding="utf-8")
    manager = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    assert "if not scope.explicitly_dry:" in preflight
    assert "if not cleaning_scope(job.cleaning_params).uses_water:" in preflight
    assert "canonicalize_effective_cleaning_params(attempt.cleaning_params)" in adapter
    assert '_WATER_SCOPE_REPAIR_ID = "0.12.46_water_cleaning_scope"' in manager
    assert 'discard_archives=True' in manager
