"""Operational robot strip and effective cleaning profile contracts."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_scheduler_status_exposes_physical_robot_and_water_state():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'def _robot_status_payload(self, now: datetime)' in engine
    assert '"robot_status": self._robot_status_payload(now)' in engine
    assert 'self.execution.real.status_observation(now)' in engine
    assert 'self.statistics.water.payload()' in engine
    assert '"dock.clean_water"' in engine
    assert '"dock.dirty_water"' in engine
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    assert 'def status_observation(self, now: datetime)' in backend
    assert 'return self._observe(now).to_dict()' in backend


def test_status_page_has_expandable_robot_strip_before_active_jobs():
    panel = PANEL.read_text(encoding="utf-8")
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy", panel.index("  _statusHtml() {"))]
    assert '${this._robotStatusHtml(entry)}' in status
    assert status.index('${this._robotStatusHtml(entry)}') < status.index('<section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.active_jobs")}')
    assert 'class="robot-status-strip" data-live-robot-status' in panel
    assert 'panel.clean_water_short' in panel
    assert 'panel.dirty_water_short' in panel
    assert 'panel.battery' in panel


def test_active_jobs_show_effective_cleaning_mode_after_now_column():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[panel.index("  _activeJobsHtml(entry) {"):panel.index("  _executionModeLabel(mode)")]
    assert '<span>${this._tr("panel.now_short")}</span>' in block
    assert '<span>${this._tr("panel.cleaning")}</span>' in block
    assert block.index('panel.now_short') < block.index('panel.cleaning') < block.index('panel.planned')
    assert '${this._activeJobModeHtml(job)}' in block
    mode_fn = panel[panel.index("  _activeJobModeHtml(job) {"):panel.index("  _readinessHtml(job) {")]
    assert 'job?.cleaning_params||{}' in mode_fn
    assert '_compactCleaningProfileText' in mode_fn


def test_robot_strip_localization_complete():
    required = {
        "panel.robot_short", "panel.clean_water_short", "panel.dirty_water_short",
        "panel.service_required_short", "panel.detergent", "panel.mop", "panel.observed_at",
        "panel.live_activity.on_dock", "panel.live_activity.idle",
        "panel.live_activity.unavailable", "panel.live_activity.unknown",
    }
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert required <= data.keys()


def test_live_status_timer_refreshes_backend_observation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'setInterval(() => this._refreshActiveLiveStatus(), 10000)' in panel
    assert 'type:"vacuum_schedule/scheduler/status"' in panel
    assert 'this._updateRobotStatusStrip();' in panel


def test_robot_strip_uses_binary_water_fallback_when_synthetic_state_is_unknown():
    import subprocess, textwrap
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}},querySelector(){{return null}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});
        require({str(PANEL)!r});
        const key=[...customElements._m.keys()].find(k=>k==='vacuum-schedule-panel-01214'); const C=customElements.get(key); const x=new C();
        const fs=require('fs'); x._hass={{language:'ru',config:{{time_zone:'UTC'}}}};
        x._translations=JSON.parse(fs.readFileSync({str(MODULE / 'frontend/localization/ru.json')!r},'utf8')); x._fallbackTranslations=x._translations;
        const html=x._robotStatusHtml({{robot_status:{{observation:{{vendor_status:'docked',normalized_state:'docked',phase:'IDLE',battery_percent:null}},resources:{{'dock.clean_water':{{effective:true,effective_available:true}},'dock.dirty_water':{{effective:false,effective_available:true}}}},water:{{clean:{{remaining_percent:null,estimate_ml_eq:null}},dirty:{{filled_percent:null,estimate_ml_eq:null}}}}}}}});
        if(html.includes('≈0%')) throw new Error(html);
        if(!html.includes('Норма')) throw new Error('clean fallback missing: '+html);
        if(!html.includes('Требует обслуживания')) throw new Error('dirty fallback missing: '+html);
        console.log('ok');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
