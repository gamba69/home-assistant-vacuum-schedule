"""Checks the single canonical release identity and frontend cache boundary.

This replaces per-stage version assertions that did not test product behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
EXPECTED_VERSION = "0.13.2"
EXPECTED_COMPONENT = "vacuum-schedule-panel-0132"


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


def test_manifest_keys_follow_hassfest_order():
    manifest = json.loads(
        (MODULE / "manifest.json").read_text(encoding="utf-8"),
        object_pairs_hook=dict,
    )
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_generated_patch_files_are_ignored_and_absent_from_project_tree():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.patch" in gitignore
    assert not list(ROOT.glob("*.patch"))


def test_project_tests_workflow_covers_release_gate():
    workflow = (ROOT / ".github" / "workflows" / "tests.yaml").read_text(encoding="utf-8")
    for required in (
        "branches:",
        "- main",
        "tags:",
        '- "[0-9]+.[0-9]+.[0-9]+"',
        "pull_request:",
        "release:",
        "types: [published]",
        "workflow_dispatch:",
        "pytest -q",
        "python -m compileall -q custom_components tests",
        "node --check custom_components/vacuum_schedule/frontend/panel.js",
        "Validate JSON and YAML",
        "git ls-files '*.patch'",
    ):
        assert required in workflow
