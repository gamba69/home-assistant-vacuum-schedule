"""Regression coverage for semantic profile grouping in 0.12.50."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from statistics_models import aggregate_records  # noqa: E402


def _record(job_id: str, params: dict, *, result: str = "SUCCESS") -> dict:
    return {
        "job_id": job_id,
        "schedule_id": "schedule-1",
        "schedule_name": "Living room",
        "execution_mode": "REAL",
        "origin": "SCHEDULED",
        "result": result,
        "cleaning_params_snapshot": dict(params),
        "time": {
            "physical_execution_seconds": 600.0 if result == "SUCCESS" else None,
            "wait": {"total_seconds": 0.0, "count": 0, "by_reason_seconds": {}},
            "pause_seconds": 0.0,
            "start_delay_seconds": 0.0,
        },
        "battery": {"consumed_percent": 5.0 if result == "SUCCESS" else None},
        "area": {"floor_cleaned_m2": 10.0 if result == "SUCCESS" else None,
                 "processed_m2": 10.0 if result == "SUCCESS" else None,
                 "physical_cleaned_m2": 10.0 if result == "SUCCESS" else None},
        "water_usage": {"available": True, "clean_used_ml_eq": 0.0, "dirty_gained_ml_eq": 0.0},
        "zones": [{
            "zone_id": "zone-1",
            "zone_name": "Living room",
            "result": result,
            "attribution": "measured" if result == "SUCCESS" else "unavailable",
            "attributed_cleaning_seconds": 600.0 if result == "SUCCESS" else None,
            "attributed_battery_percent": 5.0 if result == "SUCCESS" else None,
            "attributed_floor_area_m2": 10.0 if result == "SUCCESS" else None,
            "attributed_processed_area_m2": 10.0 if result == "SUCCESS" else None,
        }],
    }


def test_vacuum_only_stale_mop_and_water_fields_do_not_split_statistics_rows():
    canonical = {
        "cleaning_mode": "vacuum",
        "cleaning_route": "standard",
        "fan_mode": "turbo",
        "passes": 1,
    }
    legacy = {
        **canonical,
        "mop_mode": "standard",
        "water_mode": "weak",
    }
    aggregate = aggregate_records([
        _record("old", legacy, result="FAILED"),
        _record("new-1", canonical),
        _record("new-2", canonical),
        _record("new-3", canonical),
    ])
    assert len(aggregate["by_schedule"]) == 1
    assert len(aggregate["zones"]["rows"]) == 1
    row = aggregate["by_schedule"][0]
    assert row["total"] == 4
    assert row["success"] == 3
    assert row["profile_params"] == canonical


def test_mop_only_stale_fan_and_route_fields_do_not_split_statistics_rows():
    canonical = {
        "cleaning_mode": "mop",
        "mop_mode": "standard",
        "water_mode": "weak",
        "passes": 1,
    }
    legacy = {
        **canonical,
        "fan_mode": "turbo",
        "cleaning_route": "fast",
    }
    aggregate = aggregate_records([_record("old", legacy), _record("new", canonical)])
    assert len(aggregate["by_schedule"]) == 1
    assert len(aggregate["zones"]["rows"]) == 1
    assert aggregate["by_schedule"][0]["profile_params"] == canonical


def test_combined_cleaning_keeps_all_resource_relevant_profile_fields_distinct():
    base = {
        "cleaning_mode": "vac_and_mop",
        "cleaning_route": "standard",
        "fan_mode": "turbo",
        "mop_mode": "standard",
        "water_mode": "weak",
        "passes": 1,
    }
    different_water = {**base, "water_mode": "strong"}
    aggregate = aggregate_records([_record("a", base), _record("b", different_water)])
    assert len(aggregate["by_schedule"]) == 2
    assert len(aggregate["zones"]["rows"]) == 2


def test_unknown_legacy_mode_is_not_aggressively_merged():
    one = {
        "cleaning_mode": "custom_vendor_mode",
        "fan_mode": "turbo",
        "mop_mode": "standard",
        "water_mode": "weak",
        "passes": 1,
    }
    two = {**one, "water_mode": "strong"}
    aggregate = aggregate_records([_record("a", one), _record("b", two)])
    assert len(aggregate["by_schedule"]) == 2
