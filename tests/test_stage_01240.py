"""Regression coverage for Estimate ordering and semantic Area percentile in 0.12.40."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"

import sys
sys.path.insert(0, str(MODULE))
from forecast_models import ForecastPolicyConfig, model_tables  # noqa: E402
from statistics_models import aggregate_records  # noqa: E402


def _record(index: int) -> dict:
    at = datetime(2026, 9, 6, tzinfo=timezone.utc) - timedelta(days=index)
    return {
        "job_id": f"j{index}",
        "schedule_id": "s1",
        "schedule_name": "Daily",
        "origin": "SCHEDULED",
        "execution_mode": "REAL",
        "result": "SUCCESS",
        "planned_start": at.isoformat(),
        "actual_start": at.isoformat(),
        "finished_at": (at + timedelta(seconds=100 + index)).isoformat(),
        "cleaning_params_snapshot": {"cleaning_mode": "vacuum_mop", "passes": 1},
        "time": {"physical_execution_seconds": 100 + index, "wait": {"total_seconds": 0, "count": 0}},
        "battery": {"consumed_percent": 10 + index},
        "area": {"physical_cleaned_m2": 10 + index, "processed_m2": 10 + index},
        "zones": [{
            "zone_id": "kitchen",
            "zone_name": "Kitchen",
            "result": "SUCCESS",
            "attribution": "measured",
            "nominal_area_m2": 10.0,
            "attributed_floor_area_m2": 10 + index,
            "attributed_processed_area_m2": 10 + index,
            "attributed_cleaning_seconds": 100 + index,
            "attributed_battery_percent": 10 + index,
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


def test_area_distribution_uses_time_percentile_when_mapping_is_supplied():
    aggregate = aggregate_records(
        [_record(0), _record(1), _record(2), _record(3), _record(4)],
        metric_percentiles={"time": 80, "area": 80, "battery": 95, "clean_water": 85, "dirty_water": 75},
    )
    assert aggregate["area"]["per_job_m2"]["percentile_number"] == 80
    assert aggregate["area"]["processed_per_job_m2"]["percentile_number"] == 80
    assert aggregate["by_schedule"][0]["floor_area_m2"]["percentile_number"] == 80
    assert aggregate["zones"]["rows"][0]["floor_area_m2"]["percentile_number"] == 80


def test_statistics_manager_maps_area_to_current_time_policy():
    source = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    block = source[source.index("    def _statistics_percentiles"):source.index("    def _ensure_forecast_branches")]
    assert 'values["area"] = values["time"]' in block


def test_model_tables_expose_last_sample_time_for_freshness_tie_break():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=80, delta_percent=10, lookback_days=90)
    samples = [_forecast_sample(now - timedelta(days=i), 10 + i, i) for i in (2, 0, 1)]
    table = model_tables(samples, policy, minimum_samples=3)
    assert table["by_schedule"][0]["last_sample_at"] == now.isoformat()
    assert table["by_zone"][0]["last_sample_at"] == now.isoformat()


def test_estimate_rows_sort_by_name_then_match_quality_data_quality_freshness_and_id():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}} toggleAttribute(){{}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'en-US'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01242');
        const x=new C();
        x._language='en-US';x._schedulerEntryId='entry';
        x._data={{entries:[{{entry_id:'entry',schedules:[
          {{schedule_id:'k',name:'Kitchen',enabled:true,paused:false,targets:['kitchen'],weekdays:[0],cleaning_params:{{cleaning_mode:'vacuum_mop',passes:1}},weekday_overrides:{{}}}},
          {{schedule_id:'l',name:'Living',enabled:true,paused:false,targets:['living'],weekdays:[0],cleaning_params:{{cleaning_mode:'vacuum_mop',passes:1}},weekday_overrides:{{}}}}
        ]}}]}};
        const rows=[
          {{schedule_id:'l',schedule_name:'Living',zone_ids:['living'],profile_params:{{cleaning_mode:'vacuum_mop',passes:1}},trained:true,sample_count:100,last_sample_at:'2026-09-06T12:00:00+00:00'}},
          {{schedule_id:'k-old',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{{cleaning_mode:'vacuum_mop',passes:2}},trained:true,sample_count:50,last_sample_at:'2026-09-06T13:00:00+00:00'}},
          {{schedule_id:'k',schedule_name:'Kitchen',zone_ids:['kitchen'],profile_params:{{cleaning_mode:'vacuum_mop',passes:1}},trained:true,sample_count:3,last_sample_at:'2026-09-01T10:00:00+00:00'}},
          {{schedule_id:'unused',schedule_name:'Bedroom',zone_ids:['bedroom'],profile_params:{{cleaning_mode:'vacuum_mop',passes:1}},trained:true,sample_count:999,last_sample_at:'2026-09-06T14:00:00+00:00'}}
        ];
        const sorted=x._forecastSortRows(rows,'schedule',{{enabled:true}});
        const active=sorted.active.map(r=>`${{r.schedule_name}}:${{r.profile_params.passes}}`).join('|');
        if(active!=='Kitchen:1|Living:1') throw new Error('bad active '+active);
        const other=sorted.other.map(r=>`${{r.schedule_name}}:${{r.profile_params.passes}}`).join('|');
        if(other!=='Bedroom:1|Kitchen:2') throw new Error('bad other '+other);
        const exact=x._forecastRowMatch(rows[2],'schedule');
        const corrected=x._forecastRowMatch(rows[1],'schedule');
        if(!exact.exact||!exact.applicable||exact.correction_count!==0||corrected.exact||corrected.applicable||corrected.correction_count!==1) throw new Error(JSON.stringify({{exact,corrected}}));
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_area_header_uses_time_percentile_in_statistics_table():
    panel = PANEL.read_text(encoding="utf-8")
    stats = panel[panel.index("  _statisticsHtml() {"):]
    assert 'const areaLabel=this._formatPercentileLabel(configuredPercentile("time"));' in stats
