"""Regression tests for settings binding table actions are icon only, cleaning zone table actions are icon only, and cleaning zone add action is compact and uses area.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_settings_binding_table_actions_are_icon_only():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="ghost icon-only compact-icon-action settings-edit-binding"' in panel
    assert 'title="${this._tr("panel.edit_0fd5c3c")}"' in panel
    assert 'aria-label="${this._tr("panel.edit_0fd5c3c")}"' in panel
    assert 'settings-edit-binding" data-key="${this._escape(row.key)}">${this._mdi("pencil-outline")}<span>' not in panel


def test_cleaning_zone_table_actions_are_icon_only():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="ghost icon-only compact-icon-action room-edit"' in panel
    assert 'class="danger icon-only compact-icon-action room-delete"' in panel
    assert 'title="${this._tr("panel.configure")}"' in panel
    assert 'title="${this._tr("panel.delete")}"' in panel
    assert '<div class="room-actions-inner">' in panel
    assert '.zone-summary-table td.room-actions { display:table-cell;' in panel


def test_cleaning_zone_add_action_is_compact_and_uses_area_icon():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="primary room-new"' in panel
    assert 'this._mdi("vector-square-plus")' in panel
    assert '<span>${this._tr("panel.add")}</span>' in panel
    assert '<span>${this._t("Добавить зону","Add zone")}</span>' not in panel
