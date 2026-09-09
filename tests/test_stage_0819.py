"""Regression contracts for Vacuum Schedule 0.9.0 Dry-Run override lockout."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
LOCALE = MODULE / "frontend" / "localization"




def test_real_mode_warning_is_fully_localized_in_russian():
    ru = json.loads((LOCALE / "ru.json").read_text(encoding="utf-8"))
    text = ru["panel.dry_run_tools_disabled_in_real"]
    assert "overrides" not in text.lower()
    assert "подмен" in text.lower()


def test_override_table_receives_dry_run_editability():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_overrideRowsHtml(editable = true)" in panel
    assert "${this._overrideRowsHtml(dryRun)}" in panel


def test_all_override_edit_controls_use_common_disabled_state():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[panel.index("  _overrideRowsHtml(editable = true)"):panel.index("  _testingHtml()")]
    assert 'const disabled=editable ? "" : " disabled";' in block
    assert '<select data-override-field="mode"${disabled}>' in block
    assert 'placeholder="${this._tr("panel.force_only")}"${disabled}>' in block
    assert 'type="checkbox" ${ov?.persistent?"checked":""}${disabled}>' in block
    assert 'class="primary icon-only compact-icon-action override-apply"' in block
    assert 'class="danger small icon-only compact-icon-action override-clear"' in block and '${disabled}>' in block


def test_backend_still_rejects_override_mutation_in_real_mode():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    set_start = engine.index("    async def async_set_override(")
    clear_start = engine.index("    async def async_clear_overrides(")
    preset_start = engine.index("    async def async_apply_override_preset(")
    assert 'if self.execution_mode is not ExecutionMode.DRY_RUN:' in engine[set_start:clear_start]
    assert 'raise ValueError("dry_run_required")' in engine[set_start:clear_start]
    assert 'if self.execution_mode is not ExecutionMode.DRY_RUN:' in engine[clear_start:preset_start]


