"""Regression coverage for Statistics/Estimate presentation in 0.12.39."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
sys.path.insert(0, str(MODULE))

from forecast_models import ForecastPolicyConfig, model_tables  # noqa: E402
from statistics_models import aggregate_records, distribution  # noqa: E402


def _record(index: int) -> dict:
    seconds = 100.0 + index * 10.0
    battery = 10.0 + index
    clean = 100.0 + index * 10.0
    dirty = 50.0 + index * 5.0
    return {
        "job_id": f"j{index}",
        "schedule_id": "s1",
        "schedule_name": "Daily",
        "result": "SUCCESS",
        "reason_code": "success",
        "finished_at": f"2026-09-0{index + 1}T10:00:00+03:00",
        "cleaning_params_snapshot": {"cleaning_mode": "vacuum_mop", "passes": 1},
        "time": {"physical_execution_seconds": seconds, "wait": {"total_seconds": index, "count": 0}},
        "battery": {"consumed_percent": battery},
        "area": {"physical_cleaned_m2": 10.0, "processed_m2": 10.0},
        "water_usage": {"available": True, "clean_used_ml_eq": clean, "dirty_gained_ml_eq": dirty},
        "zones": [{
            "zone_id": "kitchen",
            "zone_name": "Kitchen",
            "result": "SUCCESS",
            "attribution": "measured",
            "nominal_area_m2": 10.0,
            "attributed_floor_area_m2": 10.0,
            "attributed_processed_area_m2": 10.0,
            "attributed_cleaning_seconds": seconds,
            "attributed_battery_percent": battery,
        }],
    }


def _forecast_sample(at: datetime, value: float, index: int) -> dict:
    return {
        "sample_id": f"f{index}",
        "job_id": f"f{index}",
        "schedule_id": "s1",
        "schedule_name": "Daily",
        "at": at.isoformat(),
        "result": "SUCCESS",
        "params": {"cleaning_mode": "vacuum_mop", "passes": 1},
        "job_value": value,
        "zone_ids": ["kitchen"],
        "zones": [{"zone_id": "kitchen", "zone_name": "Kitchen", "value": value, "result": "SUCCESS"}],
    }


def test_distribution_exposes_user_selected_percentile_without_removing_fixed_diagnostics():
    result = distribution([10, 20, 30, 40, 50], 80)
    assert result["median"] == 30
    assert result["percentile_number"] == 80
    assert result["percentile"] == 42
    assert result["p90"] == 46


def test_statistics_use_semantic_percentile_for_each_resource():
    aggregate = aggregate_records(
        [_record(0), _record(1), _record(2)],
        metric_percentiles={"time": 80, "battery": 95, "clean_water": 85, "dirty_water": 75},
    )
    assert aggregate["time"]["execution_seconds"]["percentile_number"] == 80
    assert aggregate["time"]["seconds_per_m2"]["percentile_number"] == 80
    assert aggregate["battery"]["consumed_percent"]["percentile_number"] == 95
    zone = aggregate["zones"]["rows"][0]
    assert zone["cleaning_seconds"]["percentile_number"] == 80
    assert zone["battery_consumed_percent"]["percentile_number"] == 95
    assert zone["clean_water_ml_eq_per_m2"]["percentile_number"] == 85
    assert zone["dirty_water_ml_eq_per_m2"]["percentile_number"] == 75
    # Area follows the Time percentile because it has no independent Forecast policy.
    assert zone["floor_area_m2"]["percentile_number"] == 80


def test_estimate_model_table_carries_median_before_configured_percentile():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=80, delta_percent=10, lookback_days=90)
    rows = [_forecast_sample(now - timedelta(days=i), value, i) for i, value in enumerate((10, 20, 30))]
    table = model_tables(rows, policy, minimum_samples=3)
    schedule = table["by_schedule"][0]
    zone = table["by_zone"][0]
    assert schedule["median_value"] == 20
    assert schedule["raw_percentile_value"] == 26
    assert schedule["percentile"] == 80
    assert zone["median_value"] == 20
    assert zone["raw_percentile_value"] == 26


def test_estimate_ui_renamed_uses_subscripts_shared_table_style_and_collapses_other_data():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.forecast_tab"] == "Оценка"
    assert ru["panel.estimate_value"] == "Оценка"
    assert 'const subs={"0":"₀","1":"₁","2":"₂","3":"₃","4":"₄","5":"₅","6":"₆","7":"₇","8":"₈","9":"₉"};' in panel
    forecast = panel[panel.index("  _statisticsForecastHtml("):panel.index("\n\n  _statisticsHtml() {", panel.index("  _statisticsForecastHtml("))]
    assert '${this._tr("panel.median")}</th><th class="metric-header">${percentileLabel}</th>' in forecast
    assert '<details class="forecast-other-data">' in forecast
    assert '_forecastSortRows(rows,kind,policy,metric)' in forecast
    assert 'th.metric-header,td.metric-number { text-align:right;' in panel
    assert '<col class="forecast-col-object">' not in panel


def test_current_estimate_applicability_includes_weekday_overrides_but_not_fallback_only_profiles():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}} toggleAttribute(){{}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01239');
        const x=new C();
        x._schedulerEntryId='entry';
        x._data={{entries:[{{entry_id:'entry',schedules:[{{schedule_id:'s1',enabled:true,paused:false,targets:['kitchen'],weekdays:[0,1],cleaning_params:{{cleaning_mode:'vacuum_mop',passes:1}},weekday_overrides:{{'1':{{passes:2}}}}}}]}}]}};
        const exact=x._forecastRowApplicability({{zone_id:'kitchen',profile_params:{{cleaning_mode:'vacuum_mop',passes:1}}}},'zone');
        const override=x._forecastRowApplicability({{zone_id:'kitchen',profile_params:{{cleaning_mode:'vacuum_mop',passes:2}}}},'zone');
        const fallback=x._forecastRowApplicability({{zone_id:'kitchen',profile_params:{{cleaning_mode:'vacuum_mop',passes:3}}}},'zone');
        const unrelated=x._forecastRowApplicability({{zone_id:'hall',profile_params:{{cleaning_mode:'vacuum_mop',passes:1}}}},'zone');
        if(exact!==2||override!==2||fallback!==0||unrelated!==0) throw new Error(JSON.stringify({{exact,override,fallback,unrelated}}));
        if(x._formatPercentileLabel(95)!=='P₉₅') throw new Error('bad percentile label '+x._formatPercentileLabel(95));
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
