"""Regression contracts for optional mains monitoring in 0.14.0."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(INTEGRATION))

from bindings import PreflightPolicy  # noqa: E402
from power_supply import normalize_power_supply  # noqa: E402


def test_legacy_configuration_keeps_power_monitor_disabled():
    policy = PreflightPolicy.from_dict({})
    assert not policy.power_monitor_enabled
    assert policy.power_entity_id is None
    assert policy.power_present_state == "on"
    assert PreflightPolicy.from_dict(policy.to_dict()) == policy


def test_explicit_mains_configuration_roundtrips():
    raw = {
        "power_monitor_enabled": True,
        "power_entity_id": "binary_sensor.grid_power",
        "power_present_state": "off",
    }
    policy = PreflightPolicy.from_dict(raw)
    assert policy.power_monitor_enabled is True
    assert policy.power_entity_id == "binary_sensor.grid_power"
    assert policy.power_present_state == "off"
    assert PreflightPolicy.from_dict(policy.to_dict()) == policy


def test_normalization_requires_positive_binary_evidence():
    assert normalize_power_supply("on", True, "on") is True
    assert normalize_power_supply("off", True, "on") is False
    assert normalize_power_supply("off", True, "off") is True
    assert normalize_power_supply("on", True, "off") is False
    for raw in ("unknown", "unavailable", "standby", "0", None):
        assert normalize_power_supply(raw, True, "on") is None
    assert normalize_power_supply("off", False, "on") is None
    assert normalize_power_supply("on", False, "on") is None
    assert normalize_power_supply("on", True, "maybe") is None


def test_legacy_sensor_inputs_cannot_infer_power_availability():
    # An unavailable source must not be treated as confirmed mains failure.
    assert normalize_power_supply(None, False) is None
    assert normalize_power_supply("unavailable", False) is None
    # A deliberately inverted sensor is supported without assuming a device class.
    assert normalize_power_supply("off", True, "off") is True


def test_user_facing_strings_exist_in_all_three_languages():
    keys = {
        "panel.power_monitor", "panel.power_monitor_enable", "panel.power_sensor",
        "panel.power_monitor_status", "panel.power_present", "panel.power_absent",
        "panel.power_unknown", "common.blocker.power_supply_unknown",
        "common.blocker.power_supply_unavailable", "common.reason.power_supply_lost",
    }
    for language in ("en", "ru", "uk"):
        catalog = json.loads((INTEGRATION / "frontend" / "localization" / f"{language}.json").read_text(encoding="utf-8"))
        assert all(catalog.get(key) for key in keys)
