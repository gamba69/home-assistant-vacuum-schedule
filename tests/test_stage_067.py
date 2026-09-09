"""Regression tests for add schedule uses icon and plain label, schedule actions are top aligned, and disable schedule requires shared modal confirmation.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_add_schedule_uses_icon_and_plain_label():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._mdi("calendar-plus-outline")' in panel
    assert 'this._tr("panel.add")' in panel
    assert 'this._t("+ Добавить", "+ Add")' not in panel


def test_schedule_actions_are_top_aligned():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.schedule-actions-cell { width:1%; vertical-align:top; }' in panel
    assert '.schedule-actions { display:flex; gap:4px; align-items:flex-start; flex-wrap:nowrap; white-space:nowrap; }' in panel


def test_disable_schedule_requires_shared_modal_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel.split('this.shadowRoot.querySelectorAll("button.schedule-toggle")', 1)[1].split('this.shadowRoot.querySelectorAll("button.job-action")', 1)[0]
    assert 'if(!enabling)' in block
    assert 'this._confirmAction(' in block
    assert 'this._tr("panel.disable_schedule")' in block
    assert 'if(!confirmed)return;' in block


def test_additional_run_is_not_exposed_on_schedule_rows_and_active_run_requires_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'button.schedule-run-now' not in panel
    block = panel.split('this.shadowRoot.querySelectorAll("button.job-action")', 1)[1]
    assert 'if(action==="additional_run")' in block
    assert 'this._confirmAction(this._tr("panel.additional_run")' in block
    assert 'if(!confirmed)return;' in block
