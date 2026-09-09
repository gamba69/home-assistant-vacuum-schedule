"""Roborock cleaning-area source disambiguation and v2 repair contracts."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from statistics_models import attempt_metrics  # noqa: E402


def _attempt(values, *, source_entity: str | None = None, start="2026-08-24T10:00:00+00:00"):
    samples = []
    for index, value in enumerate(values):
        minute = 59 + index
        hour = 9 + minute // 60
        minute %= 60
        sample = {
            "observed_at": f"2026-08-24T{hour:02d}:{minute:02d}:00+00:00",
            "phase": "CLEANING" if index else "IDLE",
            "cleaning_area_m2": value,
        }
        if source_entity:
            sample["source_entities"] = {"cleaning_area": source_entity}
        samples.append(sample)
    return {
        "requested_at": start,
        "start_confirmed_at": start,
        "completed_at": "2026-08-24T11:00:00+00:00",
        "metadata": {"statistics_observations": samples},
    }


def test_lifetime_total_cleaning_area_is_delta_not_absolute_area():
    # This is the exact 0.10.10 corruption shape: the lifetime sensor was
    # accidentally discovered as the current-run cleaning_area sensor.
    metrics = attempt_metrics(
        _attempt(
            [3800.0, 3800.0, 3810.0, 3820.0],
            source_entity="sensor.roborock_s8_total_cleaning_area",
        )
    )
    assert metrics["area_m2"] == 20.0
    assert metrics["area_diagnostics"]["method"] == "lifetime_counter_delta"
    assert metrics["area_diagnostics"]["counter_hint"] == "lifetime"


def test_unknown_large_monotonic_counter_is_conservative_delta():
    # Renamed historical source entities may not reveal that this is a lifetime
    # counter.  No-reset unknown streams must still never charge the absolute
    # value wholesale to one Job.
    metrics = attempt_metrics(_attempt([3800.0, 3800.0, 3810.0, 3820.0]))
    assert metrics["area_m2"] == 20.0
    assert metrics["area_diagnostics"]["method"] == "unknown_counter_positive_delta"


def test_explicit_current_cleaning_area_remains_session_cumulative():
    metrics = attempt_metrics(
        _attempt(
            [35.0, 0.0, 3.0, 10.0, 20.0],
            source_entity="sensor.roborock_s8_cleaning_area",
        )
    )
    assert metrics["area_m2"] == 20.0
    assert metrics["area_diagnostics"]["method"] == "session_cumulative_segments"
    assert metrics["area_diagnostics"]["counter_hint"] == "session"


def test_observer_does_not_suffix_match_total_area_or_time_as_current_metrics():
    source = (MODULE / "execution_observer.py").read_text(encoding="utf-8")
    assert 'excluded_unique_suffixes = ("total_cleaning_area", "total_cleaning_time")' in source
    assert "if translation_key in _DIAGNOSTIC_ALIAS_TO_KEY:" in source
    assert "elif not translation_key" in source
    assert "match_quality" in source


def test_v2_repair_runs_independently_of_broken_v1_marker_and_repairs_water_again():
    migrations = (MODULE / "storage_migrations.py").read_text(encoding="utf-8")
    assert '0.10.18_floor_vs_processed_area_v3' in migrations
    assert '0.10.10_area_session_counter_v1' not in migrations
    assert "repair_applied(AREA_REPAIR_ID)" in migrations
    assert "async_replace_records(replacements)" in migrations
    assert "async_repair_job_records" in migrations


