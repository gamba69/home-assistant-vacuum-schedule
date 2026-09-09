"""Regression contracts for Vacuum Schedule 0.9.0 resource-table visual cleanup."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_settings_binding_states_use_quiet_status_dots():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._quietStatusHtml(this._supportLabel(row.support_status),this._bindingSupportTone(row.support_status),"binding-support-state")' in panel
    assert 'class="ui-chip status-badge support support-${this._escape(row.support_status)}"' not in panel
    assert 'return this._quietStatusHtml(value,tone,"resource-state")' in panel
    assert '.binding-support-state,.resource-state{font-weight:500;color:var(--primary-text-color)}' in panel
    assert '.quiet-status-good .quiet-status-dot' in panel
    assert '.quiet-status-bad .quiet-status-dot' in panel
    assert '.quiet-status-warning .quiet-status-dot' in panel

def test_resource_rows_have_no_left_severity_stripe():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.settings-summary-row.resource-runtime-bad{box-shadow:' not in panel
    assert '.settings-summary-row.resource-runtime-warning{box-shadow:' not in panel
