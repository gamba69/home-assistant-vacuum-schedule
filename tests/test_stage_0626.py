"""Regression tests for every visual chip family uses one ui chip base, ui chip is the only chip geometry definition, and chip modifiers change semantics not geometry.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_every_visual_chip_family_uses_one_ui_chip_base():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'class="ui-chip status-badge state-chip ',
        'class="ui-chip status-badge support support-',
        'class="ui-chip status-badge search-select-badge"',
        'class="ui-chip parameter-chip"',
        'class="ui-chip target-chip"',
        'class="ui-chip ${css}"',
    ):
        assert token in panel


def test_ui_chip_is_the_only_chip_geometry_definition():
    panel = PANEL.read_text(encoding="utf-8")
    base = '.ui-chip { display:inline-flex; align-items:center; gap:5px; width:max-content; max-width:100%; padding:4px 7px; border-radius:10px; font-size:11px; line-height:1.2; font-weight:700;'
    assert base in panel
    assert '.chip {' not in panel
    assert '.check-pill {' not in panel
    assert '.status-badge { display:' not in panel
    assert '.status-badge { padding:' not in panel
    assert '.status-badge { border-radius:' not in panel


def test_chip_modifiers_change_semantics_not_geometry():
    panel = PANEL.read_text(encoding="utf-8")
    for forbidden in (
        '.parameter-chip { padding:',
        '.target-chip { padding:',
        '.blocker-chip { padding:',
        '.reference-chip { padding:',
        '.selectable-chip { padding:',
        '.state-chip { padding:',
        '.result-chip { padding:',
        '.support { padding:',
        '.search-select-badge { padding:',
        '.parameter-chip { border-radius:',
        '.target-chip { border-radius:',
        '.blocker-chip { border-radius:',
        '.reference-chip { border-radius:',
        '.selectable-chip { border-radius:',
    ):
        assert forbidden not in panel


def test_related_device_list_is_compact_and_hides_technical_ids():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="robot-device-list"' in panel
    assert 'class="robot-device-item"' in panel
    settings = panel[panel.index("  _settingsHtml() {"):panel.index("  _overrideInputLabel(key)")]
    assert 'x.device_id' not in settings
