"""Regression coverage for the compact 0.12.34 interface polish."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_named_zone_localization_and_frontend_compatibility_alias_remain():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend/localization/en.json").read_text(encoding="utf-8"))
    # Release/version identity is covered centrally in test_release_metadata.py.
    assert '"vacuum-schedule-panel-01243"' in panel
    assert ru["panel.live_activity.cleaning_zone_named"] == "Убираем зону {zone}"
    assert en["panel.live_activity.cleaning_zone_named"] == "Cleaning zone {zone}"


def test_live_zone_backend_exports_scheduler_attempt_scope():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    block = source[source.index("def _live_execution_status"):source.index("def _job_status_dict")]
    assert "for raw_zone_id in attempt.zone_ids:" in block
    assert 'zone_run.metadata.get("execution_attempt_id")' in block
    assert '"current_zone_ids": current_zone_ids' in block
    assert '"current_zone_names": current_zone_names' in block


def test_status_strip_wait_row_and_history_source_render_compactly():
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    keys = {
        k: ru[k]
        for k in (
            "panel.live_activity.cleaning_zone",
            "panel.live_activity.cleaning_zone_named",
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
            "panel.with_wait",
            "panel.completed",
            "panel.schedule",
            "panel.source",
            "panel.execution_mode",
            "panel.cleaning_profile_used",
            "panel.duration",
            "panel.result",
            "panel.reason",
        )
    }
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01235');
        const x=new C();
        x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._translations={json.dumps(keys, ensure_ascii=False)};x._fallbackTranslations=x._translations;
        x._resourceStatusText=()=> 'OK';x._formatDateTime=()=> '12:00';
        const entry={{
          robot_status:{{observation:{{vendor_status:'segment_cleaning'}},resources:{{}},water:{{}}}},
          active_jobs:[{{state:'RUNNING',live:{{current_zone_name:'Кухня'}}}}]
        }};
        const strip=x._robotStatusHtml(entry);
        if(!strip.includes('Убираем зону Кухня')) throw new Error('named zone missing: '+strip);
        if(strip.includes('>Убирает зону<')) throw new Error('generic zone label remained: '+strip);

        x._blockerCodes=(v)=>v;x._blockerLabels=()=>['Комната занята','Нет доступа'];
        const wait=x._nowHtml({{state:'WAIT',current_blockers:['zone_busy','path_blocked']}});
        if(!wait.includes('Комната занята · Нет доступа')) throw new Error(wait);
        if(wait.includes('Ожидает:')||wait.includes('Ожидание:')) throw new Error('wait prefix remained: '+wait);

        x._historyFilterMatches=()=>true;x._historySource=()=> 'SCHEDULED';x._historySourceLabel=()=> 'По расписанию';
        x._historyJobName=(j)=>j.schedule_name;x._historyTime=()=> '12:00';x._historyCleaningProfileText=()=> 'Пылесос';
        x._historyPhysicalDurationSeconds=()=>60;x._historyExecutionModeHtml=()=> 'Реальное выполнение';x._historyResultHtml=()=> 'Успешно';
        x._resultLabel=()=> 'Успешно';x._reasonLabel=()=> '—';x._executionModeLabel=()=> 'Реальное выполнение';
        const hist=x._historyHtml({{history:[{{job_id:'1',schedule_id:'s',schedule_name:'Кухня',waited_before_start:true,execution_mode:'REAL',result:'SUCCESS'}}]}});
        if(!hist.includes('С ожиданием')) throw new Error(hist);
        if(hist.includes('По расписанию · С ожиданием')) throw new Error('combined source remained: '+hist);
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_icon_only_buttons_use_exact_centering_box():
    panel = PANEL.read_text(encoding="utf-8")
    assert "button.icon-only { position:relative; display:inline-grid!important; place-items:center;" in panel
    assert 'const zoneCleaningActivity=new Set(["cleaning","cleaning_zone","vacuuming","mopping","vacuuming_and_mopping","spot_cleaning"]).has(activity);' in panel
    assert "button.icon-only>.button-icon { position:absolute; left:50%; top:50%;" in panel
    assert "transform:translate(-50%,-50%);" in panel
