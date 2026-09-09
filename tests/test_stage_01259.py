"""0.12.59 quiet status dots and plain schedule-zone labels regression."""
from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_recent_jobs_use_quiet_status_dots_instead_of_colored_text():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'return this._quietStatusHtml(this._executionModeLabel(normalized),tone,"history-execution-mode")' in panel
    assert 'return this._quietStatusHtml(this._resultLabel(result),tone,"history-result")' in panel
    assert '.quiet-status-good .quiet-status-dot' in panel
    assert '.quiet-status-warning .quiet-status-dot' in panel
    assert '.quiet-status-bad .quiet-status-dot' in panel
    assert '.history-execution-mode-real,.history-result-success' not in panel
    assert '.history-result-failed' not in panel


def test_settings_summary_and_zone_live_states_use_quiet_dots():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._quietStatusHtml(this._supportLabel(row.support_status),this._bindingSupportTone(row.support_status),"binding-support-state")' in panel
    assert 'return this._quietStatusHtml(value,tone,"resource-state")' in panel
    assert 'class="quiet-status quiet-status-${tone} zone-live-state zone-live-${tone}"' in panel
    assert 'el.querySelector(".quiet-status-label")' in panel
    assert '.binding-support-detected,.binding-support-manual{color:' not in panel
    assert '.resource-state-good{color:' not in panel
    assert '.zone-live-good { color:' not in panel


def test_schedule_zone_labels_are_plain_colored_text_not_badges():
    panel = PANEL.read_text(encoding="utf-8")
    schedule_cell = panel.split('data-label="${this._tr("panel.targets")}"', 1)[1].split('</td>', 1)[0]
    assert 'schedule-target-text' in schedule_cell
    assert 'ui-chip target-chip' not in schedule_cell
    assert '.schedule-target-text { color:var(--vs-brand-quiet-on);' in panel
    css = panel.split('.schedule-target-text {', 1)[1].split('}', 1)[0]
    assert 'background' not in css
    assert 'border-radius' not in css
    assert 'padding' not in css


def test_quiet_status_html_is_neutral_text_with_only_dot_toned():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01259');
        const x=new C();
        x._tr=(k)=>({{'panel.execution_real':'Реальное выполнение','common.job_result.SUCCESS':'Успешно'}}[k]||k);
        const mode=x._historyExecutionModeHtml('REAL');
        const result=x._historyResultHtml('SUCCESS');
        for(const html of [mode,result]){{
          if(!html.includes('quiet-status-dot')) throw new Error(html);
          if(!html.includes('quiet-status-good')) throw new Error(html);
          if(html.includes('status-badge')) throw new Error(html);
        }}
        if(!mode.includes('Реальное выполнение')) throw new Error(mode);
        if(!result.includes('Успешно')) throw new Error(result);
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_previous_frontend_identity_is_preserved():
    panel = PANEL.read_text(encoding="utf-8")
    assert '\"vacuum-schedule-panel-01259\"' in panel
    assert '\"vacuum-schedule-panel-01258\"' in panel
