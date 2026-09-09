"""Maintenance confirmation/revision presentation contracts for 0.12.32."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"


def test_pending_confirmation_and_confirmed_edit_are_distinct_in_ui():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    editor = panel[
        panel.index("  _maintenanceEditorHtml()"):
        panel.index("  _maintenanceSessionCardHtml", panel.index("  _maintenanceEditorHtml()"))
    ]

    assert 'const revising=draft.session?.status==="confirmed";' in editor
    assert 'revising?this._tr("panel.edit_maintenance"):this._tr("panel.confirm_maintenance")' in editor
    assert '${revising?`<div class="readiness readiness-wait maintenance-recalc-note">' in editor


def test_startup_removes_false_initial_confirmation_revisions():
    source = (MODULE / "water_statistics.py").read_text(encoding="utf-8")

    assert "self._remove_false_initial_confirmation_revisions()" in source
    assert 'previous_status == "confirmed" and previous_semantics == next_semantics' in source
    assert "A pending -> confirmed transition is the initial interpretation" in source
