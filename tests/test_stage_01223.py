"""Regression coverage for conservative live interpolation in 0.12.23."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(MODULE))

from live_progress import robust_observed_rate  # noqa: E402


def _sample(at: datetime, percent: float) -> dict[str, object]:
    return {"observed_at": at.isoformat(), "clean_percent": percent}


def test_latest_plateau_does_not_reuse_and_reanchor_an_old_spike():
    start = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    samples = [
        _sample(start, 5),
        _sample(start + timedelta(milliseconds=200), 6),
        _sample(start + timedelta(seconds=10), 6),
    ]
    assert robust_observed_rate(samples, "clean_percent", maximum=0.25) is None


def test_clustered_updates_use_a_long_baseline_instead_of_the_tiny_delta():
    start = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    samples = [
        _sample(start, 5),
        _sample(start + timedelta(milliseconds=200), 6),
        _sample(start + timedelta(seconds=9, milliseconds=900), 6),
        _sample(start + timedelta(seconds=10, milliseconds=200), 7),
    ]
    estimate = robust_observed_rate(samples, "clean_percent", maximum=0.25)
    assert estimate is not None
    assert 0.14 < estimate.per_second < 0.16
    assert estimate.anchor_at == start + timedelta(seconds=10, milliseconds=200)


def test_implausible_counter_rate_is_rejected():
    start = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    samples = [_sample(start, 0), _sample(start + timedelta(seconds=10), 10)]
    assert robust_observed_rate(samples, "clean_percent", maximum=0.25) is None


def test_frontend_bounds_each_visual_advance_and_keeps_forecast_eta():
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class {{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01226');
        const x=new C();
        x._displayNow=()=>new Date('2026-09-03T10:20:00Z');
        x._robotActivityCode=()=> 'cleaning';
        x._formatDurationSeconds=()=> '20:00';
        x._formatNumber=(value)=>Number(value).toFixed(1);
        x._formatTimeOnly=(value)=>value.toISOString().slice(11,16);
        x._tr=(key,values)=>key==='panel.m2_unit'?'m²':key==='panel.live_eta'?`ETA ${{values.time}}`:key;
        const job={{state:'RUNNING',actual_start:'2026-09-03T10:00:00Z',live:{{
          started_at:'2026-09-03T10:00:00Z',robot_clean_percent:40,cleaning_area_m2:3.7,
          observation:{{phase:'CLEANING'}},interpolation:{{
            area_m2_per_second:10,percent_per_second:10,
            area_anchor_at:'2026-09-03T10:19:51Z',percent_anchor_at:'2026-09-03T10:19:51Z',
            max_age_seconds:12,max_area_increment_m2:0.2,max_percent_increment:2
          }},forecast:{{expected_total_seconds:1800,eta_at:'2026-09-03T10:30:00Z'}}
        }}}};
        const parts=x._liveMetrics(job);
        if(!parts.includes('≈3.9 m²')) throw new Error(JSON.stringify(parts));
        if(!parts.includes('≈42%')) throw new Error(JSON.stringify(parts));
        if(!parts.includes('ETA 10:30')) throw new Error(JSON.stringify(parts));
        if(parts.includes('≈99%')) throw new Error(JSON.stringify(parts));
        """
    )
    subprocess.run(["node", "-e", script], check=True, cwd=ROOT)


def test_frontend_uses_bounded_backend_time_estimate_when_robot_percent_is_unknown():
    script = textwrap.dedent(
        f"""
        global.HTMLElement=class {{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01226');
        const x=new C();
        x._displayNow=()=>new Date('2026-09-03T10:19:50Z');
        x._robotActivityCode=()=> 'cleaning';
        x._formatDurationSeconds=()=> '19:50';
        x._formatTimeOnly=()=> '10:20';
        x._tr=(key,values)=>key==='panel.live_eta'?`ETA ${{values.time}}`:key;
        const job={{state:'RUNNING',actual_start:'2026-09-03T10:00:00Z',live:{{
          started_at:'2026-09-03T10:00:00Z',robot_clean_percent:null,observation:{{phase:'CLEANING'}},
          forecast:{{expected_total_seconds:1200,estimated_progress_percent:22,eta_at:'2026-09-03T10:20:00Z'}}
        }}}};
        const parts=x._liveMetrics(job);
        if(!parts.includes('≈22%')) throw new Error(JSON.stringify(parts));
        if(parts.includes('≈99%')||parts.includes('≈95%')) throw new Error(JSON.stringify(parts));
        """
    )
    subprocess.run(["node", "-e", script], check=True, cwd=ROOT)


def test_backend_publishes_strict_interpolation_guards():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'maximum=0.05' in source
    assert 'maximum=0.25' in source
    assert '"max_age_seconds": 12' in source
    assert '"max_percent_increment": 2.0' in source
    assert '"max_area_increment_m2": 0.2' in source
    assert 'estimated_progress_percent = min(95.0' in source
