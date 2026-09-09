"""Acceptance contracts for Vacuum Schedule 0.7.9 mode indicator coloring."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_top_navigation_dry_run_tab_is_not_semantically_colored():
    panel=PANEL.read_text()
    assert 'key==="testing"?"dry-run-tab"' not in panel
    assert 'button.tab.dry-run-tab' not in panel
    assert '["testing", "flask-outline", this._tr("panel.dry_run_tab")]' in panel


def test_status_summary_dry_run_icon_and_text_are_amber():
    panel=PANEL.read_text()
    assert '.summary-card .summary-mode-value.execution-mode-dry-run { color:var(--warning-color' in panel
    assert '.summary-card .summary-mode-value .button-icon,.summary-card .summary-mode-value b { color:currentColor; }' in panel


def test_status_summary_real_text_is_green_without_duplicate_icon():
    panel=PANEL.read_text()
    assert '.summary-card .summary-mode-value.execution-mode-real { color:var(--success-color' in panel
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy(preset) {")]
    assert 'dryRunMode?"flask-outline":"robot-vacuum"' not in status


def test_settings_picker_remains_semantically_colored():
    panel=PANEL.read_text()
    assert 'button.execution-mode-choice-dry { color:var(--warning-color' in panel
    assert 'button.execution-mode-choice-real { color:var(--success-color' in panel
