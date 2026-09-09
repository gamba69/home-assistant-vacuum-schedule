"""Acceptance contracts for Vacuum Schedule 0.7.7 execution-mode visual consistency."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_dry_run_spelling_is_canonical_in_current_ui_localizations():
    for lang in ("ru", "en"):
        data=json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text())
        assert data["panel.execution_dry_run"] == "Dry-Run"
        assert data["panel.dry_run_tab"] == "Dry-Run"
        blob=(MODULE / "frontend" / "localization" / f"{lang}.json").read_text()
        assert 'Dry-run' not in blob
        assert 'DRY-RUN' not in blob


def test_banner_and_status_use_localized_canonical_mode_label():
    panel=PANEL.read_text()
    assert 'title:this._tr("panel.execution_dry_run")' in panel
    assert 'executionModeText=dryRunMode?this._tr("panel.execution_dry_run")' in panel
    assert '<b>DRY-RUN</b>' not in panel


def test_mode_icon_and_text_share_semantic_color():
    panel=PANEL.read_text()
    assert 'button.execution-mode-choice-dry { color:var(--warning-color' in panel
    assert 'button.execution-mode-choice-real { color:var(--success-color' in panel
    assert 'button.execution-mode-choice-dry .button-icon,button.execution-mode-choice-real .button-icon { color:currentColor; }' in panel
    assert '.execution-mode-inline-dry-run { color:var(--warning-color' in panel
    assert '.execution-mode-inline-real { color:var(--success-color' in panel
    assert '.system-banner-body b { flex:0 0 auto; letter-spacing:.02em; color:var(--warning-color' in panel


def test_dry_run_navigation_tab_keeps_canonical_spelling():
    data=json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text())
    assert data["panel.dry_run_tab"] == "Dry-Run"


def test_push_unicode_remains_but_mode_spelling_is_canonical():
    formatter=(MODULE / "notification_formatting.py").read_text()
    assert '🧪 Dry-Run' in formatter
    assert '🧪 DRY-RUN' not in formatter
