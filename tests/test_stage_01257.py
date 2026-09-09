"""0.12.57 schedule-row visual compaction regression."""
from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_schedule_status_is_plain_text_not_badge():
    panel = PANEL.read_text(encoding="utf-8")
    schedule_cell = panel.split('<td class="name-cell"', 1)[1].split('</td>', 1)[0]
    assert 'class="schedule-row-state"' in schedule_cell
    assert 'schedule-row-state-text ${this._escape(this._stateChipClass' in schedule_cell
    assert '_stateLabel(this._scheduleJob' in schedule_cell
    assert '_stateChipHtml(' not in schedule_cell
    assert 'class="schedule-row-state"' in schedule_cell
    assert '.schedule-row-state{margin-top:3px;font-size:10px' in panel
    assert '.schedule-row-state-text{display:inline;background:transparent!important' in panel
    assert 'background:transparent!important' in panel
    assert '.schedule-row-state .state-chip' not in panel


def test_force_preemption_marker_is_inline_after_force_label_and_not_bold():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01257');
        const x=new C();
        x._tr=(k,p)=>{{
          if(k==='panel.source_force') return 'Досрочно';
          if(k==='panel.force_preempt_short_yes') return '↑ плановых';
          if(k==='panel.force_preempt_short_no') return '↓ плановых';
          if(k==='panel.force_preempts_scheduled_help') return 'важнее';
          if(k==='panel.force_respects_scheduled_help') return 'обычно';
          if(k==='panel.force_summary') return `${{p.window}} · приоритет ${{p.priority}} · групп условий: ${{p.groups}}`;
          return k;
        }};
        const yes=x._forceScheduleSummary({{force_enabled:true,force_max_advance_minutes:390,force_priority:90,force_condition_groups:[{{}}],force_preempts_scheduled:true}});
        const no=x._forceScheduleSummary({{force_enabled:true,force_max_advance_minutes:105,force_priority:80,force_condition_groups:[{{}}],force_preempts_scheduled:false}});
        if(!yes.includes('<b>Досрочно</b> <span class="force-preempt-short"')) throw new Error(yes);
        if(!yes.includes('>↑ плановых</span>:')) throw new Error(yes);
        if(!no.includes('>↓ плановых</span>:')) throw new Error(no);
        if(yes.includes('· ↑ плановых') || no.includes('· ↓ плановых')) throw new Error('marker still appended');
        if(yes.includes('<b>↑ плановых</b>') || no.includes('<b>↓ плановых</b>')) throw new Error('marker is bold');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
