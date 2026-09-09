"""Regression tests for statistics and forecast spacing rules are present and legacy opaque forecast zone ids are resolved or hidden.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_statistics_and_forecast_spacing_rules_are_present():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.statistics-summary-reasons .history-report-section-head + .table-wrap { margin-top:10px; }' in panel
    assert '.forecast-model-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:16px}' in panel
    assert '.forecast-model-card .forecast-table-head{margin-top:18px;margin-bottom:8px}' in panel
    assert '.forecast-model-card .forecast-table-wrap + .forecast-table-head{margin-top:20px}' in panel


def test_legacy_opaque_forecast_zone_ids_are_resolved_or_hidden():
    opaque1 = "11a459715338520bb53edb4803e02de4"
    opaque2 = "5c124aaad06556f99432e1eb019441ef"
    unknown = "35610c3beca84b6ab1b2fafdf883aa29"
    script = textwrap.dedent(f"""
        global.HTMLElement = class {{ attachShadow(){{ this.shadowRoot={{}}; return this.shadowRoot; }} }};
        global.customElements = {{ _m:new Map(), get(k){{return this._m.get(k)}}, define(k,v){{this._m.set(k,v)}} }};
        global.window = {{ localStorage:{{getItem(){{return null}},setItem(){{}}}}, matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}} }};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-0114');
        const x=new C();
        x._language='ru-RU'; x._translations={{}}; x._fallbackTranslations={{}}; x._ru=true;
        x._schedulerEntryId='entry';
        x._settingsData={{cleaning_zones:[{{zone_id:'{opaque1}',name:'Гостиная'}},{{zone_id:'{opaque2}',name:'Столовая'}}]}};
        x._data={{entries:[{{entry_id:'entry',schedules:[{{schedule_id:'s1',name:'Гостиная'}},{{schedule_id:'s2',name:'Общие комнаты'}}]}}]}};
        const policy={{enabled:true,percentile:90,delta_percent:10,lookback_days:90}};
        const rows=[
          {{schedule_id:'s1',schedule_name:'s1',zone_ids:['{opaque1}'],zone_names:['{opaque1}'],profile_params:{{cleaning_mode:'vacuum_and_mop',fan_mode:'balanced',passes:2}},sample_count:4,trained:true,raw_percentile_value:100,delta_percent:10,forecast_value:110}},
          {{schedule_id:'s2',schedule_name:'s2',zone_ids:['{opaque1}','{opaque2}'],zone_names:['{opaque1}','{opaque2}'],profile_params:{{cleaning_mode:'vacuum_and_mop',fan_mode:'turbo',passes:2}},sample_count:4,trained:true,raw_percentile_value:100,delta_percent:10,forecast_value:110}},
          {{schedule_id:'s3',schedule_name:'Readable legacy job',zone_ids:['{unknown}'],zone_names:['{unknown}'],profile_params:{{fan_mode:'turbo',passes:1}},sample_count:4,trained:true,raw_percentile_value:100,delta_percent:10,forecast_value:110}}
        ];
        const model={{policy,generation:1,started_at:new Date().toISOString(),active_sample_count:12,trained:true,by_schedule:rows,by_zone:[{{zone_id:'{opaque1}',zone_name:'{opaque1}',profile_params:{{fan_mode:'turbo'}},sample_count:4,trained:true,raw_percentile_value:10,delta_percent:10,forecast_value:11}},{{zone_id:'{unknown}',zone_name:'{unknown}',profile_params:{{fan_mode:'turbo'}},sample_count:4,trained:true,raw_percentile_value:10,delta_percent:10,forecast_value:11}}],archives:[]}};
        const html=x._statisticsForecastHtml({{models:{{time:model,battery:model,clean_water:model,dirty_water:model}}}});
        for (const bad of ['{opaque1}','{opaque2}','{unknown}']) if (html.includes(bad)) throw new Error('opaque id leaked: '+bad);
        if (!html.includes('Гостиная')) throw new Error('current zone name missing');
        if (!html.includes('Столовая')) throw new Error('second current zone name missing');
        if (!html.includes('Общие комнаты')) throw new Error('current schedule name missing');
        if (!html.includes('Readable legacy job')) throw new Error('readable legacy schedule name missing');
        console.log('ok');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


