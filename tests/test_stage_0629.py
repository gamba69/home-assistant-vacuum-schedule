"""Regression tests for every desktop table has a phone card contract, mobile table contract removes min width and horizontal scrolling, and phone breakpoint stacks dense card fields and actions.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"


def _panel() -> str:
    return PANEL.read_text(encoding="utf-8")




def test_every_desktop_table_has_a_phone_card_contract():
    panel = _panel()
    tags = re.findall(r"<table[^>]*>", panel)
    assert len(tags) >= 10
    specialized = [tag for tag in tags if "notification-history" in tag]
    assert len(specialized) == 1
    for tag in tags:
        if "notification-history" in tag:
            continue
        assert "mobile-card-table" in tag, tag


def test_mobile_table_contract_removes_min_width_and_horizontal_scrolling():
    panel = _panel()
    assert ".table-wrap{overflow:visible}" in panel
    assert ".mobile-card-table{display:block!important;width:100%!important;min-width:0!important" in panel
    assert ".mobile-card-table thead{display:none}" in panel
    assert ".mobile-card-table tbody>tr{display:grid!important;grid-template-columns:1fr" in panel
    assert ".mobile-card-table td{display:grid!important;grid-template-columns:minmax(92px,34%) minmax(0,1fr)" in panel
    assert ".mobile-card-table td::before{content:attr(data-label)" in panel
    assert ".override-table{min-width:980px}" not in panel


def test_phone_breakpoint_stacks_dense_card_fields_and_actions():
    panel = _panel()
    assert "@media (max-width:460px)" in panel
    assert ".mobile-card-table td{grid-template-columns:1fr;gap:3px}" in panel
    assert ".mobile-card-table .mobile-actions-cell{align-items:flex-start;flex-direction:column}" in panel
    assert ".job-summary{grid-template-columns:1fr}" in panel
    assert ".vs-dialog-actions{flex-direction:column}" in panel


def test_mobile_tabs_wrap_instead_of_scrolling_sideways():
    panel = _panel()
    mobile = panel.split("@media (max-width:760px)", 1)[1].split("@media (max-width:460px)", 1)[0]
    assert ".tabs{overflow-x:visible;flex-wrap:wrap" in mobile
    assert ".tabs{overflow-x:auto}" not in mobile


def test_schedule_and_configuration_rows_expose_mobile_labels():
    panel = _panel()
    for token in (
        'data-label="${this._tr("panel.schedule")}"',
        'data-label="${this._tr("panel.next_run")}"',
        'data-label="${this._tr("panel.check")}"',
        'data-label="${this._tr("panel.result_reason")}"',
        'data-label="${this._tr("panel.recipient")}"',
        'data-label="${this._tr("panel.configuration_state")}"',
        'data-label="${this._tr("panel.occupancy")}"',
        'data-label="${this._tr("panel.test_mode")}"',
    ):
        assert token in panel


def test_mobile_non_table_layouts_reflow_and_long_values_wrap():
    panel = _panel()
    mobile = panel.split("@media (max-width:760px)", 1)[1].split("@media (max-width:460px)", 1)[0]
    for token in (
        ".system-banner{align-items:flex-start}",
        ".system-banner-body{align-items:flex-start;flex-direction:column",
        ".entry-header,.editor-header,.channel-head,.subsection-header,.path-header{align-items:stretch;flex-direction:column",
        ".editor-actions{flex-wrap:wrap}",
        ".target-grid,.choice-grid{grid-template-columns:1fr;max-height:none}",
        ".ui-chip{white-space:normal;overflow-wrap:anywhere;word-break:break-word}",
        ".search-select-option-main{flex-wrap:wrap}",
        ".dialog-field{padding:8px 16px 14px}",
    ):
        assert token in mobile
    assert "pre { white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; max-width:100%;" in panel
