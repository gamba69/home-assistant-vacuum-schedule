"""Regression coverage for current-profile grouping in Estimate 0.12.42."""
from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def _run_node(body: str) -> None:
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}} toggleAttribute(){{}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'en-US'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01242');
        const x=new C();
        x._language='en-US';x._schedulerEntryId='entry';
        {body}
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_same_zone_stale_profile_is_collapsed_not_current():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[
          {schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],cleaning_params:{cleaning_mode:'vacuum_mop',fan_mode:'balanced',passes:1},weekday_overrides:{}}
        ]}]};
        const rows=[
          {schedule_id:'old',schedule_name:'Kitchen old',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',fan_mode:'turbo',passes:2},trained:true,sample_count:30},
          {schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',fan_mode:'balanced',passes:1},trained:true,sample_count:3}
        ];
        const sorted=x._forecastSortRows(rows,'schedule',{enabled:true});
        if(sorted.active.length!==1 || sorted.active[0].profile_params.passes!==1) throw new Error('stale profile remained active');
        if(sorted.other.length!==1 || sorted.other[0].profile_params.passes!==2) throw new Error('stale profile not collapsed');
        const stale=x._forecastRowMatch(rows[0],'schedule');
        if(stale.applicable || stale.exact || stale.correction_count!==2) throw new Error(JSON.stringify(stale));
    ''')


def test_weekday_override_profile_is_current_and_stays_visible():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[
          {schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0,1],cleaning_params:{cleaning_mode:'vacuum_mop',passes:1},weekday_overrides:{'1':{passes:2}}}
        ]}]};
        const rows=[
          {schedule_id:'a',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',passes:1},trained:true,sample_count:3},
          {schedule_id:'b',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',passes:2},trained:true,sample_count:3},
          {schedule_id:'c',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',passes:3},trained:true,sample_count:3}
        ];
        const sorted=x._forecastSortRows(rows,'schedule',{enabled:true});
        const active=sorted.active.map(r=>r.profile_params.passes).sort().join('|');
        if(active!=='1|2') throw new Error('override not recognized '+active);
        if(sorted.other.length!==1 || sorted.other[0].profile_params.passes!==3) throw new Error('non-configured profile not collapsed');
    ''')


def test_zone_table_uses_same_current_effective_profiles():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[
          {schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],cleaning_params:{cleaning_mode:'vacuum_mop',water_mode:'standard',passes:1},weekday_overrides:{}}
        ]}]};
        const rows=[
          {zone_id:'kitchen',zone_name:'Kitchen',profile_params:{cleaning_mode:'vacuum_mop',water_mode:'standard',passes:1},trained:true,sample_count:3},
          {zone_id:'kitchen',zone_name:'Kitchen',profile_params:{cleaning_mode:'vacuum_mop',water_mode:'intense',passes:1},trained:true,sample_count:12},
          {zone_id:'bedroom',zone_name:'Bedroom',profile_params:{cleaning_mode:'vacuum_mop',water_mode:'standard',passes:1},trained:true,sample_count:99}
        ];
        const sorted=x._forecastSortRows(rows,'zone',{enabled:true});
        if(sorted.active.length!==1 || sorted.active[0].zone_id!=='kitchen' || sorted.active[0].profile_params.water_mode!=='standard') throw new Error('bad current zone rows');
        if(sorted.other.length!==2) throw new Error('bad collapsed count '+sorted.other.length);
    ''')


def test_disabled_model_has_no_current_rows():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[
          {schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],cleaning_params:{cleaning_mode:'vacuum_mop',passes:1},weekday_overrides:{}}
        ]}]};
        const rows=[{schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vacuum_mop',passes:1},trained:true,sample_count:3}];
        const sorted=x._forecastSortRows(rows,'schedule',{enabled:false});
        if(sorted.active.length!==0 || sorted.other.length!==1) throw new Error('disabled model must not expose current rows');
    ''')
