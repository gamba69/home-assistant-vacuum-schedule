"""Regression contracts for Vacuum Schedule 0.8.12 mode switching and settings hierarchy."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
ENGINE = MODULE / "scheduler_engine.py"
FRONTEND = MODULE / "frontend.py"




def test_mode_change_has_preview_and_immediate_confirmed_write():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'vacuum_schedule/settings/preview_execution_mode' in panel
    assert 'vacuum_schedule/settings/update_execution_mode' in panel
    assert 'panel.execution_mode_regeneration_warning' in panel
    assert 'panel.execution_mode_may_start_immediately' in panel
    assert 'button.execution-additional-save' in panel
    assert 'button.execution-settings-save' not in panel


def test_mode_regeneration_is_narrowly_safety_gated():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index('def _job_safe_for_mode_regeneration'):engine.index('def execution_mode_change_preview')]
    assert 'job.execution_attempts' in section
    assert 'JobState.PLANNED, JobState.WAIT' in section
    assert 'ZoneJobState.PLANNED, ZoneJobState.WAIT' in section
    assert 'not zone.terminal' in section


def test_regeneration_preserves_identity_but_resets_execution_state_and_occupancy_override():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index('def _regenerated_job_for_mode'):engine.index('async def _apply_execution_mode_change')]
    assert 'raw = job.to_dict()' in section
    assert '"manual_overrides"' in section
    assert 'metadata.pop(key, None)' in section
    assert '"execution_attempts": {}' in section
    assert '"state": JobState.PLANNED.value' in section
    assert '"execution_mode": selected.value' in section
    assert 'raw["job_id"]' not in section  # job_id from original serialized snapshot is preserved


def test_regeneration_does_not_archive_or_count_failure():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index('async def _apply_execution_mode_change'):engine.index('async def async_set_execution_mode')]
    assert '"statistics_eligible": False' in section
    assert 'self.store.remove_active(job.job_id)' in section
    assert 'self.store.archive(job)' not in section
    assert 'job.finish(' not in section
    assert 'self._finish(' not in section


def test_consumed_target_occurrence_is_not_replayed():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index('async def _apply_execution_mode_change'):engine.index('async def async_set_execution_mode')]
    assert 'self.store.by_occurrence_id(' in section
    assert 'dropped_consumed_jobs' in section
    assert 'normal materialization will' in section


def test_unrelated_settings_refresh_cannot_change_existing_job_backend():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index('async def async_settings_changed'):engine.index('async def async_real_execution_readiness')]
    assert 'job.execution_mode = self.execution_mode' not in section
    assert 'Mode changes have their own explicit regeneration transaction' in section


def test_settings_page_has_new_information_hierarchy():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'panel.page_scheduler_settings' in panel
    assert 'panel.additional_execution_settings' in panel
    assert 'class="settings-health-grid"' not in panel
    assert 'class="overflow-menu"' in panel
    settings = panel[panel.index('  _settingsHtml() {'):panel.index('  _overrideInputLabel(key) {')]
    assert 'registry ${' not in settings
    assert 'class="robot-compact-status"' in settings
    assert '_realReadinessHtml' not in settings


def test_0810_localization_complete():
    required = {
        'panel.vacuum_schedule_settings', 'panel.execution_mode_action_help',
        'panel.additional_execution_settings', 'panel.related_devices',
        'panel.configuration', 'panel.ready_state', 'panel.unavailable_state',
        'panel.no_errors', 'panel.warnings_count', 'panel.errors_count',
        'panel.enable_dry_run', 'panel.switch_to_real_execution_warning',
        'panel.switch_to_dry_run_warning', 'panel.execution_mode_regeneration_warning',
        'panel.execution_mode_may_start_immediately', 'panel.execution_mode_changed',
    }
    for lang in ('en', 'ru'):
        data=json.loads((MODULE/'frontend'/'localization'/f'{lang}.json').read_text(encoding='utf-8'))
        assert required <= set(data)


def test_preview_websocket_registered():
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/settings/preview_execution_mode"' in frontend
    assert 'websocket_api.async_register_command(hass, websocket_settings_preview_execution_mode)' in frontend


