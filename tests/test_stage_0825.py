"""Regression and behavioral contracts for Vacuum Schedule 0.9.0 sound suppression."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(ROOT))

# Keep this test runnable in the isolated source package used by the project.
pkg = sys.modules.setdefault("custom_components.vacuum_schedule", ModuleType("custom_components.vacuum_schedule"))
pkg.__path__ = [str(MODULE)]
ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
entity_component = sys.modules.setdefault(
    "homeassistant.helpers.entity_component", ModuleType("homeassistant.helpers.entity_component")
)
entity_component.DATA_INSTANCES = "entity_components"
entity_registry = sys.modules.setdefault(
    "homeassistant.helpers.entity_registry", ModuleType("homeassistant.helpers.entity_registry")
)
if not hasattr(entity_registry, "async_get"):
    entity_registry.async_get = lambda hass: None
if not hasattr(entity_registry, "async_entries_for_device"):
    entity_registry.async_entries_for_device = lambda *args, **kwargs: ()
setattr(helpers, "entity_registry", entity_registry)

from custom_components.vacuum_schedule.execution_adapter import (  # noqa: E402
    PhysicalExecutionError,
    VacuumExecutionAdapter,
)


class _States:
    def __init__(self, entity_id: str, value: float) -> None:
        self.entity_id = entity_id
        self.state = SimpleNamespace(state=str(value), attributes={})

    def get(self, entity_id: str):
        return self.state if entity_id == self.entity_id else None


def _sound_adapter(initial_volume: float):
    adapter = object.__new__(VacuumExecutionAdapter)
    volume_entity = "number.robot_volume"
    states = _States(volume_entity, initial_volume)
    adapter.hass = SimpleNamespace(states=states)
    adapter.vendor = "roborock"
    adapter.executor = SimpleNamespace(vacuum_entity_id="vacuum.robot")
    adapter._persist_callback = None
    adapter._roborock_volume_entity_id = lambda: volume_entity
    events: list[tuple] = []

    async def persist():
        events.append(("persist",))

    async def set_volume(entity_id: str, value: float):
        events.append(("volume", float(value)))
        states.state.state = str(float(value))

    adapter._async_persist_sound_barrier = persist
    adapter._async_set_volume_number = set_volume
    return adapter, states, events




def test_roborock_volume_discovery_uses_public_number_entity_metadata():
    source = (MODULE / "execution_adapter.py").read_text(encoding="utf-8")
    assert 'translation_key == "volume"' in source
    assert 'entity_id.startswith("number.")' in source
    assert 'getattr(entry, "platform", "")' in source
    assert '"number",\n            "set_value"' in source
    assert 'include_disabled_entities=False' in source


def test_prepare_and_restore_are_both_wrapped_in_sound_suppression():
    source = (MODULE / "execution_adapter.py").read_text(encoding="utf-8")
    assert 'await self._async_with_parameter_sound_suppressed(attempt, "prepare", _apply)' in source
    assert 'await self._async_with_parameter_sound_suppressed(attempt, "restore", _restore)' in source
    assert '"pending_volume_restore"' in source
    assert 'await self._async_persist_sound_barrier()' in source


def test_parameter_transaction_mutes_before_write_and_restores_exact_previous_volume():
    adapter, states, events = _sound_adapter(37)
    attempt = SimpleNamespace(metadata={})

    async def parameter_write():
        events.append(("parameter_write", float(states.state.state)))

    asyncio.run(
        adapter._async_with_parameter_sound_suppressed(attempt, "prepare", parameter_write)
    )

    assert events == [
        ("persist",),
        ("volume", 0.0),
        ("parameter_write", 0.0),
        ("volume", 37.0),
    ]
    assert float(states.state.state) == 37.0
    assert "pending_volume_restore" not in attempt.metadata
    record = attempt.metadata["parameter_sound_suppression"][0]
    assert record["volume_before"] == 37.0
    assert record["muted"] is True
    assert record["restored"] is True


def test_parameter_failure_still_restores_volume_in_finally():
    adapter, states, events = _sound_adapter(63)
    attempt = SimpleNamespace(metadata={})

    async def failing_write():
        events.append(("parameter_write", float(states.state.state)))
        raise RuntimeError("parameter failed")

    try:
        asyncio.run(
            adapter._async_with_parameter_sound_suppressed(attempt, "restore", failing_write)
        )
    except RuntimeError as err:
        assert str(err) == "parameter failed"
    else:
        raise AssertionError("parameter error must be preserved")

    assert events[-1] == ("volume", 63.0)
    assert float(states.state.state) == 63.0
    assert "pending_volume_restore" not in attempt.metadata


def test_failed_mute_prevents_loud_parameter_write():
    adapter, states, events = _sound_adapter(55)
    attempt = SimpleNamespace(metadata={})
    action_called = False

    async def fail_mute(entity_id: str, value: float):
        events.append(("volume", float(value)))
        raise RuntimeError("mute failed")

    async def parameter_write():
        nonlocal action_called
        action_called = True

    adapter._async_set_volume_number = fail_mute
    try:
        asyncio.run(
            adapter._async_with_parameter_sound_suppressed(attempt, "prepare", parameter_write)
        )
    except PhysicalExecutionError as err:
        assert err.code == "execution_volume_mute_failed"
    else:
        raise AssertionError("mute failure must stop parameter writes")

    assert action_called is False
    assert float(states.state.state) == 55.0
    assert "pending_volume_restore" not in attempt.metadata


def test_volume_recovery_is_independent_of_restore_previous_settings():
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    manager = (MODULE / "execution_manager.py").read_text(encoding="utf-8")
    assert 'await self.adapter.async_restore_pending_volume(attempt)' in backend
    assert 'Speaker muting is an operational guard, not a user restore-setting.' in backend
    assert 'attempt.metadata.get("pending_volume_restore")' in manager
    assert 'await self.real.adapter.async_restore_pending_volume(attempt)' in manager
    assert 'await self.real.adapter.async_restore_parameters(attempt)' in manager


