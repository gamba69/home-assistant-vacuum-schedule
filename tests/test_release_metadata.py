"""Checks the single canonical release identity and frontend cache boundary.

This replaces per-stage version assertions that did not test product behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
EXPECTED_VERSION = "0.13.1"
EXPECTED_COMPONENT = "vacuum-schedule-panel-0131"


def test_release_metadata_is_consistent():
    manifest = json.loads((MODULE / "manifest.json").read_text(encoding="utf-8"))
    const = (MODULE / "const.py").read_text(encoding="utf-8")
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")

    assert manifest["version"] == EXPECTED_VERSION
    assert f'VERSION: Final = "{EXPECTED_VERSION}"' in const
    assert f'PANEL_COMPONENT = "{EXPECTED_COMPONENT}"' in frontend
    assert f'localization/${{lang}}.json?v={EXPECTED_VERSION}' in panel
    assert f'"{EXPECTED_COMPONENT}"' in panel
    assert 'const ElementClass = index === 0 ? VacuumSchedulePanel : class extends VacuumSchedulePanel {};' in panel


def test_previous_frontend_identity_remains_a_cache_compatibility_alias():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert '"vacuum-schedule-panel-01256"' in panel
    assert '"vacuum-schedule-panel-01255"' in panel
    assert '"vacuum-schedule-panel-01254"' in panel
    assert '"vacuum-schedule-panel-01239"' in panel
