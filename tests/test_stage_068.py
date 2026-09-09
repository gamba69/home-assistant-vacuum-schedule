"""Regression tests for notification primary action labels are single word and notification action icons are preserved.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_notification_primary_action_labels_are_single_word():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._tr("panel.save")' in panel
    assert 'this._tr("panel.add")' in panel
    assert 'this._tr("panel.clear")' in panel
    assert 'this._t("Сохранить настройки уведомлений","Save notification settings")' not in panel
    assert 'this._t("Добавить получателя","Add recipient")' not in panel
    assert 'this._t("Очистить журнал","Clear log")' not in panel


def test_notification_action_icons_are_preserved():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._mdi("content-save-outline")' in panel
    assert 'this._mdi("account-plus-outline")' in panel
    assert 'this._mdi("delete-sweep-outline")' in panel
