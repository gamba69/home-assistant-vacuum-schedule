"""Regression tests for browser native confirmation apis are not used, global execution disable requires modal confirmation, and destructive flows use shared modal.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_browser_native_confirmation_apis_are_not_used():
    panel = PANEL.read_text(encoding="utf-8")
    assert "window.confirm(" not in panel
    assert "window.prompt(" not in panel
    assert "window.alert(" not in panel
    assert "_confirmAction(" in panel
    assert "_promptAction(" in panel
    assert '<dialog class="vs-dialog"' in panel
    assert ".vs-dialog::backdrop" in panel


def test_global_execution_disable_requires_modal_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._tr("panel.disable_schedule_execution")' in panel
    assert 'if(!confirmed)return;' in panel
    assert 'type:"datetime-local"' in panel
    assert 'this._tr("panel.disable_until")' in panel


def test_destructive_flows_use_shared_modal():
    panel = PANEL.read_text(encoding="utf-8")
    for key in (
        "panel.delete_schedule",
        "panel.additional_run",
        "panel.skip_job",
        "panel.reset_simulation_01dcd64",
        "panel.delete_recipient",
        "panel.detach_channel",
        "panel.clear_notification_log",
        "panel.delete_cleaning_zone",
    ):
        assert key in panel
