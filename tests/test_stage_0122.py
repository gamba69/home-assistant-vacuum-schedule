"""Live active-job status and progress contracts for Vacuum Schedule 0.12.3."""
from pathlib import Path
import json
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_scheduler_status_exposes_live_observation_progress_and_time_forecast():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    block = source[source.index("def _live_execution_status"):source.index("def _job_status_dict")]
    assert '"robot_clean_percent": attempt.stable_clean_percent()' in block
    assert '"cleaning_area_m2": self._normalized_live_area(attempt)' in block
    assert "def _normalized_live_area" in source
    assert '"battery_percent": current_battery' in block
    assert '"battery_consumed_percent"' in block
    assert 'self.statistics.forecast_estimate(' in block
    assert '"time"' in block
    assert '"expected_total_seconds": forecast_seconds' in block
    assert '"eta_at": eta_at.isoformat() if eta_at else None' in block
    assert 'estimated_progress_percent = min(95.0' in block
    assert 'for raw_zone_id in attempt.zone_ids:' in block
    assert '"current_zone_ids": current_zone_ids' in block
    assert '"current_zone_names": current_zone_names' in block


def test_observer_collects_optional_dock_activity_diagnostics_without_making_drying_a_session_state():
    source = (MODULE / "execution_observer.py").read_text(encoding="utf-8")
    for key in ("wash_status", "wash_phase", "dry_status", "dust_collection_status", "charge_status", "back_type"):
        assert f'"{key}"' in source
    service_set = source[source.index("_ROBOROCK_SERVICE_STATES"):source.index("_ROBOROCK_SEGMENT_STATES")]
    assert "drying" not in service_set
    assert "dry_status" not in service_set
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    assert '"wash_status", "wash_phase", "dry_status", "dust_collection_status"' in backend


def test_active_jobs_table_uses_now_column_and_removes_state_column():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[panel.index("_activeJobsHtml(entry)"):panel.index("_executionModeLabel(mode)")]
    assert '<span>${this._tr("panel.now_short")}</span>' in block
    assert 'data-live-job=' in block
    assert '${this._nowHtml(job)}' in block
    assert '<span>${this._tr("panel.state")}</span>' not in block
    assert '${this._stateChipHtml(job)}' not in block
    assert block.index('panel.now_short') < block.index('panel.planned')


def test_now_column_renders_specific_activity_measured_progress_and_eta():
    ru_full = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    ru = {key: ru_full[key] for key in (
        "panel.live_activity.vacuuming_and_mopping", "panel.cleaning_in_progress",
        "panel.live_eta", "panel.floor_cleaning_100", "panel.live_forecast_overrun",
        "panel.live_activity.cleaning_zone_named", "panel.live_activity.cleaning_zones_named",
        "panel.unknown", "panel.now_error",
    )}
    script = textwrap.dedent(f"""
        global.HTMLElement = class {{ attachShadow(){{ this.shadowRoot={{querySelectorAll(){{return []}}}}; return this.shadowRoot; }} }};
        global.customElements = {{ _m:new Map(), get(k){{return this._m.get(k)}}, define(k,v){{this._m.set(k,v)}} }};
        global.window = {{ localStorage:{{getItem(){{return null}},setItem(){{}}}}, matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}} }};
        global.history={{replaceState(){{}}}};
        global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-0122');
        if (!C) throw new Error('0.12.3 panel component missing');
        const x=new C();
        x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._translations={json.dumps(ru, ensure_ascii=False)};
        x._fallbackTranslations=x._translations;
        x._displayNow=()=>new Date('2026-08-31T13:10:00Z');
        x._timeZone=()=> 'UTC';
        const job={{
          state:'RUNNING', actual_start:'2026-08-31T13:00:00Z', zone_runs:{{a:{{}},b:{{}}}},
          execution_attempts:{{x:{{state:'RUNNING',metadata:{{last_observation:{{vendor_status:'sweep_and_mop',phase:'CLEANING'}}}}}}}},
          live:{{attempt_state:'RUNNING',started_at:'2026-08-31T13:00:00Z',current_zone_name:'Кухня',robot_clean_percent:42,cleaning_area_m2:8.4,battery_percent:80,observation:{{vendor_status:'sweep_and_mop',phase:'CLEANING',clean_percent:42,cleaning_area_m2:8.4}},forecast:{{expected_total_seconds:1200}}}}
        }};
        const html=x._nowHtml(job);
        if(!html.includes('Убираем зону Кухня')) throw new Error(html);
        if(!html.includes('8,4') && !html.includes('8.4')) throw new Error('area missing: '+html);
        if(!html.includes('42%')) throw new Error('measured progress missing: '+html);
        if(html.includes('≈42%')) throw new Error('measured progress marked estimated: '+html);
        if(!html.includes('≈ до 13:20')) throw new Error('eta missing: '+html);
        console.log('ok');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_now_column_estimated_progress_and_dock_activity_semantics():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'estimated?"≈":""' in panel
    assert 'going_to_wash_the_mop:"going_to_wash_mop"' in panel
    assert 'washing_the_mop:"washing_mop"' in panel
    assert 'emptying_the_bin:"emptying_bin"' in panel
    assert 'this._diagnosticActivityActive(obs.dry_status)' in panel
    assert 'this._diagnosticActivityActive(obs.charge_status)' in panel
    assert 'panel.floor_cleaning_100' in panel
    assert 'new Set(["charging","waiting_to_charge"]).has(activity)' in panel


def test_now_column_refreshes_elapsed_progress_and_eta_without_full_page_reload():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._activeLiveTimer = setInterval(() => this._refreshActiveLiveStatus(), 10000);' in panel
    assert 'cell.innerHTML=this._nowHtml(job);' in panel
    assert 'this._clearActiveLiveTimer();' in panel


def test_0122_localization_is_complete_for_live_activity_codes():
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    keys = {
        "panel.now_short", "panel.live_eta", "panel.floor_cleaning_100",
        "panel.live_activity.vacuuming", "panel.live_activity.mopping",
        "panel.live_activity.vacuuming_and_mopping", "panel.live_activity.washing_mop",
        "panel.live_activity.drying_mop", "panel.live_activity.emptying_bin",
        "panel.live_activity.charging", "panel.live_activity.returning_to_dock",
    }
    assert keys <= ru.keys()
    assert keys <= en.keys()
