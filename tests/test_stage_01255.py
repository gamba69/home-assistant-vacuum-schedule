"""Regression coverage for full-charge dock wording in 0.12.55."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_full_charge_is_presented_as_on_base_not_charging():
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    keys = {
        key: ru[key]
        for key in (
            "panel.live_activity.charging",
            "panel.live_activity.on_dock",
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
        const C=customElements.get('vacuum-schedule-panel-01256');
        const x=new C();
        x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._translations={json.dumps(keys, ensure_ascii=False)};x._fallbackTranslations=x._translations;
        x._resourceStatusText=()=> 'OK';x._formatDateTime=()=> '12:00';

        const full=x._robotStatusHtml({{robot_status:{{observation:{{vendor_status:'charging',battery_percent:100}},resources:{{'vacuum.charging':{{effective:true}}}},water:{{}}}},active_jobs:[]}});
        if(!full.includes('На базе')) throw new Error('full charge should be on base: '+full);
        if(full.includes('>Заряжается<')) throw new Error('full charge still says charging: '+full);

        const charging=x._robotStatusHtml({{robot_status:{{observation:{{vendor_status:'charging',battery_percent:99}},resources:{{'vacuum.charging':{{effective:true}}}},water:{{}}}},active_jobs:[]}});
        if(!charging.includes('Заряжается')) throw new Error('active charge below 100% lost: '+charging);
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_on_dock_wording_is_localized_for_all_frontend_languages():
    expected={"ru":"На базе","uk":"На базі","en":"At base"}
    for lang,label in expected.items():
        data=json.loads((MODULE / f"frontend/localization/{lang}.json").read_text(encoding="utf-8"))
        assert data["panel.live_activity.on_dock"] == label
