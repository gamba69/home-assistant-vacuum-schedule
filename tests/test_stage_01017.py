"""Regression tests for statistics record count is shared across all tabs and statistics card titles are bold.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def _statistics_body():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _statisticsHtml() {")
    end = panel.index("\n\n  _maintenanceActionLabel", start)
    return panel[start:end]




def test_statistics_record_count_is_shared_across_all_tabs():
    body = _statistics_body()
    assert 'const recordsCaption=active==="forecast"?`<div class="muted statistics-global-caption">${this._tr("panel.forecast_filters_independent")}</div>`:`<div class="muted statistics-global-caption">${this._tr("panel.statistics_records_found",{count:d.total_records??0})}</div>`;' in body
    assert '${tabNav}${recordsCaption}<div class="statistics-tab-body">' in body
    assert 'statistics-summary-caption' not in body
    assert body.count('panel.statistics_records_found') == 1


def test_statistics_card_titles_are_bold():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.statistics-metric-card>span { min-width:0; white-space:normal; overflow-wrap:anywhere; font-weight:600; color:var(--primary-text-color); }' in panel
    assert '.statistics-summary-section .summary-card>span:first-child { font-weight:600; color:var(--primary-text-color); }' in panel


