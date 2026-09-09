"""Regression tests for all state changing active job actions require modal confirmation, start now modal explains preflight semantics, and recheck remains immediate and does not require confirmation.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_all_state_changing_active_job_actions_require_modal_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'if(action==="start_now"||action==="start_now_ignore_busy"||action==="skip"||action==="cancel")' in panel
    assert 'this._tr("panel.execute_early")' in panel
    assert 'this._tr("panel.skip_job")' in panel
    assert 'this._tr("panel.cancel_execution")' in panel
    assert 'if(!confirmed)return;' in panel


def test_start_now_modal_explains_preflight_semantics():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'panel.execute_early_help' in panel
    assert 'isStart?this._tr("panel.execute_early")' in panel


def test_recheck_remains_immediate_and_does_not_require_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'action==="start_now"||action==="start_now_ignore_busy"||action==="skip"||action==="cancel"' in panel
    assert 'action==="recheck"' not in panel.split('if(action==="start_now"||action==="start_now_ignore_busy"||action==="skip"||action==="cancel")', 1)[1].split('try{await this._callWs', 1)[0]
