"""Regression contracts for Vacuum Schedule 0.8.3 frontend bootstrap fix."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_registered_panel_webcomponent_is_defined_by_panel_module():
    frontend=(MODULE / "frontend.py").read_text()
    panel=(MODULE / "frontend" / "panel.js").read_text()
    match=re.search(r'PANEL_COMPONENT\s*=\s*"([^"]+)"', frontend)
    assert match, "PANEL_COMPONENT must be declared"
    component=match.group(1)
    assert f'"{component}"' in panel
    assert 'customElements.define(name, ElementClass);' in panel


def test_previous_079_component_remains_as_cache_compatibility_alias():
    panel=(MODULE / "frontend" / "panel.js").read_text()
    assert '"vacuum-schedule-panel-079"' in panel
