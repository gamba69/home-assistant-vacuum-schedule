"""Initial global-notice hydration contracts for Vacuum Schedule 0.10.0."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_global_notice_stack_is_absent_until_first_complete_backend_hydration():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._globalNoticesReadyEntryId = null;' in panel
    assert 'this._settingsDataEntryId = null;' in panel
    assert '_markGlobalNoticesReady(entryId = this._schedulerEntryId)' in panel
    assert 'if (!this._data || !this._schedulerData || !this._settingsData) return;' in panel
    assert 'if (this._settingsDataEntryId !== selected) return;' in panel
    assert 'if(!this._schedulerEntryId || this._globalNoticesReadyEntryId!==this._schedulerEntryId) return "";' in panel


def test_settings_hydration_marks_only_the_matching_config_entry_ready():
    panel = PANEL.read_text(encoding="utf-8")
    settings = panel[panel.index('async _loadSettings(render = true)'):panel.index('async _loadNotifications(render = true)')]
    assert 'this._settingsDataEntryId = entryId;' in settings
    assert 'this._markGlobalNoticesReady(entryId);' in settings
    assert 'this._settingsData = null; this._settingsDataEntryId = null;' in settings


def test_background_refresh_does_not_clear_notice_readiness():
    panel = PANEL.read_text(encoding="utf-8")
    # Readiness is monotonic for the currently hydrated entry; loading state must
    # not reset it, otherwise every refresh would make a valid banner flicker.
    load = panel[panel.index('async _load(force = false)'):panel.index('_syncLoadingIndicator()')]
    assert '_globalNoticesReadyEntryId = null' not in load
