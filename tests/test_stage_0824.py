"""Regression contracts for Vacuum Schedule 0.9.0 settings table overflow fix."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_settings_binding_tables_do_not_create_horizontal_scrollbars():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.settings-bindings-grid .table-wrap{width:100%;max-width:100%;min-width:0;overflow-x:hidden}' in panel
    assert '.settings-bindings-grid .settings-summary-table{min-width:0!important;max-width:100%;width:100%;table-layout:fixed}' in panel
    assert '.settings-binding-column{min-width:0;max-width:100%;margin:0;box-sizing:border-box}' in panel

def test_settings_binding_tracks_fit_and_content_wraps():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.settings-bindings-grid .settings-summary-table th:nth-child(4){width:8%}' in panel
    assert '.settings-bindings-grid .settings-summary-table th,.settings-bindings-grid .settings-summary-table td{min-width:0!important;white-space:normal;overflow-wrap:anywhere}' in panel
    assert '.settings-bindings-grid .settings-summary-table td.settings-summary-action{width:8%;min-width:0!important;white-space:normal;text-align:right}' in panel
