"""Statistics summary percentages and inline failure reasons for 0.11.9."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(MODULE))

from statistics_models import aggregate_records  # noqa: E402


def _record(job_id, *, origin="SCHEDULED", result="SUCCESS", reason="execution_success"):
    return {
        "job_id": job_id,
        "schedule_id": "s1",
        "schedule_name": "Schedule",
        "origin": origin,
        "execution_mode": "REAL",
        "result": result,
        "reason_code": reason,
        "time": {"physical_execution_seconds": 60, "wait": {"total_seconds": 0, "count": 0, "by_reason_seconds": {}}, "pause_seconds": 0, "start_delay_seconds": 0},
        "battery": {"consumed_percent": 1},
        "area": {"floor_cleaned_m2": 1, "processed_m2": 1, "physical_cleaned_m2": 1},
        "zones": [],
    }




def test_scheduled_uncompleted_rate_uses_only_scheduled_jobs_as_denominator():
    aggregate = aggregate_records([
        _record("s-ok"),
        _record("s-fail", result="FAILED", reason="blocked"),
        _record("m-ok", origin="MANUAL"),
        _record("m-fail", origin="MANUAL", result="FAILED", reason="manual_failed"),
    ])
    assert aggregate["records"] == 4
    assert aggregate["execution"]["scheduled"] == 2
    assert aggregate["execution"]["scheduled_uncompleted"] == 1
    assert aggregate["execution"]["scheduled_uncompleted_rate"] == 50.0


def test_summary_cards_show_percent_for_total_and_scheduled_uncompleted():
    panel = PANEL.read_text(encoding="utf-8")
    body = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    assert '${fmt(a.records,0)} · ${a.records?"100%":"—"}' in body
    assert 'execution.scheduled_uncompleted_rate' in body
    assert '${fmt(execution.scheduled_uncompleted_rate)}%' in body


def test_reasons_table_is_inline_under_summary_and_reasons_tab_is_removed():
    panel = PANEL.read_text(encoding="utf-8")
    body = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    assert 'class="statistics-tab-section statistics-summary-reasons"' in body
    assert '<h4>${this._tr("panel.reasons")}</h4>' in body
    assert 'const sections={summary:`${summary}${reasons}`,time,jobs:scheduleTable,zones:zoneTable,battery,water,forecast};' in body
    assert '["reasons","alert-circle-outline",this._tr("panel.reasons")]' not in body


def test_stale_saved_reasons_tab_falls_back_to_summary():
    panel = PANEL.read_text(encoding="utf-8")
    body = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    assert 'const requestedTab=this._statisticsTab||"summary";' in body
    assert 'const active=tabs.some(([key])=>key===requestedTab)?requestedTab:"summary";' in body


def test_failure_reason_method_help_is_explicit_in_both_locales():
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    assert "одна итоговая причина" in ru["panel.failure_reasons_share_help"]
    assert "среди всех таких записей" in ru["panel.failure_reasons_share_help"]
    assert "one final reason" in en["panel.failure_reasons_share_help"]
    assert "among all such records" in en["panel.failure_reasons_share_help"]


