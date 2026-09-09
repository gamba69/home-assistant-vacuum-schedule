"""Regression contracts for Vacuum Schedule 0.9.0 settings-page cleanup."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
LOCALE = MODULE / "frontend" / "localization"
FRONTEND = MODULE / "frontend.py"


def _settings_block() -> str:
    panel = PANEL.read_text(encoding="utf-8")
    return panel[panel.index("  _settingsHtml() {"):panel.index("  _overrideInputLabel(key)")]




def test_settings_health_counter_strip_is_removed():
    block = _settings_block()
    assert "settings-health-grid" not in block
    assert "capabilities_attention" not in block
    assert "relatedCount" not in block


def test_compact_robot_card_is_first_and_contains_two_status_rows():
    block = _settings_block()
    robot = block.index('class="entry-card robot-settings-card compact-robot-settings"')
    execution = block.index('class="entry-card execution-settings-card"')
    assert robot < execution
    assert 'class="robot-device-list"' in block
    assert 'panel.physical_execution' in block
    assert 'panel.preliminary_check' in block
    assert 'panel.adapter_ready_summary' in block
    assert 'panel.configuration_correct_summary' in block
    assert "_realReadinessHtml" not in block
    assert "_validationHtml" not in block


def test_primary_device_is_marked_and_filtered_from_related_device_list():
    frontend = FRONTEND.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert '"is_primary": device_id == snapshot.related_devices.primary' in frontend
    assert 'const configuredDevices=(d.related_devices||[]).filter(x=>!x.is_primary);' in panel


def test_compact_status_localization_exists_in_both_languages():
    ru = json.loads((LOCALE / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALE / "en.json").read_text(encoding="utf-8"))
    assert ru["panel.adapter_ready_summary"] == "адаптер {adapter}, готово"
    assert ru["panel.configuration_correct_summary"] == "конфигурация корректна"
    assert en["panel.adapter_ready_summary"] == "adapter {adapter}, ready"
    assert en["panel.configuration_correct_summary"] == "configuration is valid"


def test_additional_execution_save_does_not_touch_mode_buttons_or_full_render():
    panel = PANEL.read_text(encoding="utf-8")
    helper = panel[panel.index("  _setExecutionFormSaving("):panel.index("  _syncExecutionOptionsDom()")]
    assert 'button.execution-mode-choice' in helper  # only optional mode-switch path
    assert 'if(includeModeControls)' in helper
    assert '.execution-advanced-body' in helper
    assert 'toggleAttribute("inert", !!saving)' in helper
    save = panel[panel.index('const executionSave=this.shadowRoot.querySelector("button.execution-additional-save")'):panel.index('const faultSave=', panel.index('const executionSave='))]
    assert 'this._setExecutionFormSaving(true);' in save
    assert 'this._syncExecutionOptionsDom();' in save
    assert 'this._render()' not in save
    assert 'this._saving=true' not in save


def test_capabilities_and_resources_use_responsive_two_column_grid():
    panel = PANEL.read_text(encoding="utf-8")
    block = _settings_block()
    assert '<div class="settings-bindings-grid">' in block
    assert block.count('class="entry-card settings-binding-column"') == 2
    assert '.settings-bindings-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)' in panel
    assert '@media (max-width:1100px) { .zone-summary-table{min-width:1040px}.settings-bindings-grid{grid-template-columns:1fr}' in panel
    assert '.settings-bindings-grid .settings-summary-table{min-width:0!important;max-width:100%;width:100%;table-layout:fixed}' in panel


