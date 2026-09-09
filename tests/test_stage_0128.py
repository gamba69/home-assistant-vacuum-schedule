"""Regression tests for force conditions are embedded in timing section, editor has no detached force editor after daily corrections, and embedded force editor uses timing card divider not second.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_force_conditions_are_embedded_in_timing_section():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _scheduleTimingHtml() {")
    end = panel.index("\n  _targetEditorHtml() {", start)
    block = panel[start:end]
    assert 'forceEnabled?this._forceEditorHtml():""' in block
    assert 'data-duration-minutes="force_max_advance_minutes"' in block


def test_editor_has_no_detached_force_editor_after_daily_corrections():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _editorHtml() {")
    end = panel.index("\n  async _debugAction", start)
    block = panel[start:end]
    assert block.count('${this._scheduleTimingHtml()}') == 1
    assert '${this._forceEditorHtml()}' not in block
    daily = block.index('this._tr("panel.daily_corrections")')
    timing = block.index('${this._scheduleTimingHtml()}')
    assert timing < daily


def test_embedded_force_editor_uses_timing_card_divider_not_second_card():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.schedule-timing-editor > .force-editor {' in panel
    css = panel[panel.index('.schedule-timing-editor > .force-editor {'):]
    css = css[:css.index('}')]
    assert 'border-top:1px solid var(--vs-border)' in css
    assert 'background:transparent' in css
