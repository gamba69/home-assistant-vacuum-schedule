"""Regression contracts for Vacuum Schedule 0.9.0 unified system banners."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_all_top_notices_use_one_orange_system_banner_renderer():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_systemBannerRowHtml(' in panel
    assert '.system-banner {' in panel
    assert 'background:var(--vs-warning-quiet-fill)' in panel
    assert 'border:1px solid var(--wa-color-warning-border-normal,var(--warning-color))' in panel
    assert 'background:var(--vs-danger-quiet-fill)' not in panel[panel.index('.system-notice-stack'):panel.index('.execution-mode-inline')]
    assert 'runtime-blocker-global-banner' not in panel


def test_notice_priority_puts_critical_and_action_required_attention_first():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index('  _globalNoticeItems()')
    end = panel.index('  _systemBannerRowHtml', start)
    block = panel[start:end]
    assert 'priority:critical?0:8' in block
    assert 'panel.station_service_required' in block
    assert 'priority:5' in block
    assert 'priority:10' in block
    assert 'priority:20' in block
    assert 'priority:30' in block
    assert 'priority:40' in block
    assert 'return notices.sort((a,b)=>a.priority-b.priority);' in block


def test_multiple_notices_are_collapsed_with_more_affordance():
    panel = PANEL.read_text(encoding="utf-8")
    assert '<details class="system-notice-stack"' in panel
    assert '<summary class="system-banner system-banner-summary">' in panel
    assert 'panel.more_notices' in panel
    assert 'chevron-down' in panel
    assert 'chevron-up' in panel
    assert 'this._systemNoticesOpen=noticeStack.open' in panel


def test_context_warnings_moved_into_global_notice_stack():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'title:this._tr("panel.dry_run_tab"),lines:[this._tr("panel.dry_run_tools_disabled_in_real")]' in panel
    assert 'title:this._tr("panel.test_overrides")' in panel
    assert '${!dryRun?`<div class="persistent-warning"' not in panel
    assert '${persistent?`<div class="persistent-warning"' not in panel


def test_more_notice_and_override_labels_are_localized():
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    assert ru["panel.more_notices"] == "Ещё {count}"
    assert en["panel.more_notices"] == "{count} more"
    assert ru["panel.test_overrides"] == "Тестовые подмены"
    assert en["panel.test_overrides"] == "Test overrides"
