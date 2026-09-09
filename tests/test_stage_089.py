"""Regression contracts carried forward from Vacuum Schedule 0.8.9 stable advanced execution form."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
ENGINE = MODULE / "scheduler_engine.py"
FRONTEND = MODULE / "frontend.py"




def test_execution_form_has_independent_local_state():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._executionSettingsDraft = null;' in panel
    assert 'this._executionSettingsDirty = false;' in panel
    assert '_syncExecutionSettingsDraft(force = false)' in panel
    assert 'const executionDraft=this._executionSettingsDraft || {' in panel


def test_background_refresh_does_not_overwrite_dirty_execution_form():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'if (!force && (this._executionSettingsDirty || this._executionOptionsSaving || this._saving)) return;' in panel


def test_advanced_controls_update_local_draft_before_save():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._executionSettingsDraft.dry_run_duration=dryRunDurationInput.value;' in panel
    assert 'this._executionSettingsDraft.restore_previous_settings=!!restoreSettingsInput.checked;' in panel


def test_advanced_form_saves_one_atomic_snapshot_without_mode_confirmation_logic():
    panel = PANEL.read_text(encoding="utf-8")
    listener = panel.index('const executionSave=this.shadowRoot.querySelector("button.execution-additional-save")')
    end = panel.index('const faultSave=this.shadowRoot.querySelector("button.execution-fault-save")', listener)
    body = panel[listener:end]
    assert 'const snapshot={mode,dry_run:{execution_duration_seconds:dryRunDuration},real:{restore_previous_settings:restorePreviousSettings,runtime_error_recovery_minutes:runtimeErrorRecoveryMinutes}};' in body
    assert body.count('await this._callWs(') == 1
    assert 'type:"vacuum_schedule/settings/update_execution"' in body
    assert '_confirmAction' not in body


def test_atomic_backend_updates_config_entry_once():
    engine = ENGINE.read_text(encoding="utf-8")
    start = engine.index('async def async_update_execution_settings(')
    end = engine.index('    def _schedule_by_id', start)
    body = engine[start:end]
    assert body.count('self.hass.config_entries.async_update_entry(self.entry, options=options)') == 1
    assert 'options[CONF_EXECUTION_MODE] = selected.value' in body
    assert 'options[CONF_DRY_RUN_SETTINGS] = dry_settings' in body
    assert 'options[CONF_REAL_EXECUTION_SETTINGS] = real_settings' in body


def test_atomic_websocket_command_is_registered():
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/settings/update_execution"' in frontend
    assert 'websocket_api.async_register_command(hass, websocket_settings_update_execution)' in frontend
