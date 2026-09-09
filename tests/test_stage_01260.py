from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"
FRONTEND_PY = ROOT / "custom_components" / "vacuum_schedule" / "frontend.py"


def test_01260_remains_registered_as_legacy_alias() -> None:
    panel = PANEL.read_text(encoding="utf-8")
    assert '"vacuum-schedule-panel-01260"' in panel


def test_custom_element_registry_does_not_reuse_constructor() -> None:
    node = shutil.which("node")
    assert node, "node is required for frontend registry regression test"
    script = r'''
const fs=require('fs'), vm=require('vm');
class HTMLElement {}
const byName=new Map(), byCtor=new Set();
const customElements={
  get:n=>byName.get(n),
  define:(n,c)=>{
    if(byCtor.has(c)) throw new Error('NotSupportedError constructor reused: '+n);
    if(byName.has(n)) throw new Error('name reused: '+n);
    byName.set(n,c); byCtor.add(c);
  }
};
const ctx={HTMLElement,customElements,console,Map,Set,Date,Math,Number,String,Boolean,Array,Object,JSON,Promise,URLSearchParams,setTimeout,clearTimeout,setInterval,clearInterval,fetch:()=>Promise.reject(new Error('no fetch'))};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
if(!byName.get('vacuum-schedule-panel-01260')) throw new Error('current component missing');
if(!byName.get('vacuum-schedule-panel-01259')) throw new Error('legacy component missing');
console.log(byName.size);
'''
    result = subprocess.run(
        [node, "-e", script, str(PANEL)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
