"""Presentation cleanup contracts for Vacuum Schedule 0.9.3."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_recent_jobs_rows_use_plain_table_status_text():
    panel = PANEL.read_text(encoding="utf-8")
    history = panel[panel.index("_historyHtml(entry)"):panel.index("_statusHtml()")]
    assert "_historyExecutionModeHtml(job.execution_mode)" in history
    assert "_historyResultHtml(job.result)" in history
    assert "status-badge result-chip" not in history
    assert ".quiet-status-good .quiet-status-dot { background:var(--success-color,var(--vs-success-quiet-on)); }" in panel
    assert ".quiet-status-bad .quiet-status-dot { background:var(--error-color,var(--vs-danger-quiet-on)); }" in panel


def test_recent_jobs_filter_popover_uses_standard_controls():
    panel = PANEL.read_text(encoding="utf-8")
    css = panel[panel.index(".history-filter-trigger {"):panel.index(".history-job-report")]
    assert "border-radius:var(--ha-border-radius-md,8px)" in css
    assert "button.history-filter-option" in css
    assert "border:1px solid var(--vs-control-border)!important" in css


def test_busy_and_inaccessible_zone_states_are_warning_orange():
    panel = PANEL.read_text(encoding="utf-8")
    tone_fn = panel[panel.index("_zoneStatusTone(zone, kind)"):panel.index("_zoneStatusCell(zone, kind)")]
    assert 'if (remaining > 0) return "warning";' in tone_fn
    assert 'item.effective === true ? "warning" : "good"' in tone_fn
    assert 'item.effective === true ? "good" : "warning"' in tone_fn
    assert '.quiet-status-warning .quiet-status-dot { background:var(--warning-color,var(--vs-warning-quiet-on)); }' in panel
