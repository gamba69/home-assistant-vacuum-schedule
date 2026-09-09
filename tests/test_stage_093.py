"""Readable completed-Job report contracts for Vacuum Schedule 0.9.3."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"

_spec = importlib.util.spec_from_file_location("vacuum_schedule_statistics_models_093", MODULE / "statistics_models.py")
_stats = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_stats)
build_job_comparison = _stats.build_job_comparison


def _record(job_id: str, *, at: str, duration: float = 1800, battery: float = 10, zones=("kitchen",), passes=1):
    return {
        "job_id": job_id,
        "planned_start": at,
        "finished_at": at,
        "schedule_id": "schedule-1",
        "execution_mode": "REAL",
        "result": "SUCCESS",
        "cleaning_params_snapshot": {"passes": passes, "mop_mode": "moderate"},
        "zones": [{"zone_id": zone, "zone_name": zone, "result": "SUCCESS", "attribution": "measured"} for zone in zones],
        "time": {"physical_execution_seconds": duration, "wait": {"total_seconds": 0, "count": 0, "by_reason_seconds": {}}, "pause_seconds": 0, "start_delay_seconds": 0},
        "battery": {"consumed_percent": battery, "charge_gain_percent": 0, "charging_seconds": 0},
        "area": {"physical_cleaned_m2": 20},
    }


def test_job_comparison_prefers_exact_previous_zone_parameter_matches():
    rows = [
        _record("current", at="2026-08-21T19:00:00+03:00", duration=2100),
        _record("a", at="2026-08-20T19:00:00+03:00", duration=1800),
        _record("b", at="2026-08-19T19:00:00+03:00", duration=1900),
        _record("c", at="2026-08-18T19:00:00+03:00", duration=2000),
        _record("other", at="2026-08-17T19:00:00+03:00", zones=("hall",)),
    ]
    comparison = build_job_comparison(rows[0], rows, minimum_samples=3)
    assert comparison["available"] is True
    assert comparison["basis"] == "zones_and_parameters"
    assert comparison["sample_count"] == 3
    assert comparison["aggregate"]["time"]["execution_seconds"]["median"] == 1900


def test_job_comparison_refuses_pseudostatistics_below_three_samples():
    rows = [
        _record("current", at="2026-08-21T19:00:00+03:00"),
        _record("a", at="2026-08-20T19:00:00+03:00"),
        _record("b", at="2026-08-19T19:00:00+03:00"),
    ]
    comparison = build_job_comparison(rows[0], rows, minimum_samples=3)
    assert comparison["available"] is False
    assert comparison["sample_count"] == 2
    assert comparison["minimum_samples"] == 3


def test_water_layer_exposes_per_job_usage_summary():
    text = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    assert "def job_usage_summary" in text
    assert '"clean_used_ml_eq"' in text
    assert '"dirty_gained_ml_eq"' in text


def test_job_details_endpoint_attaches_statistics_context():
    text = FRONTEND.read_text(encoding="utf-8")
    assert '"statistics_context"] = await scheduler.statistics.async_job_context' in text


def test_completed_job_report_uses_progressive_disclosure_tabs():
    panel = PANEL.read_text(encoding="utf-8")
    for marker in (
        'data-history-tab="${key}"',
        '_historySummaryReportHtml(job,context)',
        '_historyZoneReportHtml(job,context)',
        '_historyTimeReportHtml(job,context)',
        '_historyResourcesReportHtml(job,context)',
        '_historyExecutionReportHtml(job,context)',
        '_historyJournalReportHtml(job)',
        '_historyTechnicalReportHtml(job)',
    ):
        assert marker in panel
    assert '_formatPercentileLabel(dist.percentile_number??90)' in panel
    assert 'panel.not_enough_comparable_runs' in panel


def test_lifecycle_noise_is_collapsed_in_human_journal():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'blockerKey!==previousBlockers' in panel
    assert 'previousState!=="WAIT"' in panel
    assert 'panel.journal_wait_changed' in panel
    assert 'panel.journal_conditions_met' in panel


def test_statistics_page_uses_matching_progressive_tabs():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'data-statistics-tab="${key}"' in panel
    assert 'const sections={summary:`${summary}${reasons}`,time,jobs:scheduleTable,zones:zoneTable,battery,water,forecast};' in panel
    assert 'this._statisticsTab=button.dataset.statisticsTab||"summary"' in panel
    assert 'const areaLabel=this._formatPercentileLabel(configuredPercentile("time"));' in panel
