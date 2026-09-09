"""Readable active-Job dashboard contracts for Vacuum Schedule 0.10.0."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_active_job_uses_progressive_dashboard_tabs():
    panel = PANEL.read_text(encoding="utf-8")
    for marker in (
        'data-active-job-tab="${key}"',
        '_activeNowReportHtml(job)',
        '_activeZonesReportHtml(job)',
        '_activePlanReportHtml(job)',
        '_activeReadinessReportHtml(job)',
        '_activeParametersReportHtml(job)',
        '_activeExecutionReportHtml(job)',
        '_historyJournalReportHtml(job)',
        '_activeTechnicalReportHtml(job)',
    ):
        assert marker in panel


def test_active_now_is_operational_summary_not_field_dump():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'panel.active_next_action' in panel
    assert 'panel.active_action_required_detail' in panel
    assert 'panel.active_waiting_detail' in panel
    assert 'panel.active_preflight_scheduled_detail' in panel
    assert 'active-primary-metrics' in panel


def test_friendly_readiness_uses_normalized_snapshot_and_zone_checks():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'const snapshot=report.input_snapshot||{};' in panel
    assert 'values["vacuum.battery_percent"]' in panel
    assert 'values["dnd.active"]' in panel
    assert 'Object.entries(zoneSnapshots)' in panel
    assert 'panel.active_check_disabled' in panel
    assert '_preflightSourcesHtml(job,false)' in panel  # retained only for technical drill-down


def test_active_full_trace_is_loaded_lazily_and_open_tab_survives_render():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._activeJobOpen = new Set();' in panel
    assert 'this._activeJobTab = new Map();' in panel
    assert 'this._activeJobDetails = new Map();' in panel
    assert 'async _loadActiveJobDetails(jobId, force=false, render=true)' in panel
    assert 'vacuum_schedule/scheduler/job_details' in panel
    assert 'void this._loadActiveJobDetails(id,false,true)' in panel
    assert 'this._activeJobOpen.has(id)?"open":""' in panel


def test_active_main_zones_hide_opaque_segment_key_and_technical_data_is_drilled_down():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'raw.match(/(?:^|_)(\\d+)$/)' in panel
    assert 'panel.active_zone_readiness' in panel
    assert 'panel.active_zone_not_started' in panel
    technical = panel[panel.index('  _activeTechnicalReportHtml(job) {'):panel.index('  async _loadActiveJobDetails', panel.index('  _activeTechnicalReportHtml(job) {'))]
    assert 'panel.execution_trace' in technical
    assert 'panel.user_actions' in technical
    assert 'panel.notifications' in technical
    assert 'panel.raw_job_data' in technical
