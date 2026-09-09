"""Regression coverage for the compact recent-Job history columns."""

from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_recent_jobs_show_effective_profile_full_job_time_and_compact_today_time():
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01230');
        const x=new C();
        x._hass={{language:'ru'}};
        x._displayNow=()=>new Date('2026-09-04T12:00:00Z');
        x._timeZone=()=>'UTC';
        x._tr=(key)=>({{
          'panel.completed':'Завершено','panel.schedule':'Расписание',
          'panel.cleaning_profile_used':'Режим уборки',
          'panel.duration':'Длительность',
          'panel.execution_mode':'Режим выполнения','panel.source':'Источник',
          'panel.result':'Результат','panel.reason':'Причина',
          'panel.with_wait':'С ожиданием',
          'common.relative.today':'сегодня'
        }}[key]||key);
        x._compactCleaningProfileText=(params)=>String(params?.label||'');
        x._historyFilterMatches=()=>true;
        x._historySource=()=> 'SCHEDULED';
        x._historySourceLabel=()=> 'По расписанию';
        x._historyExecutionModeHtml=()=> 'Реальное выполнение';
        x._historyResultHtml=()=> 'Успешно';
        x._reasonLabel=(reason)=>reason==='success'?'Успешно выполнено':'Ошибка выполнения';
        const job={{
          job_id:'job-1',schedule_id:'schedule-1',schedule_name:'Кухня',
          execution_mode:'REAL',result:'SUCCESS',reason_code:'success',
          waited_before_start:true,
          planned_start:'2026-09-04T10:00:00Z',
          actual_start:'2026-09-04T10:01:00Z',
          finished_at:'2026-09-04T11:02:00Z',
          cleaning_params:{{label:'snapshot'}},
          execution_attempts:{{
            first:{{
              execution_mode:'REAL',start_confirmed_at:'2026-09-04T10:02:00Z',
              completed_at:'2026-09-04T10:47:00Z',cleaning_params:{{label:'actual'}},
              metadata:{{floor_cleaning_finished_at:'2026-09-04T10:42:00Z'}}
            }}
          }}
        }};
        if(x._historyCleaningProfileText(job)!=='actual') throw new Error('profile');
        if(x._historyPhysicalDurationSeconds(job)!==3600) throw new Error(String(x._historyPhysicalDurationSeconds(job)));
        if(x._historyTime(job)!=='11:02') throw new Error(x._historyTime(job));
        const html=x._historyHtml({{history:[job]}});
        for(const expected of ['Завершено','Режим уборки','Длительность','actual','01:00:00','11:02','С ожиданием']){{
          if(!html.includes(expected)) throw new Error(expected+' missing: '+html);
        }}
        const order=['Завершено','Расписание','Источник','Режим выполнения','Режим уборки','Длительность','Результат','Причина'];
        let previous=-1;
        for(const label of order){{const position=html.indexOf(`<span>${{label}}</span>`);if(position<=previous)throw new Error('column order: '+html);previous=position;}}
        if(html.includes('Успешно выполнено')||html.includes('<small>С ожиданием</small>')) throw new Error(html);
        const failedHtml=x._historyHtml({{history:[{{...job,job_id:'job-2',result:'FAILED',reason_code:'failure'}}]}});
        if(!failedHtml.includes('Ошибка выполнения')) throw new Error(failedHtml);
        if(html.includes('сегодня')) throw new Error(html);
        """
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_recent_jobs_use_full_physical_span_and_safe_legacy_fallback():
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        global.navigator={{language:'en-US'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01230');
        const x=new C();
        const multi={{execution_mode:'REAL',finished_at:'2026-09-04T10:25:00Z',execution_attempts:{{
          a:{{execution_mode:'REAL',start_confirmed_at:'2026-09-04T10:00:00Z',completed_at:'2026-09-04T10:10:00Z'}},
          b:{{execution_mode:'REAL',start_confirmed_at:'2026-09-04T10:11:00Z',completed_at:'2026-09-04T10:22:00Z',metadata:{{floor_cleaning_finished_at:'2026-09-04T10:20:00Z'}}}},
          dry:{{execution_mode:'DRY_RUN',start_confirmed_at:'2026-09-04T09:00:00Z',completed_at:'2026-09-04T12:00:00Z'}}
        }}}};
        if(x._historyPhysicalDurationSeconds(multi)!==1500) throw new Error('multi');
        const legacy={{execution_mode:'REAL',actual_start:'2026-09-04T10:00:00Z',finished_at:'2026-09-04T10:09:30Z'}};
        if(x._historyPhysicalDurationSeconds(legacy)!==570) throw new Error('legacy');
        const simulated={{execution_mode:'DRY_RUN',actual_start:'2026-09-04T10:00:00Z',finished_at:'2026-09-04T10:09:30Z'}};
        if(x._historyPhysicalDurationSeconds(simulated)!==null) throw new Error('simulated');
        """
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_recent_job_layout_keeps_new_columns_responsive():
    panel = PANEL.read_text(encoding="utf-8")
    assert "grid-template-columns:.75fr 1fr .95fr 1.15fr 2fr .78fr .75fr 1.2fr" in panel
    assert ".history-job-list-header>span { min-width:0; white-space:normal; overflow-wrap:anywhere; }" in panel
    assert ".history-cell-value { display:block; min-width:0; max-width:100%; white-space:normal; overflow-wrap:anywhere; }" in panel
    assert ".history-job-summary>span:nth-child(1) .history-cell-value,.history-job-summary>span:nth-child(6) .history-cell-value,.history-job-summary>span:nth-child(7) .history-cell-value { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; overflow-wrap:normal; }" in panel
    assert ".history-job-summary>span:nth-child(3),.history-job-summary>span:nth-child(4),.history-job-summary>span:nth-child(8){display:none}" in panel
    assert ".history-cleaning-profile{white-space:normal" not in panel
