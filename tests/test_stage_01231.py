"""Roborock mop-drying entity migration coverage for 0.12.31."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = ROOT / "custom_components" / "vacuum_schedule" / "execution_observer.py"


def _literal_assignment(source: str, name: str):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def test_new_mop_drying_switch_is_preferred_with_legacy_sensor_fallback():
    source = OBSERVER.read_text(encoding="utf-8")
    aliases = _literal_assignment(source, "_DIAGNOSTIC_ALIASES")
    priorities = _literal_assignment(source, "_DIAGNOSTIC_ALIAS_PRIORITY")

    assert "mop_drying" in aliases["dry_status"]
    assert "mop_drying_status" in aliases["dry_status"]
    assert priorities["mop_drying"] > priorities["mop_drying_status"]
    assert "100 + _DIAGNOSTIC_ALIAS_PRIORITY.get(translation_key, 0)" in source


def test_mop_drying_remains_outside_execution_lease():
    source = OBSERVER.read_text(encoding="utf-8")
    service = source[source.index("def _dock_service_activity"):source.index("@dataclass", source.index("def _dock_service_activity"))]

    assert "Drying remains intentionally absent" in service
    assert "dry_status" not in service

