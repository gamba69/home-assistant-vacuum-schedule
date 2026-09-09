"""Regression contracts for Vacuum Schedule 0.8.12 history filter consistency."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_history_filter_uses_standard_control_typography():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.history-filter-trigger {' in panel
    assert 'button.history-filter-option {' in panel
    assert 'button.history-filter-option.selected' in panel
    assert 'border-radius:var(--ha-border-radius-md,8px)' in panel


def test_history_filter_trigger_and_active_chips_have_icons():
    panel = PANEL.read_text(encoding="utf-8")
    assert '${this._mdi("filter-variant")}<span>${this._tr("panel.filters")}</span>' in panel
    assert '${this._mdi("close")}</button>' in panel
    assert '${this._mdi("filter-remove-outline")}<span>${this._tr("panel.reset_filters")}</span>' in panel


def test_history_active_chips_wrap_and_remain_compact():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.history-active-filters { display:flex; align-items:center; justify-content:flex-end; gap:6px; flex-wrap:wrap;' in panel
    assert '.history-active-filter {' in panel
    assert 'button.history-active-filter-remove {' in panel
    assert 'white-space:nowrap' in panel
    assert 'border-radius:15px' in panel
