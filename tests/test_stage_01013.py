"""Regression tests for statistics time labels and per m2 formatter are human, zone statistics use separate median and p90 columns, and charge and water statistics use compact non clipping layout.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"




def test_statistics_time_labels_and_per_m2_formatter_are_human_readable():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.physical_execution_time_short"] == "Время уборки"
    assert ru["panel.time_wait_pause"] == "Время, ожидание и пауза"
    assert ru["panel.wait_time"] == "Ожидание"
    assert ru["panel.pause"] == "Пауза"
    assert ru["panel.wait_cycles"] == "Циклов ожидания"
    assert 'const durationPerM2=' in panel
    assert 'const durationPerM2=(v)=>this._formatDurationSeconds(v);' in panel
    stats = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    assert '<span>WAIT</span>' not in stats
    assert '<span>PAUSE</span>' not in stats
    assert 'this._tr("panel.wait_time")' in stats
    assert 'this._tr("panel.pause")' in stats


def test_zone_statistics_use_separate_median_and_configured_percentile_columns():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="mobile-card-table statistics-profile-table"' in panel
    assert '<th colspan="2" class="metric-group-head">${this._tr("panel.time")}</th>' in panel
    assert '<th colspan="2" class="metric-group-head">${this._tr("panel.battery_percent_header")}</th>' in panel
    assert '<th colspan="2" class="metric-group-head">${this._tr("panel.floor_area_m2_header")}</th>' in panel
    assert 'class="statistics-zone-subhead"' in panel
    assert 'this._tr("panel.median")' in panel
    assert 'const areaLabel=this._formatPercentileLabel(configuredPercentile("time"));' in panel
    assert 'tableTime(x.cleaning_seconds,"median")' in panel
    assert 'tableTime(x.cleaning_seconds,"percentile")' in panel
    assert 'tableMetric(x.battery_consumed_percent,"median")' in panel
    assert 'tableMetric(x.floor_area_m2||x.area_m2,"percentile")' in panel
    assert 'configuredPercentile("time")' in panel
    assert 'configuredPercentile("battery")' in panel


def test_charge_and_water_statistics_use_compact_non_clipping_layout():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ru["panel.charge_rate_short"] == "Скорость зарядки"
    assert ru["panel.dirty_water_filled_short"] == "Грязная вода"
    assert en["panel.charge_rate_short"] == "Charge rate"
    assert 'statistics-battery-grid' in panel
    assert 'statistics-water-grid' in panel
    assert 'this._tr("panel.clean_water_remaining_short")' in panel
    assert 'this._tr("panel.dirty_water_filled_short")' in panel
    assert '.statistics-summary-grid .summary-card { height:auto; min-height:60px;' in panel


def test_failure_reasons_include_percentage_share():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.share"] == "Доля"
    assert 'const reasonTotal=(a.reasons||[]).reduce' in panel
    assert '<th class="metric-header">${this._tr("panel.share")}</th>' in panel
    assert '/reasonTotal*100' in panel
    assert 'panel.failure_reasons_share_help' in panel


