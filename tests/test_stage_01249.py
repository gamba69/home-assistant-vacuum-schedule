"""Regression coverage for metric-aware Estimate applicability in 0.12.49."""
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


def test_water_estimate_ignores_fan_and_route_when_matching_current_profile():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[{
          schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],
          cleaning_params:{cleaning_mode:'vac_and_mop',cleaning_route:'fast',fan_mode:'turbo',mop_mode:'standard',water_mode:'weak',passes:1},
          weekday_overrides:{}
        }]}]};
        const row={schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],
          profile_params:{scope:'wet',cleaning_mode:'vac_and_mop',mop_mode:'standard',water_mode:'weak',passes:'1'},
          trained:true,sample_count:12};
        const water=x._forecastRowMatch(row,'schedule','clean_water');
        if(!water.applicable||!water.exact||water.correction_count!==0) throw new Error('water row hidden '+JSON.stringify(water));
        const dirty=x._forecastRowMatch(row,'schedule','dirty_water');
        if(!dirty.applicable||!dirty.exact) throw new Error('dirty row hidden '+JSON.stringify(dirty));
    ''')


def test_water_estimate_keeps_different_water_profile_collapsed():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[{
          schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],
          cleaning_params:{cleaning_mode:'vac_and_mop',fan_mode:'turbo',mop_mode:'standard',water_mode:'weak',passes:1},weekday_overrides:{}
        }]}]};
        const current={schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{scope:'wet',cleaning_mode:'vac_and_mop',mop_mode:'standard',water_mode:'weak',passes:'1'},trained:true,sample_count:4};
        const stale={schedule_id:'old',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{scope:'wet',cleaning_mode:'vac_and_mop',mop_mode:'standard',water_mode:'strong',passes:'1'},trained:true,sample_count:50};
        const sorted=x._forecastSortRows([stale,current],'schedule',{enabled:true},'clean_water');
        if(sorted.active.length!==1||sorted.active[0]!==current) throw new Error('wrong active '+JSON.stringify(sorted.active));
        if(sorted.other.length!==1||sorted.other[0]!==stale) throw new Error('wrong collapsed '+JSON.stringify(sorted.other));
    ''')


def test_explicit_dry_current_variant_does_not_activate_wet_water_history():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[{
          schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[1],
          cleaning_params:{cleaning_mode:'vac_and_mop',mop_mode:'standard',water_mode:'weak',passes:1},
          weekday_overrides:{'1':{cleaning_mode:'vacuum'}}
        }]}]};
        const wet={schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{scope:'wet',cleaning_mode:'vac_and_mop',mop_mode:'standard',water_mode:'weak',passes:'1'},trained:true,sample_count:10};
        const match=x._forecastRowMatch(wet,'schedule','clean_water');
        if(match.applicable) throw new Error('dry-only current schedule activated wet history');
    ''')


def test_non_water_estimate_still_uses_full_profile():
    _run_node(r'''
        x._data={entries:[{entry_id:'entry',schedules:[{
          schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],
          cleaning_params:{cleaning_mode:'vac_and_mop',cleaning_route:'fast',fan_mode:'turbo',mop_mode:'standard',water_mode:'weak',passes:1},weekday_overrides:{}
        }]}]};
        const same={schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vac_and_mop',cleaning_route:'fast',fan_mode:'turbo',mop_mode:'standard',water_mode:'weak',passes:1}};
        const otherFan={schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{cleaning_mode:'vac_and_mop',cleaning_route:'fast',fan_mode:'balanced',mop_mode:'standard',water_mode:'weak',passes:1}};
        if(!x._forecastRowMatch(same,'schedule','time').applicable) throw new Error('exact time row hidden');
        if(x._forecastRowMatch(otherFan,'schedule','time').applicable) throw new Error('time matcher incorrectly ignored fan');
    ''')
