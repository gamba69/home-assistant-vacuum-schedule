"""Regression tests for schedule actions do not turn td into flex box.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_schedule_actions_do_not_turn_td_into_flex_box():
    panel = PANEL.read_text(encoding="utf-8")
    assert '<td class="actions schedule-actions-cell mobile-actions-cell" data-label="${this._tr("panel.actions")}"><div class="schedule-actions">' in panel
    assert '.schedule-actions-cell { width:1%; vertical-align:top; }' in panel
    assert '.schedule-actions { display:flex; gap:4px; align-items:flex-start; flex-wrap:nowrap; white-space:nowrap; }' in panel
    assert '<td class="actions schedule-actions">' not in panel
