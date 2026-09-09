"""Regression coverage for post-auto-empty completion and lease visibility."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(MODULE))

from status_overlays import apply_execution_lease_overlay  # noqa: E402


def test_execution_lease_overlay_marks_another_runnable_job_waiting():
    payload = {"current_blockers": [], "current_preflight_decision": "PASS"}
    assert apply_execution_lease_overlay(
        payload,
        job_id="next-job",
        lease_owner=("current-job", "attempt-1"),
        runnable_job_ids=("next-job",),
        owner_schedule_name="Кухня",
        owner_attempt_state="COMPLETION_PENDING",
    )
    assert payload["current_blockers"] == ["execution_lease_busy"]
    assert payload["current_preflight_decision"] == "WAIT"
    assert payload["execution_lease"] == {
        "owner_job_id": "current-job",
        "owner_attempt_id": "attempt-1",
        "owner_schedule_name": "Кухня",
        "owner_attempt_state": "COMPLETION_PENDING",
    }


def test_execution_lease_overlay_does_not_block_its_owner_or_hide_a_failure():
    owner = {"current_blockers": [], "current_preflight_decision": "PASS"}
    assert not apply_execution_lease_overlay(
        owner,
        job_id="current-job",
        lease_owner=("current-job", "attempt-1"),
        runnable_job_ids=("current-job",),
    )
    assert owner == {"current_blockers": [], "current_preflight_decision": "PASS"}

    failed = {"current_blockers": ["vacuum_error"], "current_preflight_decision": "FAIL"}
    apply_execution_lease_overlay(
        failed,
        job_id="next-job",
        lease_owner=("current-job", "attempt-1"),
        runnable_job_ids=("next-job",),
    )
    assert failed["current_preflight_decision"] == "FAIL"
    assert failed["current_blockers"] == ["vacuum_error", "execution_lease_busy"]


def test_frontend_prefers_live_lease_over_a_stale_pass_report():
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01226');
        const x=new C();
        x._tr=(key,values)=>({{
          'common.blocker.vacuum_busy':'VACUUM_BUSY',
          'panel.robot_vacuum':'VACUUM_ROW',
          'panel.robot_busy_scheduler_completion':`CHECKING ${{values?.schedule||''}}`,
          'panel.active_readiness_ready':'WRONG_READY',
          'panel.active_readiness_waiting':'WAITING',
          'panel.now_waiting_reason':`WAIT: ${{values?.reason||''}}`,
          'panel.active_readiness_issue_count':`ISSUES ${{values?.count||0}}`,
          'panel.active_condition_ready':'condition ready',
          'panel.active_robot_available':'robot docked',
          'panel.live_activity.completion_check_remaining':`CHECK ${{values?.seconds}}`
        }}[key]||key);
        x._displayNow=()=>new Date('2026-09-03T10:00:00Z');
        const job={{
          job_id:'next-job',state:'PLANNED',advisory_checked_at:null,warning_at:'2026-09-03T11:00:00Z',
          current_blockers:['execution_lease_busy'],current_preflight_decision:'WAIT',
          execution_lease:{{owner_job_id:'current-job',owner_attempt_id:'attempt-1',owner_schedule_name:'Кухня',owner_attempt_state:'COMPLETION_PENDING'}},
          metadata:{{current_preflight_report:{{decision:'PASS',blockers:[],input_snapshot:{{values:{{
            'vacuum.available':{{effective:true}},'vacuum.activity':{{effective:'docked'}}
          }},zones:{{}}}}}}}}
        }};
        const now=x._nowHtml(job);
        const readiness=x._readinessHtml(job);
        const report=x._activeReadinessReportHtml(job);
        for(const html of [now,readiness,report]) if(!html.includes('VACUUM_BUSY')) throw new Error(html);
        if(!report.includes('VACUUM_ROW')||!report.includes('CHECKING Кухня')) throw new Error(report);
        if(report.includes('SCHEDULER_LEASE')) throw new Error(report);
        if(report.includes('WRONG_READY')) throw new Error(report);
        const current={{state:'RUNNING',live:{{attempt_state:'COMPLETION_PENDING',completion_check_due_at:'2026-09-03T10:00:10Z'}}}};
        if(x._liveActivityLabel(current)!=='CHECK 10') throw new Error(x._liveActivityLabel(current));
        """
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_backend_uses_one_settle_calculation_and_exposes_countdown_deadline():
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    observer = (MODULE / "execution_observer.py").read_text(encoding="utf-8")
    assert 'self._settle_duration(attempt)' in backend
    assert '"post_clean_auto_empty_observed_at"' in backend
    assert '"completion_check_due_at"' in engine
    assert 'apply_execution_lease_overlay(' in engine
    assert 'auto_empty_activity=auto_empty_activity' in observer
