"""Profile-aware statistics and floor/processed area contracts for 0.10.18."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from statistics_models import aggregate_records, build_statistics_record  # noqa: E402


def _attempt(attempt_id: str, *, pass_index: int, pass_total: int = 2, area: float = 10.0):
    return {
        "attempt_id": attempt_id,
        "state": "COMPLETED",
        "zone_ids": ["z1"],
        "target_type": "segment",
        "targets": ["1"],
        "requested_passes": 2,
        "repetition_mode": "EMULATED",
        "pass_index": pass_index,
        "pass_total": pass_total,
        "cleaning_params": {"passes": 2, "mop_mode": "standard", "fan_mode": "balanced"},
        "start_confirmed_at": f"2026-08-25T10:{(pass_index - 1) * 15:02d}:00+00:00",
        "completed_at": f"2026-08-25T10:{pass_index * 15:02d}:00+00:00",
        "metadata": {
            "statistics_observations": [
                {"observed_at": f"2026-08-25T10:{(pass_index - 1) * 15:02d}:00+00:00", "phase": "RUNNING", "cleaning_area_m2": 0.0, "battery_percent": 80 - (pass_index - 1) * 5},
                {"observed_at": f"2026-08-25T10:{pass_index * 15:02d}:00+00:00", "phase": "RUNNING", "cleaning_area_m2": area, "battery_percent": 75 - (pass_index - 1) * 5},
            ]
        },
    }


def _job(*, attempts: int = 2, result: str = "SUCCESS"):
    return {
        "job_id": "job-pass",
        "occurrence_id": "occ-pass",
        "schedule_id": "schedule-pass",
        "schedule_name": "Kitchen",
        "schedule_revision": 8,
        "origin": "SCHEDULED",
        "execution_mode": "REAL",
        "result": result,
        "reason_code": "execution_success" if result == "SUCCESS" else "execution_failed",
        "created_at": "2026-08-25T09:55:00+00:00",
        "planned_start": "2026-08-25T10:00:00+00:00",
        "actual_start": "2026-08-25T10:00:00+00:00",
        "finished_at": "2026-08-25T10:30:00+00:00" if attempts == 2 else "2026-08-25T10:15:00+00:00",
        "cleaning_params": {"passes": 2, "mop_mode": "standard", "fan_mode": "balanced"},
        "targets": ["z1"],
        "zone_runs": {
            "z1": {
                "zone_name": "Kitchen",
                "robot_target_type": "segment",
                "robot_target_id": "1",
                "result": result,
                "actual_start": "2026-08-25T10:00:00+00:00",
                "finished_at": "2026-08-25T10:30:00+00:00" if attempts == 2 else "2026-08-25T10:15:00+00:00",
                "metadata": {"nominal_area_m2": 10.0, "nominal_area_source": "manual"},
            }
        },
        "execution_attempts": {f"a{i}": _attempt(f"a{i}", pass_index=i) for i in range(1, attempts + 1)},
    }


def _record(
    job_id: str,
    *,
    schedule_revision: int = 1,
    passes: int = 1,
    floor: float = 10.0,
    processed: float | None = None,
    seconds: float = 600.0,
    battery: float = 5.0,
    clean: float = 100.0,
    dirty: float = 60.0,
):
    processed = floor * passes if processed is None else processed
    return {
        "job_id": job_id,
        "schedule_id": "schedule-1",
        "schedule_name": "Living room",
        "schedule_revision": schedule_revision,
        "execution_mode": "REAL",
        "origin": "SCHEDULED",
        "result": "SUCCESS",
        "cleaning_params_snapshot": {
            "cleaning_mode": "vacuum_and_mop",
            "cleaning_route": "standard",
            "fan_mode": "balanced",
            "mop_mode": "standard",
            "water_mode": "standard",
            "passes": passes,
        },
        "time": {
            "physical_execution_seconds": seconds,
            "wait": {"total_seconds": 0.0, "count": 0, "by_reason_seconds": {}},
            "pause_seconds": 0.0,
            "start_delay_seconds": 0.0,
        },
        "battery": {"consumed_percent": battery, "charge_gain_percent": 0.0, "charging_seconds": 0.0},
        "area": {"floor_cleaned_m2": floor, "processed_m2": processed, "physical_cleaned_m2": floor},
        "water_usage": {"available": True, "clean_used_ml_eq": clean, "dirty_gained_ml_eq": dirty},
        "zones": [
            {
                "zone_id": "zone-1",
                "zone_name": "Living room",
                "result": "SUCCESS",
                "attribution": "measured",
                "attributed_cleaning_seconds": seconds,
                "attributed_battery_percent": battery,
                "attributed_floor_area_m2": floor,
                "attributed_processed_area_m2": processed,
                "attributed_area_m2": floor,
            }
        ],
    }


def test_emulated_two_passes_separate_floor_and_processed_area():
    record = build_statistics_record(_job(attempts=2), [], recorded_at=datetime.now(timezone.utc))
    assert record["schema_version"] == 2
    assert record["area"]["floor_cleaned_m2"] == 10.0
    assert record["area"]["physical_cleaned_m2"] == 10.0
    assert record["area"]["processed_m2"] == 20.0
    zone = record["zones"][0]
    assert zone["attributed_floor_area_m2"] == 10.0
    assert zone["attributed_processed_area_m2"] == 20.0


def test_partial_emulated_sequence_does_not_divide_cleaned_floor_by_requested_passes():
    record = build_statistics_record(_job(attempts=1, result="FAILED"), [], recorded_at=datetime.now(timezone.utc))
    assert record["area"]["floor_cleaned_m2"] == 10.0
    assert record["area"]["processed_m2"] == 10.0
    assert record["zones"][0]["attributed_floor_area_m2"] == 10.0


def test_global_per_m2_metrics_are_weighted_by_floor_area():
    rows = [
        _record("a", floor=10, seconds=600, battery=5, clean=100, dirty=60),
        _record("b", floor=20, seconds=1800, battery=10, clean=300, dirty=180),
    ]
    aggregate = aggregate_records(rows)
    assert aggregate["area"]["floor_cleaned_m2"] == 30.0
    assert aggregate["area"]["processed_m2"] == 30.0
    assert aggregate["time"]["seconds_per_m2_weighted"]["value"] == 80.0
    assert aggregate["battery"]["consumed_percent_per_m2_weighted"]["value"] == 0.5
    assert round(aggregate["water_usage"]["clean_ml_eq_per_m2_weighted"]["value"], 6) == round(400 / 30, 6)
    assert round(aggregate["water_usage"]["dirty_ml_eq_per_m2_weighted"]["value"], 6) == 8.0


def test_schedule_and_zone_rows_are_profile_aware_but_revision_is_not_a_profile():
    rows = [
        _record("a", schedule_revision=1, passes=1),
        _record("b", schedule_revision=2, passes=1),
        _record("c", schedule_revision=3, passes=2, seconds=1200, battery=9, clean=180, dirty=100),
    ]
    aggregate = aggregate_records(rows)
    assert len(aggregate["by_schedule"]) == 2
    assert sorted(row["total"] for row in aggregate["by_schedule"]) == [1, 2]
    assert len(aggregate["zones"]["rows"]) == 2
    profiles = {int(row["profile_params"]["passes"]): row for row in aggregate["by_schedule"]}
    assert profiles[1]["clean_water_ml_eq_per_m2"]["count"] == 2
    assert profiles[2]["dirty_water_ml_eq_per_m2"]["count"] == 1
    assert profiles[2]["processed_area_m2"]["median"] == 20.0
    assert profiles[2]["floor_area_m2"]["median"] == 10.0


def test_frontend_has_jobs_before_zones_and_one_profile_table_with_water_columns():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert '["jobs","clipboard-text-clock-outline",this._tr("panel.history_tab_jobs")],["zones","floor-plan"' in panel
    assert 'const sections={summary:`${summary}${reasons}`,time,jobs:scheduleTable,zones:zoneTable,battery,water,forecast};' in panel
    assert 'const profileTable=(rows,{kind,withAttribution=false,help=""})=>' in panel
    assert 'panel.clean_water_ml_per_m2_header' in panel
    assert 'panel.dirty_water_ml_per_m2_header' in panel
    assert 'panel.floor_area_m2_header' in panel


def test_water_tab_uses_weighted_global_per_m2_cards_and_floor_water_uses_processed_area():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    water = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    assert 'aggregate.water_usage?.clean_ml_eq_per_m2_weighted?.value' in panel
    assert 'aggregate.water_usage?.dirty_ml_eq_per_m2_weighted?.value' in panel
    assert '_number(area_data.get("processed_m2"))' in water
    assert 'effective_area * self.profile.floor_ml_per_effective_m2' in water


def test_area_v3_repair_is_isolated_and_schema_remains_current():
    migrations = (MODULE / "storage_migrations.py").read_text(encoding="utf-8")
    manager = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    assert '0.10.18_floor_vs_processed_area_v3' in migrations
    assert 'async_repair_statistics_area_history' in manager
    assert 'async_repair_job_records' in migrations
    assert 'STATISTICS_SCHEMA_VERSION = 2' in (MODULE / "statistics_models.py").read_text(encoding="utf-8")
