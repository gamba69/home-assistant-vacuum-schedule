"""Regression contracts carried forward from Vacuum Schedule 0.8.8 execution-settings persistence."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_execution_form_keeps_restore_checkbox_in_local_draft():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._executionSettingsDraft.restore_previous_settings=!!restoreSettingsInput.checked;' in panel
    assert 'restore_previous_settings:restorePreviousSettings' in panel


def test_additional_execution_form_uses_one_atomic_write():
    panel = PANEL.read_text(encoding="utf-8")
    listener = panel.index('const executionSave=this.shadowRoot.querySelector("button.execution-additional-save")')
    end = panel.index('const faultSave=this.shadowRoot.querySelector("button.execution-fault-save")', listener)
    body = panel[listener:end]
    assert 'vacuum_schedule/settings/update_execution' in body
    assert 'vacuum_schedule/settings/update_dry_run' not in body
    assert 'vacuum_schedule/settings/update_real_execution' not in body
    assert body.count('await this._callWs(') == 1


def test_real_execution_backend_still_persists_both_boolean_values():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'real_settings["restore_previous_settings"] = bool(' in engine
    assert 'options[CONF_REAL_EXECUTION_SETTINGS] = real_settings' in engine
