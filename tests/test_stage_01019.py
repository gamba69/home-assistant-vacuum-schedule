"""Statistics profile-table attribution repair contracts for 0.10.21."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from statistics_models import aggregate_records  # noqa: E402


def _record(*, zones, seconds=600.0, battery=10.0, floor=30.0, processed=30.0, clean=300.0, dirty=180.0):
    return {
        "job_id": "job-historical",
        "schedule_id": "schedule-1",
        "schedule_name": "Daily clean",
        "schedule_revision": 7,
        "result": "SUCCESS",
        "origin": "SCHEDULED",
        "execution_mode": "REAL",
        "cleaning_params_snapshot": {"cleaning_mode": "vacuum_and_mop", "fan_mode": "balanced", "passes": 1},
        "time": {"physical_execution_seconds": seconds, "wait": {"total_seconds": 0.0, "count": 0, "by_reason_seconds": {}}, "pause_seconds": 0.0},
        "battery": {"consumed_percent": battery},
        "area": {"floor_cleaned_m2": floor, "processed_m2": processed, "physical_cleaned_m2": floor},
        "water_usage": {"available": True, "clean_used_ml_eq": clean, "dirty_gained_ml_eq": dirty},
        "zones": zones,
    }


def test_schedule_profile_exposes_cleaning_seconds_alias_for_shared_table():
    aggregate = aggregate_records([_record(zones=[{
        "zone_id": "z1", "zone_name": "Room", "result": "SUCCESS", "nominal_area_m2": 30.0,
        "attribution": "unavailable",
    }])])
    row = aggregate["by_schedule"][0]
    assert row["execution_seconds"]["median"] == 600.0
    assert row["cleaning_seconds"]["median"] == 600.0


def test_single_historical_zone_inherits_job_totals_as_estimated():
    aggregate = aggregate_records([_record(zones=[{
        "zone_id": "z1", "zone_name": "Room", "result": "SUCCESS", "nominal_area_m2": 30.0,
        "attribution": "unavailable",
    }])])
    row = aggregate["zones"]["rows"][0]
    assert row["estimated"] == 1
    assert row["unavailable"] == 0
    assert row["cleaning_seconds"]["median"] == 600.0
    assert row["battery_consumed_percent"]["median"] == 10.0
    assert row["floor_area_m2"]["median"] == 30.0
    assert row["processed_area_m2"]["median"] == 30.0
    assert row["clean_water_ml_eq_per_m2"]["median"] == 10.0
    assert row["dirty_water_ml_eq_per_m2"]["median"] == 6.0


def test_multi_zone_historical_totals_use_nominal_area_weights():
    aggregate = aggregate_records([_record(zones=[
        {"zone_id": "z1", "zone_name": "Small", "result": "SUCCESS", "nominal_area_m2": 10.0, "attribution": "unavailable"},
        {"zone_id": "z2", "zone_name": "Large", "result": "SUCCESS", "nominal_area_m2": 20.0, "attribution": "unavailable"},
    ])])
    rows = {row["zone_id"]: row for row in aggregate["zones"]["rows"]}
    assert rows["z1"]["cleaning_seconds"]["median"] == 200.0
    assert rows["z2"]["cleaning_seconds"]["median"] == 400.0
    assert round(rows["z1"]["battery_consumed_percent"]["median"], 6) == round(10.0 / 3.0, 6)
    assert round(rows["z2"]["battery_consumed_percent"]["median"], 6) == round(20.0 / 3.0, 6)
    assert rows["z1"]["floor_area_m2"]["median"] == 10.0
    assert rows["z2"]["floor_area_m2"]["median"] == 20.0
    assert rows["z1"]["estimated"] == 1 and rows["z2"]["estimated"] == 1


def test_skipped_zone_does_not_receive_historical_job_resources():
    aggregate = aggregate_records([_record(zones=[
        {"zone_id": "done", "zone_name": "Done", "result": "SUCCESS", "nominal_area_m2": 10.0, "attribution": "unavailable"},
        {"zone_id": "skip", "zone_name": "Skipped", "result": "SKIPPED", "nominal_area_m2": 20.0, "attribution": "unavailable"},
    ])])
    rows = {row["zone_id"]: row for row in aggregate["zones"]["rows"]}
    assert rows["done"]["cleaning_seconds"]["median"] == 600.0
    assert rows["done"]["floor_area_m2"]["median"] == 30.0
    assert rows["skip"]["cleaning_seconds"]["count"] == 0
    assert rows["skip"]["floor_area_m2"]["count"] == 0
    assert rows["skip"]["unavailable"] == 1


def test_multi_zone_without_attribution_basis_stays_unavailable():
    aggregate = aggregate_records([_record(zones=[
        {"zone_id": "z1", "zone_name": "One", "result": "SUCCESS", "attribution": "unavailable"},
        {"zone_id": "z2", "zone_name": "Two", "result": "SUCCESS", "attribution": "unavailable"},
    ])])
    for row in aggregate["zones"]["rows"]:
        assert row["cleaning_seconds"]["count"] == 0
        assert row["battery_consumed_percent"]["count"] == 0
        assert row["floor_area_m2"]["count"] == 0
        assert row["unavailable"] == 1


