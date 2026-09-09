"""Regression contracts for Vacuum Schedule 0.12.1 history multi-select filters."""

from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_history_filter_active_tags_are_compact_non_wrapping_tokens():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="history-active-filter-label"' in panel
    assert 'class="history-active-filter-remove icon-only"' in panel
    assert '.history-active-filter { min-width:0; max-width:220px; height:30px;' in panel
    assert 'white-space:nowrap;' in panel
    assert '.history-active-filter-label { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;' in panel
    assert 'button.history-active-filter-remove { width:22px!important;' in panel


def test_all_history_filter_dimensions_use_toggleable_multi_select_options():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_toggleHistoryFilterValue(group,value)' in panel
    assert '_historyFilterValues(group)' in panel
    assert '_historyFilterMatches(group,value)' in panel
    assert 'option("execution_mode","REAL"' in panel
    assert 'option("execution_mode","DRY_RUN"' in panel
    assert 'option("source","SCHEDULED"' in panel
    assert 'option("source","MANUAL"' in panel
    assert 'option("source","EXTERNAL"' in panel
    assert 'schedules.map(([id,name])=>option("schedule_id",id,name))' in panel
    assert 'data-history-filter-clear-value=' in panel


def test_history_multi_select_toggle_semantics_execute_in_real_panel_code():
    script = textwrap.dedent(f"""
        global.HTMLElement = class {{ attachShadow(){{ this.shadowRoot={{}}; return this.shadowRoot; }} }};
        global.customElements = {{ _m:new Map(), get(k){{return this._m.get(k)}}, define(k,v){{this._m.set(k,v)}} }};
        global.window = {{ localStorage:{{getItem(){{return null}},setItem(){{}}}}, matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}} }};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-0121');
        if (!C) throw new Error('0.12.1 panel component missing');
        const x=new C();

        x._toggleHistoryFilterValue('source','SCHEDULED');
        x._toggleHistoryFilterValue('source','MANUAL');
        if (JSON.stringify(x._historyFilterValues('source')) !== JSON.stringify(['SCHEDULED','MANUAL'])) throw new Error('same-group OR selection failed');
        if (!x._historyFilterMatches('source','SCHEDULED') || !x._historyFilterMatches('source','MANUAL') || x._historyFilterMatches('source','EXTERNAL')) throw new Error('source matching failed');

        x._toggleHistoryFilterValue('execution_mode','REAL');
        x._toggleHistoryFilterValue('execution_mode','DRY_RUN');
        if (x._historyFilterValues('execution_mode').length !== 2) throw new Error('execution mode multi-select failed');

        x._clearHistoryFilterValue('source','SCHEDULED');
        if (JSON.stringify(x._historyFilterValues('source')) !== JSON.stringify(['MANUAL'])) throw new Error('single chip removal failed');
        x._clearHistoryFilterValue('source','MANUAL');
        if (x._historyFilters.source !== 'all') throw new Error('empty selection must normalize to all');

        x._toggleHistoryFilterValue('schedule_id','schedule-a');
        x._toggleHistoryFilterValue('schedule_id','schedule-b');
        if (x._historyFilterValues('schedule_id').length !== 2) throw new Error('schedule multi-select failed');
        x._toggleHistoryFilterValue('schedule_id','all');
        if (x._historyFilters.schedule_id !== 'all') throw new Error('All must clear one dimension');
        console.log('ok');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
