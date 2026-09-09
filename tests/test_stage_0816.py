"""Regression contracts for Vacuum Schedule 0.9.0 status mode-control cleanup."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_current_mode_value_has_no_duplicate_mode_icon():
    panel = PANEL.read_text(encoding="utf-8")
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy(preset) {")]
    card = status[status.index('class="summary-card summary-card-mode"'):]
    card = card[:card.index('</div>\n        <div class="summary-card"><span>${this._tr("panel.active_jobs")}</span>')]
    value = card[card.index('class="summary-mode-value'):card.index('class="mini-actions summary-actions summary-mode-actions"')]
    assert '${this._mdi(dryRunMode?"flask-outline":"robot-vacuum")}' not in value
    assert '<b>${this._escape(executionModeText)}</b>' in value


def test_quick_mode_buttons_use_same_neutral_framed_visual_language_as_gate_buttons():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.summary-card-gate button.ghost.summary-action,' in panel
    assert '.summary-card-mode button.quick-execution-mode { color:var(--vs-neutral-on)!important; background:var(--vs-neutral-fill)!important; border-color:var(--vs-control-border)!important; box-shadow:none; }' in panel
    assert 'button.quick-execution-mode-dry { color:var(--warning-color' not in panel
    assert 'button.quick-execution-mode-real { color:var(--success-color' not in panel
    assert 'button.quick-execution-mode.active { box-shadow:inset 0 0 0 1px currentColor' not in panel


def test_active_quick_mode_is_only_neutrally_emphasized():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.summary-card-mode button.quick-execution-mode.active { color:var(--primary-text-color)!important; background:var(--vs-neutral-fill-active)!important; border-color:var(--secondary-text-color)!important; }' in panel


