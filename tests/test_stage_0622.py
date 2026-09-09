"""Regression tests for status summary starts with execution gate then execution mode, redundant scheduler active card is removed, and execution mode maps simulation to dry run and is.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_status_summary_starts_with_execution_gate_then_execution_mode():
    panel = PANEL.read_text(encoding="utf-8")
    gate = 'class="summary-card summary-card-gate"><span>${this._tr("panel.execution")}'
    mode = 'class="summary-card summary-card-mode"><span>${this._tr("panel.execution_mode")}'
    active_jobs = 'this._tr("panel.active_jobs")'
    assert gate in panel
    assert mode in panel
    assert panel.index(gate) < panel.index(mode) < panel.index(active_jobs, panel.index(mode))


def test_redundant_scheduler_active_card_is_removed():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._t("Планировщик", "Scheduler")}</span><b>● ${this._t("Активен", "Active")' not in panel
    assert 'this._tr("panel.scheduler_is_not_loaded_yet")' in panel


def test_execution_mode_maps_simulation_to_dry_run_and_is_future_ready():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'rawExecutionMode==="SIMULATION"',
        'executionModeText=dryRunMode?this._tr("panel.execution_dry_run")',
        'this._tr("panel.execution_real")',
        'this._tr("panel.jobs_run_fully_in_simulation_no_physical_commands_are_sent_to_the_vacuum")',
        '.execution-mode-dry-run { color:var(--warning-color',
        '.execution-mode-real { color:var(--success-color',
    ):
        assert token in panel


def test_execution_gate_uses_operational_enabled_disabled_wording():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._tr("panel.enabled_dde9969")' in panel
    assert 'this._tr("panel.disabled_until")' in panel
    assert 'this._tr("panel.disabled")' in panel
