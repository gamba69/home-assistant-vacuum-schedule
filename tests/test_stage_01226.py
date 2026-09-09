"""Regression coverage for one user-facing vacuum-busy concept."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(MODULE))

from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    localized_blocker,
    render_plain,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402


def test_internal_busy_reasons_share_one_localized_user_label_and_notification():
    assert localized_blocker("vacuum_busy", "ru") == "Пылесос занят"
    assert localized_blocker("execution_lease_busy", "ru") == "Пылесос занят"
    assert localized_blocker("vacuum_busy", "en") == "Vacuum busy"
    assert localized_blocker("execution_lease_busy", "en") == "Vacuum busy"

    event = SemanticNotificationEvent(
        event_id="wait:busy-deduplication",
        semantic_type="vacuum.job.wait",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 9, 3, 10, 0),
        schedule_name="Кухня",
        blockers=("vacuum_busy", "execution_lease_busy"),
    )
    rendered = render_plain(event, "ru")
    assert rendered.message.count("Пылесос занят") == 1


def test_panel_deduplicates_busy_reasons_and_uses_one_vacuum_row():
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
          'common.blocker.execution_lease_busy':'WRONG_LEASE_LABEL',
          'panel.now_waiting_reason':`WAIT: ${{values?.reason||''}}`,
          'panel.now_waiting_reasons':`WRONG_COUNT ${{values?.count||0}}`,
          'panel.robot_vacuum':'VACUUM_ROW',
          'common.binding.vacuum.activity':'WRONG_ACTIVITY_ROW',
          'panel.scheduler_execution_lease':'WRONG_LEASE_ROW',
          'panel.active_readiness_waiting':'WAITING',
          'panel.active_readiness_issue_count':`ISSUES ${{values?.count||0}}`,
          'panel.active_condition_ready':'READY',
          'panel.active_robot_available':'AVAILABLE',
          'panel.robot_busy_scheduler_completion':`CHECKING ${{values?.schedule||''}}`,
          'panel.robot_busy_scheduler_running':`RUNNING ${{values?.schedule||''}}`,
          'panel.robot_busy_scheduler_generic':'SCHEDULER_BUSY',
          'panel.robot_busy_external':'EXTERNAL_BUSY',
          'panel.current_readiness_to_start':'READINESS',
          'panel.check':'CHECK',
          'panel.state':'STATE',
          'panel.details':'DETAILS'
        }}[key]||key);
        const job={{
          job_id:'waiting-job',state:'WAIT',advisory_checked_at:'2026-09-03T09:55:00Z',
          current_blockers:['vacuum_busy','execution_lease_busy'],current_preflight_decision:'WAIT',
          execution_lease:{{owner_schedule_name:'Спальня',owner_attempt_state:'COMPLETION_PENDING'}},
          metadata:{{current_preflight_report:{{decision:'WAIT',blockers:[{{code:'vacuum_busy',decision_class:'WAIT'}}],input_snapshot:{{values:{{
            'vacuum.available':{{effective:true}},'vacuum.activity':{{effective:'docked',live:'docked'}}
          }},zones:{{}}}}}}}}
        }};
        const labels=x._blockerLabels(job.current_blockers);
        if(labels.length!==1||labels[0]!=='VACUUM_BUSY') throw new Error(JSON.stringify(labels));
        const durations=x._mergedBlockerDurations({{vacuum_busy:12,execution_lease_busy:18}});
        if(durations.length!==1||durations[0][0]!=='vacuum_busy'||durations[0][1]!==30) throw new Error(JSON.stringify(durations));
        for(const html of [x._nowHtml(job),x._readinessHtml(job),x._currentReadinessDetailsHtml(job)]){{
          if((html.match(/VACUUM_BUSY/g)||[]).length!==1) throw new Error(html);
          if(html.includes('WRONG_LEASE_LABEL')||html.includes('WRONG_COUNT')) throw new Error(html);
        }}
        const report=x._activeReadinessReportHtml(job);
        if(!report.includes('VACUUM_ROW')||!report.includes('VACUUM_BUSY')||!report.includes('CHECKING Спальня')) throw new Error(report);
        if(report.includes('WRONG_ACTIVITY_ROW')||report.includes('WRONG_LEASE_ROW')||report.includes('WRONG_LEASE_LABEL')) throw new Error(report);
        if((report.match(/<tr>/g)||[]).length!==2) throw new Error(report);
        if(!report.includes('ISSUES 1')) throw new Error(report);
        """
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_internal_busy_codes_remain_distinct_in_scheduler_protocol():
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    overlay = (MODULE / "status_overlays.py").read_text(encoding="utf-8")
    assert '"vacuum_busy"' in preflight
    assert '"execution_lease_busy"' in overlay
