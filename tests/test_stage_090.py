"""Statistics integration contracts for Vacuum Schedule 0.9.0."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from cleaning_zones import CleaningZone  # noqa: E402
from statistics_models import aggregate_records, build_statistics_record  # noqa: E402


def _job(*, shared_target: bool = False, native: bool = True):
    observations = [
        {"observed_at": "2026-08-21T10:05:00+03:00", "phase": "RUNNING", "vendor_status": "segment_cleaning", "battery_percent": 80, "cleaning_area_m2": 0},
        {"observed_at": "2026-08-21T10:20:00+03:00", "phase": "SERVICE", "vendor_status": "washing_the_mop", "battery_percent": 75, "cleaning_area_m2": 15, "wash_mode": "balanced"},
        {"observed_at": "2026-08-21T10:35:00+03:00", "phase": "RUNNING", "vendor_status": "segment_cleaning", "battery_percent": 70, "cleaning_area_m2": 30},
    ]
    metadata = {"statistics_observations": observations} if native else {"start_observation": observations[0], "last_observation": observations[-1]}
    z2_target = "1" if shared_target else "2"
    return {
        "job_id": "job-1",
        "occurrence_id": "occ-1",
        "schedule_id": "schedule-1",
        "schedule_name": "Morning",
        "schedule_revision": 3,
        "origin": "SCHEDULED",
        "execution_mode": "REAL",
        "result": "SUCCESS",
        "reason_code": "execution_success",
        "created_at": "2026-08-21T09:45:00+03:00",
        "planned_start": "2026-08-21T10:00:00+03:00",
        "actual_start": "2026-08-21T10:05:00+03:00",
        "finished_at": "2026-08-21T10:35:00+03:00",
        "cleaning_params": {"mop_mode": "moderate", "fan_mode": "balanced", "passes": 1},
        "targets": ["z1", "z2"],
        "zone_runs": {
            "z1": {"zone_name": "Kitchen", "robot_target_type": "segment", "robot_target_id": "1", "result": "SUCCESS", "actual_start": "2026-08-21T10:05:00+03:00", "finished_at": "2026-08-21T10:35:00+03:00", "metadata": {"nominal_area_m2": 20.0, "nominal_area_source": "manual"}},
            "z2": {"zone_name": "Hall", "robot_target_type": "segment", "robot_target_id": z2_target, "result": "SUCCESS", "actual_start": "2026-08-21T10:05:00+03:00", "finished_at": "2026-08-21T10:35:00+03:00", "metadata": {"nominal_area_m2": 10.0, "nominal_area_source": "manual"}},
        },
        "execution_attempts": {
            "a1": {"attempt_id": "a1", "state": "COMPLETED", "zone_ids": ["z1", "z2"], "target_type": "segment", "targets": ["1", z2_target], "requested_passes": 1, "pass_index": 1, "pass_total": 1, "cleaning_params": {"mop_mode": "moderate", "passes": 1}, "start_confirmed_at": "2026-08-21T10:05:00+03:00", "completed_at": "2026-08-21T10:35:00+03:00", "metadata": metadata}
        },
    }


def _events():
    return [
        {"at": "2026-08-21T09:50:00+03:00", "state": "WAIT", "current_blockers": ["zone_busy"]},
        {"at": "2026-08-21T09:55:00+03:00", "state": "PLANNED", "current_blockers": []},
        {"at": "2026-08-21T10:05:00+03:00", "state": "RUNNING", "current_blockers": []},
        {"at": "2026-08-21T10:35:00+03:00", "state": "FINISHED", "current_blockers": []},
    ]


def test_nominal_area_is_optional_positive_zone_setting():
    zone = CleaningZone.from_dict({"zone_id": "z", "name": "Kitchen", "robot_target_type": "segment", "robot_target_id": "1", "nominal_area_m2": "18.5"})
    assert zone.nominal_area_m2 == 18.5
    assert zone.to_dict()["nominal_area_m2"] == 18.5


def test_combined_batch_keeps_measured_batch_facts_and_estimated_zone_attribution():
    record = build_statistics_record(_job(), _events(), recorded_at=datetime.now(timezone.utc))
    assert record["area"]["physical_cleaned_m2"] == 30.0
    assert record["battery"]["consumed_percent"] == 10.0
    assert record["time"]["physical_execution_seconds"] == 1800.0
    assert record["water_facts"]["wash_count"] == 1
    zones = {row["zone_id"]: row for row in record["zones"]}
    assert zones["z1"]["attribution"] == "estimated"
    assert zones["z2"]["attribution"] == "estimated"
    assert round(zones["z1"]["attributed_area_m2"], 3) == 20.0
    assert round(zones["z2"]["attributed_area_m2"], 3) == 10.0


def test_shared_physical_target_is_represented_once_per_batch():
    record = build_statistics_record(_job(shared_target=True), _events(), recorded_at=datetime.now(timezone.utc))
    targets = record["execution_batches"][0]["physical_targets"]
    assert len(targets) == 1
    assert set(targets[0]["logical_zone_ids"]) == {"z1", "z2"}
    assert record["area"]["physical_cleaned_m2"] == 30.0


def test_wait_duration_and_reason_are_reconstructed_from_semantic_trace():
    record = build_statistics_record(_job(), _events(), recorded_at=datetime.now(timezone.utc))
    assert record["time"]["wait"]["count"] == 1
    assert record["time"]["wait"]["total_seconds"] == 300.0
    assert record["time"]["wait"]["by_reason_seconds"] == {"zone_busy": 300.0}


def test_native_and_historical_source_quality_are_not_conflated():
    native = build_statistics_record(_job(native=True), _events(), recorded_at=datetime.now(timezone.utc))
    historical = build_statistics_record(_job(native=False), _events(), recorded_at=datetime.now(timezone.utc))
    assert native["source_quality"] == "native_0.9"
    assert historical["source_quality"] == "historical_backfill"


def test_aggregates_include_percentiles_and_per_square_metre_metrics():
    record = build_statistics_record(_job(), _events(), recorded_at=datetime.now(timezone.utc))
    aggregate = aggregate_records([record])
    assert aggregate["execution"]["success_rate"] == 100.0
    assert aggregate["time"]["execution_seconds"]["median"] == 1800.0
    assert aggregate["time"]["seconds_per_m2"]["median"] == 60.0
    assert round(aggregate["battery"]["consumed_percent_per_m2"]["median"], 6) == round(10 / 30, 6)


def test_statistics_storage_is_monthly_immutable_ledger_not_recorder():
    source = (MODULE / "statistics_store.py").read_text(encoding="utf-8")
    assert 'f"vacuum_schedule.{self.entry_id}.statistics.{month}"' in source
    assert '"job_month"' in source
    assert "contains_job" in source
    assert "Recorder" not in source


def test_water_calibration_uses_absolute_scale_not_repeated_relative_ratio():
    source = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    assert "observed_scale = capacity / base_usage" in source
    assert "_record_scale_observation" in source
    assert '"cycle_error_percent"' in source
    assert "clean_median_cycle_error_percent" in source
    assert "dirty_median_cycle_error_percent" in source
    assert 'statistics.water.ledger' in source
    assert 'statistics.water.state' in source


def test_statistics_are_not_wired_into_preflight_decisions():
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    assert "StatisticsManager" not in preflight
    assert "statistics.water" not in preflight


def test_release_exposes_statistics_websocket_and_panel_tab():
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/statistics/get"' in frontend
    assert 'f"{DOMAIN}/statistics/water_service"' in frontend  # compatibility API
    assert 'f"{DOMAIN}/maintenance/get"' in frontend
    assert 'f"{DOMAIN}/maintenance/save"' in frontend
    assert '["statistics", "chart-box-outline"' in panel
    assert 'type:"vacuum_schedule/statistics/get"' in panel
    assert 'type:"vacuum_schedule/maintenance/save"' in panel
    stats_water = panel[panel.index("  _statisticsWaterHtml"):panel.index("  _tabsHtml", panel.index("  _statisticsWaterHtml"))]
    assert "water-service" not in stats_water
