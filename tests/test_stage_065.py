"""Regression tests for global execution enable requires confirmation everywhere, active job actions are compact row icons, and job row actions do not toggle details.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"




def test_global_execution_enable_requires_confirmation_everywhere():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'enabling?this._tr("panel.enable_schedule_execution")' in panel
    assert 'panel.after_enabling_the_scheduler_may_again_start_ready_jobs_automatically_wi' in panel
    assert 'if(gate!==currentGate)' in panel
    assert 'if(gate!=="enabled" && gate!==currentGate)' not in panel


def test_active_job_actions_are_compact_row_icons():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_jobRowActionsHtml(job)' in panel
    assert 'class="job-actions-header"' in panel
    assert 'class="job-row-actions"' in panel
    assert 'class="${style} icon-only compact-icon-action job-action job-row-action"' in panel
    assert '.job-row-actions { display:flex;' in panel
    assert 'title="${this._escape(label)}" aria-label="${this._escape(label)}"' in panel
    assert '<b>${this._t("Управление заданием", "Job controls")}</b>' not in panel


def test_job_row_actions_do_not_toggle_details():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'button.job-action").forEach((el)=>el.addEventListener("click",async(event)=>{' in panel
    assert 'event.preventDefault();' in panel
    assert 'event.stopPropagation();' in panel


def test_partial_success_uses_visual_result_badge():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.result-PARTIAL_SUCCESS {' in panel
    assert '_historyResultHtml(job.result)' in panel
    assert 'return this._quietStatusHtml(this._resultLabel(result),tone,"history-result")' in panel
    assert '.quiet-status-good .quiet-status-dot' in panel
