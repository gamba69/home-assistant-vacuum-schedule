"""Regression tests for forecast number formatter is defined not only called, forecast render is guarded against presentation exceptions, and real forecast html renderer does not throw on trained.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_forecast_number_formatter_is_defined_not_only_called():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_formatNumber(value, digits = 1)" in panel
    assert "this._formatNumber(" in panel


def test_forecast_render_is_guarded_against_presentation_exceptions():
    panel = PANEL.read_text(encoding="utf-8")
    assert "forecast=this._statisticsForecastHtml(this._forecastData);" in panel
    assert "const renderError=this._errorText(err);" in panel
    assert 'panel.forecast_load_failed' in panel




def test_real_forecast_html_renderer_does_not_throw_on_trained_payload():
    script = textwrap.dedent(f"""
        global.HTMLElement = class {{ attachShadow(){{ this.shadowRoot={{}}; return this.shadowRoot; }} }};
        global.customElements = {{ _m:new Map(), get(k){{return this._m.get(k)}}, define(k,v){{this._m.set(k,v)}} }};
        global.window = {{ localStorage:{{getItem(){{return null}},setItem(){{}}}}, matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}} }};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-0114');
        if (!C) throw new Error('current panel component missing');
        const x=new C();
        x._language='en-US'; x._translations={{}}; x._fallbackTranslations={{}}; x._ru=false;
        const policy={{enabled:true,percentile:90,delta_percent:15,lookback_days:60}};
        const scheduleRow={{schedule_name:'Daily',zone_names:['Kitchen'],profile_params:{{fan_mode:'turbo',passes:1}},sample_count:5,trained:true,raw_percentile_value:10.5,delta_percent:15,forecast_value:12.075}};
        const zoneRow={{zone_name:'Kitchen',profile_params:{{fan_mode:'turbo',passes:1}},sample_count:5,trained:true,raw_percentile_value:10.5,delta_percent:15,forecast_value:12.075}};
        const model={{policy,generation:1,started_at:new Date().toISOString(),active_sample_count:5,trained:true,by_schedule:[scheduleRow],by_zone:[zoneRow],archives:[]}};
        const html=x._statisticsForecastHtml({{models:{{time:model,battery:model,clean_water:model,dirty_water:model}}}});
        if (!html.includes('forecast-model-card')) throw new Error('forecast HTML missing');
        if (!html.includes('12.1')) throw new Error('formatted numeric forecast missing');
        console.log('ok');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
