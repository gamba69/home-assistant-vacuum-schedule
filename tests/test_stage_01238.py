"""Regression coverage for live Cleaning Zone scope in 0.12.38."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_backend_exports_complete_active_attempt_zone_scope():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    block = source[source.index("def _live_execution_status"):source.index("def _job_status_dict")]
    assert "for raw_zone_id in attempt.zone_ids:" in block
    assert 'zone_run.metadata.get("execution_attempt_id")' in block
    assert 'in {"STARTING", "RUNNING"}' in block
    assert '"current_zone_ids": current_zone_ids' in block
    assert '"current_zone_names": current_zone_names' in block
    assert "configured_zone_names" in block


def test_frontend_enrichment_refreshes_live_zone_names_from_current_configuration():
    source = (MODULE / "frontend.py").read_text(encoding="utf-8")
    block = source[source.index("async def _async_enrich_job_display_data"):source.index("async def _async_entry_editor_payload")]
    assert 'live.get("current_zone_ids")' in block
    assert 'zone_names.get(zone_id)' in block
    assert 'live["current_zone_names"] = live_zone_names' in block


def test_robot_status_names_single_and_merged_scheduler_zone_scopes():
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    keys = {
        key: ru[key]
        for key in (
            "panel.live_activity.cleaning_zone_named",
            "panel.live_activity.cleaning_zones_named",
            "panel.robot_short",
            "panel.battery",
            "panel.clean_water_short",
            "panel.dirty_water_short",
            "panel.unknown",
            "panel.ml_unit",
            "panel.robot_status",
            "panel.clean_water",
            "panel.dirty_water",
            "panel.detergent",
            "panel.mop",
            "panel.observed_at",
        )
    }
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01238');
        const x=new C();
        x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._translations={json.dumps(keys, ensure_ascii=False)};x._fallbackTranslations=x._translations;
        x._resourceStatusText=()=> 'OK';x._formatDateTime=()=> '12:00';

        const one={{
          robot_status:{{observation:{{vendor_status:'segment_cleaning'}},resources:{{}},water:{{}}}},
          active_jobs:[{{state:'RUNNING',live:{{current_zone_ids:['bedroom'],current_zone_names:['Спальня']}}}}]
        }};
        const oneHtml=x._robotStatusHtml(one);
        if(!oneHtml.includes('Убираем зону Спальня')) throw new Error('single zone missing: '+oneHtml);
        if(oneHtml.includes('>Убирает зону<')) throw new Error('generic single zone remained: '+oneHtml);

        const merged={{
          robot_status:{{observation:{{vendor_status:'sweep_and_mop'}},resources:{{}},water:{{}}}},
          active_jobs:[{{state:'RUNNING',live:{{current_zone_ids:['k','h'],current_zone_names:['Кухня','Коридор']}}}}]
        }};
        const mergedHtml=x._robotStatusHtml(merged);
        if(!mergedHtml.includes('Убираем зоны Кухня · Коридор')) throw new Error('merged zones missing: '+mergedHtml);

        const recovered={{
          robot_status:{{observation:{{vendor_status:'segment_cleaning'}},resources:{{}},water:{{}}}},
          active_jobs:[{{state:'RUNNING',live:{{}},zone_runs:{{bedroom:{{zone_name:'Спальня'}}}},execution_attempts:{{a:{{state:'RUNNING',zone_ids:['bedroom']}}}}}}]
        }};
        const recoveredHtml=x._robotStatusHtml(recovered);
        if(!recoveredHtml.includes('Убираем зону Спальня')) throw new Error('attempt fallback missing: '+recoveredHtml);
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
