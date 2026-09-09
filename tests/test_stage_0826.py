"""Regression contracts for Vacuum Schedule 0.9.0 global runtime banners."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_runtime_attention_is_global_banner_not_every_runtime_wait():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_globalBannersHtml()" in panel
    assert '_globalNoticeItems()' in panel
    assert 'system-banner' in panel
    assert '_runtimeAttentionBlockers(runtimeStatus)' in panel
    notices = panel[panel.index("  _globalNoticeItems()"):panel.index("  _systemBannerRowHtml", panel.index("  _globalNoticeItems()"))]
    assert 'runtimeStatus.can_start_now===false' not in notices
    assert 'panel.current_start_blocked' not in notices
    # The compact robot card keeps only structural readiness and config validity.
    settings_start = panel.index("  _settingsHtml()")
    settings_end = panel.index("  _overrideInputLabel", settings_start)
    settings = panel[settings_start:settings_end]
    assert 'const runtimeSummary=' not in settings
    assert 'const runtimeLabel=' not in settings
    assert 'robot-status-row ${runtimeTone}' not in settings


def test_global_banners_are_rendered_on_editors_and_all_tabs():
    panel = PANEL.read_text(encoding="utf-8")
    assert '<div class="content-shell">${this._globalBannersHtml()}${content}</div>' in panel
    assert 'editingPage?"":this._globalBannersHtml()' not in panel
    # Runtime status is refreshed on scheduler push even outside Settings/Testing.
    assert 'await this._loadSettings(false);' in panel[panel.index("async _subscribePush"):panel.index("async _loadScheduler")]


def test_dry_run_banner_is_fully_localized_ru_and_aligned_en():
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    ru_text = ru["panel.dry_run_banner_text"]
    assert "pre-flight" not in ru_text.lower()
    assert "WAIT" not in ru_text
    assert "предварительная проверка" in ru_text
    assert "ожидание" in ru_text
    assert "режиме симуляции" in ru_text
    assert "pre-flight checks" in en["panel.dry_run_banner_text"]
    assert "simulation mode" in en["panel.dry_run_banner_text"]


def test_runtime_banner_uses_shared_warning_visual_semantics():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.system-banner {' in panel
    assert 'background:var(--vs-warning-quiet-fill)' in panel
    assert 'border:1px solid var(--wa-color-warning-border-normal,var(--warning-color))' in panel
    assert 'runtime-blocker-global-banner' not in panel
