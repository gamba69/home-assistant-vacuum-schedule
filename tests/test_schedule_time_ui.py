"""Tests weekly-time editing and active-job time-window presentation.

The UI must keep weekly day times independent from specific-date time and show relative offsets above absolute clock times.
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
        const C=customElements.get('vacuum-schedule-panel-01215');
        const x=new C(); x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        {body}
        """
    )
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)


def test_apply_to_all_sets_disabled_day_drafts_without_using_specific_date_time():
    result = _run_node(
        """
        x._form={weekday_times:{mon:'10:00',wed:'11:00'},local_time:'21:45'};
        if(!x._applyWeekdayBulkTime('15:30')) throw new Error('bulk apply rejected');
        if(x._form.weekday_times.mon!=='15:30'||x._form.weekday_times.wed!=='15:30') throw new Error(JSON.stringify(x._form));
        if(Object.prototype.hasOwnProperty.call(x._form.weekday_times,'tue')) throw new Error('disabled Tuesday was enabled');
        if(x._form._weekday_draft_times.tue!=='15:30') throw new Error('disabled Tuesday did not receive bulk time');
        x._form.local_time='07:05';
        const html=x._weekdayScheduleRows();
        const marker='data-weekday-time="tue" value="15:30"';
        if(!html.includes(marker)) throw new Error('specific-date time leaked into disabled weekday: '+html);
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_active_window_cells_show_relative_first_and_absolute_time_second():
    result = _run_node(
        """
        const latest=x._activeLatestCell({planned_start:'2026-09-03T10:00:00Z',deadline_at:'2026-09-03T18:00:00Z'});
        const early=x._activeEarlyCell({planned_start:'2026-09-03T11:30:00Z',force:{max_advance_minutes:150,window_start:'2026-09-03T09:00:00Z'}});
        if(!latest.includes('<span>+08:00</span><small>18:00</small>')) throw new Error(latest);
        if(!early.includes('<span>-02:30</span><small>09:00</small>')) throw new Error(early);
        if(/завтра|tomorrow|2026-09-03/i.test(latest+early)) throw new Error('date leaked into compact cell');
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
