"""Regression tests for notification manager imports translate when runtime payload uses it and every translate caller imports or defines helper.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANAGER = MODULE / "notification_manager.py"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_notification_manager_imports_translate_when_runtime_payload_uses_it():
    source = MANAGER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    uses_translate = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "translate" for node in ast.walk(tree))
    imported_translate = any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "translate" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert uses_translate
    assert imported_translate
    assert 'translate("notification.value.test_zone", language)' in source
    assert 'translate("notification.value.test_schedule", language)' in source


def test_every_translate_caller_imports_or_defines_helper():
    offenders = []
    for path in MODULE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        uses_translate = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "translate"
            for node in ast.walk(tree)
        )
        if not uses_translate:
            continue
        imported = any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "translate" for alias in node.names)
            for node in ast.walk(tree)
        )
        defined = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "translate"
            for node in ast.walk(tree)
        )
        if not (imported or defined):
            offenders.append(path.name)
    assert offenders == []
