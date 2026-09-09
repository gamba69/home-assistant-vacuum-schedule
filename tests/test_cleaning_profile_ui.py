"""Tests mode-aware presentation of effective cleaning profiles.

Vacuum-only and mop-only jobs hide irrelevant controls, while weekday corrections show the complete merged result on one line.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def _run_node(body: str) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}},querySelector(){{return null}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}}; global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01226');
        const x=new C();
        x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._compactPresetLabel=(kind,value)=>`${{kind}}:${{value}}`;
        x._presetLabel=(kind,value)=>`${{kind}}:${{value}}`;
        x._tr=(key)=>({{
          'panel.route_short':'Route','panel.mop_short':'Mop','panel.water_short':'Water',
          'panel.correction':'Correction','panel.cleaning_type':'Type','panel.suction_power':'Fan',
          'panel.cleaning_route':'Route full','panel.mopping_mode':'Mop full',
          'panel.water_flow_intensity':'Water full','panel.cleaning_passes':'Passes',
          'panel.min_battery':'Min battery','panel.min_start_window':'Min window'
        }}[key]||key);
        x._weekdayLabel=(code)=>({{mon:'Mon',tue:'Tue',wed:'Wed',thu:'Thu',fri:'Fri',sat:'Sat',sun:'Sun'}}[code]||code);
        {body}
        """
    )
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)


def test_compact_profile_hides_controls_irrelevant_to_effective_mode():
    result = _run_node(
        """
        const shared={passes:2,fan_mode:'turbo',cleaning_route:'fast',mop_mode:'standard',water_mode:'standard'};
        const vacuum=x._compactCleaningProfileText({...shared,cleaning_mode:'vacuum'});
        const mop=x._compactCleaningProfileText({...shared,cleaning_mode:'mop'});
        const combined=x._compactCleaningProfileText({...shared,cleaning_mode:'vac_and_mop'});
        if(vacuum!=='cleaning_mode:vacuum · 2× · fan_mode:turbo · Route: cleaning_route:fast') throw new Error(vacuum);
        if(mop!=='cleaning_mode:mop · 2× · Mop: mop_mode:standard · Water: water_mode:standard') throw new Error(mop);
        for(const token of ['fan_mode:turbo','Route: cleaning_route:fast','Mop: mop_mode:standard','Water: water_mode:standard']) {
          if(!combined.includes(token)) throw new Error(combined);
        }
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_schedule_correction_shows_full_merged_effective_profile_in_one_line():
    result = _run_node(
        """
        const row={
          cleaning_summary:{cleaning_mode:'vac_and_mop',passes:2,fan_mode:'turbo',mop_mode:'standard',water_mode:'standard'},
          weekday_overrides_codes:{
            wed:{cleaning_mode:'vacuum',passes:1},
            fri:{cleaning_mode:'vacuum',passes:1},
            sun:{cleaning_mode:'vacuum',passes:1}
          }
        };
        const html=x._scheduleOverrideSummary(row);
        const expected='Wed · Fri · Sun — cleaning_mode:vacuum · 1× · fan_mode:turbo';
        if(!html.includes(expected)) throw new Error(html);
        if(html.includes('Mop:')||html.includes('Water:')) throw new Error('irrelevant mop settings leaked: '+html);
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_primary_job_parameter_details_use_the_same_mode_visibility_rules():
    result = _run_node(
        """
        const html=x._jobCleaningParamsHtml({cleaning_params:{cleaning_mode:'vacuum',passes:1,fan_mode:'turbo',mop_mode:'standard',water_mode:'standard'}});
        if(!html.includes('Fan')||!html.includes('fan_mode:turbo')) throw new Error(html);
        if(html.includes('Mop full')||html.includes('Water full')) throw new Error('irrelevant details leaked: '+html);
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_schedule_correction_css_prevents_row_height_growth():
    panel = PANEL.read_text(encoding="utf-8")
    assert ".schedule-corrections-summary" in panel
    assert "white-space:nowrap" in panel
    assert "text-overflow:ellipsis" in panel
