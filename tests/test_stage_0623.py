"""Regression tests for cleaning zone live states have semantic good warning neutral, cleaning zone busy and accessibility tones match semantics, and countdown recolors to green when stabilization finishes.

Covers retained behavioral contracts from earlier development stages.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_cleaning_zone_live_states_have_semantic_good_warning_neutral_styles():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        '.quiet-status { display:inline-flex; align-items:center; gap:7px;',
        '.quiet-status-good .quiet-status-dot { background:var(--success-color,var(--vs-success-quiet-on)); }',
        '.quiet-status-warning .quiet-status-dot { background:var(--warning-color,var(--vs-warning-quiet-on)); }',
        '.quiet-status-bad .quiet-status-dot { background:var(--error-color,var(--vs-danger-quiet-on)); }',
        '.zone-live-neutral { color:var(--secondary-text-color); }',
        'class="quiet-status quiet-status-${tone} zone-live-state zone-live-${tone}"',
    ):
        assert token in panel


def test_cleaning_zone_busy_and_accessibility_tones_match_semantics():
    panel_path = PANEL
    script = f"""
const fs = require('fs');
const registry = Object.create(null);
global.HTMLElement = class {{ attachShadow() {{ return {{ querySelector() {{ return null; }}, querySelectorAll() {{ return []; }}, innerHTML: '' }}; }} }};
global.customElements = {{ get(name) {{ return registry[name]; }}, define(name, cls) {{ registry[name] = cls; }} }};
global.navigator = {{ language: 'ru' }};
global.window = {{ localStorage: {{ getItem() {{ return null; }}, setItem() {{}} }}, addEventListener() {{}}, removeEventListener() {{}} }};
global.document = {{ addEventListener() {{}}, removeEventListener() {{}}, visibilityState: 'visible' }};
eval(fs.readFileSync({json.dumps(str(panel_path))}, 'utf8'));
const Panel = registry['vacuum-schedule-panel-0635'];
const p = new Panel();
p._translations = JSON.parse(fs.readFileSync({json.dumps(str(MODULE / 'frontend' / 'localization' / 'ru.json'))}, 'utf8'));
p._fallbackTranslations = p._translations;
p._hass = {{ language: 'ru' }};
p._remainingSeconds = () => 0;
const cases = [
  [{{busy_sources:[], access_paths:[], live:{{}}}}, 'busy', 'zone-live-neutral', 'Не настроено'],
  [{{busy_sources:[{{}}], live:{{busy:{{effective:true}}}}}}, 'busy', 'zone-live-warning', 'сейчас занята'],
  [{{busy_sources:[{{}}], live:{{busy:{{effective:false}}}}}}, 'busy', 'zone-live-good', 'сейчас не занята'],
  [{{access_paths:[{{}}], live:{{accessible:{{effective:true}}}}}}, 'access', 'zone-live-good', 'сейчас доступна'],
  [{{access_paths:[{{}}], live:{{accessible:{{effective:false}}}}}}, 'access', 'zone-live-warning', 'сейчас недоступна'],
];
for (const [zone, kind, cls, text] of cases) {{
  const html = p._zoneStatusCell(zone, kind);
  if (!html.includes(cls) || !html.includes(text)) throw new Error(`${{kind}}: ${{html}}`);
}}
p._remainingSeconds = () => 11;
const busyCountdown = p._zoneStatusCell({{busy_sources:[{{}}], live:{{busy:{{effective:false,stabilizing:true,stabilizing_until:'future'}}}}}}, 'busy');
if (!busyCountdown.includes('zone-live-warning') || !busyCountdown.includes('Занята (11 с)')) throw new Error(busyCountdown);
const accessCountdown = p._zoneStatusCell({{access_paths:[{{}}], live:{{accessible:{{effective:true,stabilizing:true,stabilizing_until:'future'}}}}}}, 'access');
if (!accessCountdown.includes('zone-live-warning') || !accessCountdown.includes('Недоступна (11 с)')) throw new Error(accessCountdown);
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_countdown_recolors_to_green_when_stabilization_finishes():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'el.classList.toggle("zone-live-warning", remaining > 0);' in panel
    assert 'el.classList.toggle("zone-live-good", remaining <= 0);' in panel
