"""Battery telemetry and Status layout contracts for Vacuum Schedule 0.10.0."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
sys.path.insert(0, str(ROOT))

# Keep imports runnable in the isolated source tree without a full HA install.
pkg = sys.modules.setdefault(
    "custom_components.vacuum_schedule", ModuleType("custom_components.vacuum_schedule")
)
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

from custom_components.vacuum_schedule.execution_backend import RealExecutionBackend  # noqa: E402
from custom_components.vacuum_schedule.execution_observer import (  # noqa: E402
    RobotExecutionObservation,
    RobotExecutionPhase,
)
from custom_components.vacuum_schedule.models import NormalizedVacuumState  # noqa: E402
from custom_components.vacuum_schedule.statistics_models import attempt_metrics  # noqa: E402


def _observation(at: datetime) -> RobotExecutionObservation:
    return RobotExecutionObservation(
        observed_at=at,
        normalized_state=NormalizedVacuumState.CLEANING,
        phase=RobotExecutionPhase.CLEANING,
        vendor="roborock",
        vendor_status="segment_cleaning",
        session_active=True,
        battery_percent=None,
    )


class _BatteryInputs:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)
        self.calls: list[bool] = []

    def snapshot(self, _now, *, allow_test_overrides=True):
        self.calls.append(bool(allow_test_overrides))
        value = self.values.pop(0)
        return SimpleNamespace(
            values={
                "vacuum.battery_percent": SimpleNamespace(
                    effective_available=True,
                    effective_value=value,
                    source_entity_id="sensor.roborock_battery",
                )
            }
        )

    def auto_candidate_info(self, _key):
        return {"available": False}




def test_real_observation_uses_same_dedicated_battery_source_as_preflight():
    now = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    backend = object.__new__(RealExecutionBackend)
    backend.adapter = SimpleNamespace(observe=lambda _now: _observation(_now))
    inputs = _BatteryInputs([83])
    backend.input_provider = inputs

    observed = backend._observe(now)

    assert observed.battery_percent == 83
    assert observed.source_entities["battery"] == "sensor.roborock_battery"
    assert inputs.calls == [False]  # REAL statistics never use Dry-Run overrides.



def test_battery_statistics_still_collect_when_preflight_battery_gate_is_disabled():
    now = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    backend = object.__new__(RealExecutionBackend)
    backend.adapter = SimpleNamespace(observe=lambda _now: _observation(_now))

    class Inputs:
        def snapshot(self, _now, *, allow_test_overrides=True):
            assert allow_test_overrides is False
            return SimpleNamespace(values={
                "vacuum.battery_percent": SimpleNamespace(
                    effective_available=False, effective_value=None, source_entity_id=None
                )
            })

        def auto_candidate_info(self, key):
            assert key == "vacuum.battery_percent"
            return {
                "available": True,
                "raw": "64",
                "entity_id": "sensor.roborock_battery",
            }

    backend.input_provider = Inputs()
    observed = backend._observe(now)
    assert observed.battery_percent == 64
    assert observed.source_entities["battery"] == "sensor.roborock_battery"

def test_battery_start_end_and_consumption_survive_in_attempt_statistics():
    now = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    current_time = [now]
    backend = object.__new__(RealExecutionBackend)
    backend.adapter = SimpleNamespace(observe=lambda _now: _observation(_now))
    inputs = _BatteryInputs([83, 72])
    backend.input_provider = inputs
    attempt = SimpleNamespace(metadata={})

    first = backend._observe(current_time[0])
    RealExecutionBackend._record_statistics_observation(attempt, first)
    current_time[0] += timedelta(minutes=25)
    second = backend._observe(current_time[0])
    RealExecutionBackend._record_statistics_observation(attempt, second)

    metrics = attempt_metrics({
        "requested_at": now.isoformat(),
        "start_confirmed_at": now.isoformat(),
        "completed_at": current_time[0].isoformat(),
        "metadata": attempt.metadata,
    })
    assert metrics["battery_start_percent"] == 83
    assert metrics["battery_end_percent"] == 72
    assert metrics["battery_drop_percent"] == 11


def test_status_maintenance_card_is_the_last_status_section():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _statusHtml()")
    end = panel.index("  _notificationPresetPolicy", start)
    block = panel[start:end]
    assert block.count("${this._maintenanceStatusCardHtml()}") == 1
    assert block.index("panel.active_jobs") < block.index("${this._maintenanceStatusCardHtml()}")
    assert block.index("panel.recent_jobs") < block.index("${this._maintenanceStatusCardHtml()}")
    assert block.rstrip().endswith('${this._maintenanceStatusCardHtml()}`;\n  }')


def test_battery_fix_is_measurement_only_not_scheduler_policy():
    backend = (MODULE / "execution_backend.py").read_text(encoding="utf-8")
    assert 'snapshot(now, allow_test_overrides=False)' in backend
    assert 'values.get("vacuum.battery_percent")' in backend
    assert 'replace(observation, battery_percent=battery' in backend
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    assert "StatisticsManager" not in preflight
