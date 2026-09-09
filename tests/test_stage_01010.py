"""Roborock session-area accounting and historical repair contracts for 0.10.10."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from statistics_models import attempt_metrics, repair_record_area  # noqa: E402


def _attempt(values, *, start="2026-08-24T10:00:00+00:00"):
    samples = []
    for index, value in enumerate(values):
        minute = 59 + index
        hour = 9 + minute // 60
        minute %= 60
        samples.append({
            "observed_at": f"2026-08-24T{hour:02d}:{minute:02d}:00+00:00",
            "phase": "CLEANING" if index else "IDLE",
            "cleaning_area_m2": value,
        })
    return {
        "requested_at": start,
        "start_confirmed_at": start,
        "completed_at": "2026-08-24T11:00:00+00:00",
        "metadata": {"statistics_observations": samples},
    }


def test_previous_session_area_before_start_is_not_counted():
    # 35 m² is the previous Roborock run, then the current counter resets and
    # reaches 20 m².  The old max()-based code incorrectly returned 35.
    metrics = attempt_metrics(_attempt([35.0, 0.0, 3.0, 10.0, 20.0]))
    assert metrics["area_m2"] == 20.0
    assert metrics["area_diagnostics"]["boundary_reset"] is True


def test_stale_nonzero_area_that_never_moves_is_unavailable():
    metrics = attempt_metrics(_attempt([35.0, 35.0, 35.0]))
    assert metrics["area_m2"] is None
    assert metrics["area_diagnostics"]["method"] == "flat_nonzero_ambiguous"


def test_post_start_counter_value_is_cumulative_not_delta_from_first_sample():
    # The observer can first see an already-advanced current-session counter.
    # 4 -> 12 -> 20 means 20 m² cleaned, not 16 m².
    attempt = _attempt([99.0, 4.0, 12.0, 20.0])
    metrics = attempt_metrics(attempt)
    assert metrics["area_m2"] == 20.0


def test_additional_counter_reset_inside_owned_session_is_summed():
    # A pre-start baseline already proves the first 0 is this session.  A later
    # reset is an additional counter segment and must not discard earlier work.
    metrics = attempt_metrics(_attempt([30.0, 0.0, 10.0, 0.0, 5.0]))
    assert metrics["area_m2"] == 15.0
    assert metrics["area_diagnostics"]["segment_count"] == 2


def test_area_repair_changes_only_area_derived_fields_and_keeps_identity_data():
    existing = {
        "record_id": "record-1",
        "job_id": "job-1",
        "recorded_at": "2026-08-24T11:01:00+00:00",
        "result": "SUCCESS",
        "battery": {"consumed_percent": 10},
        "area": {"physical_cleaned_m2": 35.0, "source": "robot_observation"},
        "execution_batches": [{"attempt_id": "a1", "metrics": {"area_m2": 35.0, "battery_drop_percent": 10}}],
        "zones": [{"zone_id": "z1", "attributed_area_m2": 35.0, "attributed_battery_percent": 10}],
    }
    rebuilt = {
        "area": {"physical_cleaned_m2": 20.0, "source": "robot_observation"},
        "execution_batches": [{"attempt_id": "a1", "metrics": {"area_m2": 20.0, "area_source": "robot_session_counter", "area_diagnostics": {"method": "session_cumulative_segments"}, "battery_drop_percent": 999}}],
        "zones": [{"zone_id": "z1", "attributed_area_m2": 20.0, "attributed_battery_percent": 999}],
    }
    fixed, changed = repair_record_area(
        existing,
        rebuilt,
        repaired_at=datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
        repair_id="area-v1",
    )
    assert changed is True
    assert fixed["record_id"] == "record-1"
    assert fixed["recorded_at"] == existing["recorded_at"]
    assert fixed["result"] == "SUCCESS"
    assert fixed["battery"] == existing["battery"]
    assert fixed["execution_batches"][0]["metrics"]["area_m2"] == 20.0
    assert fixed["execution_batches"][0]["metrics"]["battery_drop_percent"] == 10
    assert fixed["zones"][0]["attributed_area_m2"] == 20.0
    assert fixed["zones"][0]["attributed_battery_percent"] == 10
    assert fixed["repairs"][-1]["repair_id"] == "area-v1"


def test_repair_is_one_time_and_rebuilds_dependent_water_facts():
    manager = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    migrations = (MODULE / "storage_migrations.py").read_text(encoding="utf-8")
    store = (MODULE / "statistics_store.py").read_text(encoding="utf-8")
    water = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    assert '0.10.18_floor_vs_processed_area_v3' in migrations
    assert "repair_applied(AREA_REPAIR_ID)" in migrations
    assert "async_replace_records(replacements)" in migrations
    assert "async_repair_job_records" in migrations
    assert '"applied_repairs"' in store
    assert 'in {"FLOOR_MOP", "MOP_WASH"}' in water
    assert "self._rebuild_from_ledger()" in water


