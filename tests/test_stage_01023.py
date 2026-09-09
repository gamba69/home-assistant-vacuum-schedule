"""Unified duration presentation and Statistics card geometry for 0.11.9."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_one_clock_duration_formatter_supports_mmss_and_hhmmss():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[panel.index("_formatDurationSeconds(value)"):panel.index("_durationBetween(start, end)")]
    assert 'const hours=Math.floor(total/3600);' in block
    assert 'String(hours).padStart(2,"0")' in block
    assert 'String(totalMinutes).padStart(2,"0")' in block
    assert 'String(seconds).padStart(2,"0")' in block
    assert 'panel.seconds_short' not in block
    assert 'panel.minutes_short' not in block


def test_history_runtime_and_notifications_use_common_duration_formatter():
    panel = PANEL.read_text(encoding="utf-8")
    duration_between = panel[panel.index("_durationBetween(start, end)"):panel.index("_lifecycleEventLabel(value)")]
    history = panel[panel.index("_historyDurationValue(value, precise=false)"):panel.index("_historyNumberValue(value")]
    notification = panel[panel.index("_notificationMessageDuration(seconds)"):panel.index("_notificationMessageFactsHtml(message)")]
    assert 'return this._formatDurationSeconds((b-a)/1000);' in duration_between
    assert 'return this._formatDurationSeconds(value);' in history
    assert 'return this._formatDurationSeconds(Math.max(0,Number(seconds||0)));' in notification


def test_statistics_and_time_per_m2_use_the_same_clock_formatter_without_units():
    panel = PANEL.read_text(encoding="utf-8")
    statistics = panel[panel.index("_statisticsHtml() {"):panel.index("_maintenanceActionLabel(")]
    assert 'const duration=(v)=>this._formatDurationSeconds(v);' in statistics
    assert 'const durationPerM2=(v)=>this._formatDurationSeconds(v);' in statistics
    assert 'const tableDuration=(v)=>this._formatDurationSeconds(v);' in statistics
    comparison = panel[panel.index("_historyComparisonHtml(job,context)"):panel.index("_historySummaryReportHtml(job,context)")]
    assert 'add(this._tr("panel.time_per_m2"),secPerM2,aggregate.time?.seconds_per_m2,(v)=>this._formatDurationSeconds(v));' in comparison
    assert 'panel.sec_per_m2' not in panel


def test_read_only_force_window_minimum_window_and_dry_run_offset_are_digital():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'const duration=this._formatHoursMinutes(minutes,"-");' in panel
    assert 'this._formatDurationSeconds(Number(p.minimum_start_window_minutes)*60)' in panel
    assert '<b>${this._formatDurationSeconds(clock.offset_seconds||0)}</b>' in panel
    # Numeric configuration fields still state their input units explicitly.
    assert 'panel.wait_delay_seconds' in panel
    assert 'panel.minimum_remaining_start_window_min' in panel


def test_statistics_summary_cards_have_equal_height_and_top_alignment():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.statistics-summary-grid .summary-card { height:auto; min-height:60px;' in panel
    assert 'align-content:start; align-items:start;' in panel
    assert '.statistics-summary-grid .summary-card>span:first-child,.statistics-summary-grid .summary-card>b{align-self:start}' in panel
    assert '.statistics-summary-grid .statistics-metric-card { height:auto; min-height:60px;' not in panel


