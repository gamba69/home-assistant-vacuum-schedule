"""Regression tests for statistics tabs have no duplicate bold section headings, statistics use reasons heading without a separate tab, and useful secondary statistics captions are preserved.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"


def _statistics_body():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _statisticsHtml() {")
    end = panel.index("\n\n  _maintenanceActionLabel", start)
    return panel[start:end]




def test_statistics_tabs_have_no_duplicate_bold_section_headings():
    body = _statistics_body()
    forbidden = (
        '<h2>${this._tr("panel.time_wait_pause")}</h2>',
        '<h2>${this._tr("panel.zones_and_attribution")}</h2>',
        '<h2>${this._tr("panel.battery_statistics")}</h2>',
        '<h2>${this._tr("panel.failure_reasons")}</h2>',
    )
    for snippet in forbidden:
        assert snippet not in body
    # Water is rendered by a helper, but must follow the same rule.
    panel = PANEL.read_text(encoding="utf-8")
    water_start = panel.index("  _statisticsWaterHtml(")
    water_end = panel.index("\n\n  _tabsHtml()", water_start)
    water = panel[water_start:water_end]
    assert '<h2>${this._tr("panel.water")}</h2>' not in water


def test_statistics_use_reasons_heading_without_a_separate_tab():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    panel = _statistics_body()
    assert ru["panel.reasons"] == "Причины"
    assert en["panel.reasons"] == "Reasons"
    assert '["reasons","alert-circle-outline",this._tr("panel.reasons")]' not in panel
    assert '<h4>${this._tr("panel.reasons")}</h4>' in panel
    assert ru["panel.failure_reasons"] == "Причины невыполнения / частичного выполнения"


def test_useful_secondary_statistics_captions_are_preserved():
    body = _statistics_body()
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._tr("panel.attribution_help")' in body
    assert '<span>${this._tr("panel.failure_reasons_share_help")}</span>' in body
    assert 'statistics-tab-caption">${this._escape(profile.model||profile.label||profile.profile_id||"")}' in panel


def test_time_metrics_are_one_row_on_desktop():
    panel = PANEL.read_text(encoding="utf-8")
    body = _statistics_body()
    assert 'class="summary-grid statistics-summary-grid statistics-time-grid"' in body
    assert '.statistics-time-grid { grid-template-columns:repeat(5,minmax(0,1fr)); }' in panel
    assert '@media (max-width:900px)' in panel
    assert '.statistics-time-grid{grid-template-columns:repeat(2,minmax(0,1fr))}' in panel


