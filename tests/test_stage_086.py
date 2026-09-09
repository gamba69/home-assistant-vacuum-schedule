"""Regression contracts for Vacuum Schedule 0.8.12 unified table presentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_native_and_job_tables_share_table_tokens():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        "--vs-table-header-bg:",
        "--vs-table-header-padding:",
        "--vs-table-row-padding:",
        "--vs-table-font-size:",
        "--vs-table-header-font-size:",
    ):
        assert token in panel
    assert "padding:var(--vs-table-header-padding)" in panel
    assert "padding:var(--vs-table-row-padding)" in panel
    assert ".history-job-list-header { padding:var(--vs-table-header-padding);" in panel
    assert ".job-list-header { padding:var(--vs-table-header-padding);" in panel
    assert ".history-job-summary { cursor:pointer; list-style:none; padding:var(--vs-table-row-padding); }" in panel
    assert ".job-summary { cursor:pointer; list-style:none; padding:var(--vs-table-row-padding); }" in panel


def test_recent_jobs_filter_is_compact_combined_popover_in_header():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_historyFilterHtml(entry)" in panel
    assert 'class="entry-header history-entry-header"' in panel
    assert 'class="history-filter-popover"' in panel
    assert 'class="history-filter-trigger"' in panel
    assert 'data-history-filter-group=' in panel
    assert 'option("schedule_id",id,name)' in panel
    assert 'class="history-active-filter"' in panel
    assert 'history-filter-reset' in panel


def test_history_filters_combine_independent_dimensions():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._historyFilters = {execution_mode:"all", result:"all", source:"all", schedule_id:"all"}' in panel
    assert 'this._historyFilterMatches("result",this._historyResultCategory(job))' in panel
    assert 'this._historyFilterMatches("source",this._historySource(job))' in panel
    assert 'this._historyFilterMatches("schedule_id",String(job.schedule_id||""))' in panel
    assert 'this._historyFilterMatches("execution_mode",this._normalizedExecutionMode(job.execution_mode))' in panel


def test_mobile_history_filter_keeps_responsive_layout_without_horizontal_strip():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.history-filter-shell{width:100%;margin-left:0;justify-content:flex-start;align-items:stretch;flex-direction:column}' in panel
    assert '.history-filter-panel{position:static;width:100%;box-sizing:border-box;margin-top:7px}' in panel
