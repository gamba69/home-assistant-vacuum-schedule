"""Status dashboard layout and power-state presentation contracts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_power_monitor_uses_five_column_desktop_layout_without_changing_disabled_layout():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="summary-grid${policy.power_monitor_enabled?" summary-grid-power":""}"' in panel
    assert '.summary-grid-power { grid-template-columns:minmax(0,.9fr) minmax(0,1.25fr) minmax(0,.85fr) minmax(0,1.14fr) minmax(0,.97fr); }' in panel
    assert '@media (min-width:901px) and (max-width:1650px) { .summary-grid-power { grid-template-columns:repeat(3,minmax(0,1fr)); } }' in panel


def test_power_status_color_reflects_effective_reading():
    panel = PANEL.read_text(encoding="utf-8")
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy(preset) {")]
    assert 'powerAvailable===true?"present":powerAvailable===false?"absent":"unknown"' in status
    assert 'class="power-state-${powerState}"' in status
    assert '.summary-card-power .power-state-present { color:var(--success-color' in panel
    assert '.summary-card-power .power-state-absent { color:var(--error-color' in panel
    assert '.summary-card-power .power-state-unknown { color:var(--secondary-text-color)' in panel


def test_compact_execution_mode_title_is_localized():
    panel = PANEL.read_text(encoding="utf-8")
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy(preset) {")]
    assert 'realExecutionMode?this._tr("panel.execution_real_short")' in status
    assert '${this._tr("panel.execution_mode")}:' in status
    assert 'class="mini-actions summary-actions summary-mode-actions"' in status
    for lang, expected in (("ru", "Реальное"), ("uk", "Реальне"), ("en", "Real")):
        catalog = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert catalog["panel.execution_real_short"] == expected
