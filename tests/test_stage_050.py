"""Regression tests for cleaning zone has exactly one segment target, cleaning zone can use robot zone target, and zone requires physical target.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import subprocess
import json
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE_DIR))

PANEL_EN = json.loads((MODULE_DIR / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
PANEL_RU = json.loads((MODULE_DIR / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))

def _panel_key(ru: str, en: str | None = None) -> str:
    for key, value in PANEL_RU.items():
        if value == ru and (en is None or PANEL_EN.get(key) == en):
            return key
    raise AssertionError(f"Missing localization pair: {ru!r} / {en!r}")

from bindings import (  # noqa: E402
    BindingMode,
    CapabilityBinding,
    ExecutionGateMode,
    PreflightPolicy,
    bindings_from_options,
)
from cleaning_zones import (  # noqa: E402
    AccessPath,
    CleaningZone,
    CleaningZoneControl,
    EntityCondition,
    RobotTargetType,
    aggregate_access_paths,
    cleaning_zones_from_options,
    condition_matches,
    effective_zone_delay,
)
from input_overrides import OverrideMode, TestOverrideManager  # noqa: E402
from job import (  # noqa: E402
    JobInstance,
    JobResult,
    ZoneExecution,
    ZoneJobState,
    ZoneResult,
)
from planner import OccurrencePlanner  # noqa: E402
from preflight import PreflightEngine, SimulationPreflightProvider  # noqa: E402
from preflight_models import PreflightDecision, PreflightPhase  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


@dataclass
class FakeInput:
    key: str
    effective_value: object
    effective_available: bool = True
    live_value: object = None
    live_available: bool = True
    source_entity_id: str | None = None
    source_attribute: str | None = None
    raw_value: object = None
    status: str = "ready"
    note: str | None = None
    stabilizing: bool = False
    stabilizing_until: datetime | None = None
    configured_delay_seconds: int = 0

    def to_dict(self):
        return {
            "key": self.key,
            "effective": self.effective_value,
            "effective_available": self.effective_available,
            "live": self.live_value,
            "live_available": self.live_available,
            "source_entity_id": self.source_entity_id,
            "source_attribute": self.source_attribute,
            "raw_value": self.raw_value,
            "status": self.status,
            "note": self.note,
            "stabilizing": self.stabilizing,
            "stabilizing_until": self.stabilizing_until.isoformat() if self.stabilizing_until else None,
            "configured_delay_seconds": self.configured_delay_seconds,
        }


@dataclass
class FakeZoneSnapshot:
    zone_id: str
    name: str
    robot_target_type: str
    robot_target_id: str
    busy: FakeInput
    accessible: FakeInput
    control: str = "enabled"
    disabled_until: str | None = None
    path_details: tuple[dict, ...] = ()
    source_details: tuple[dict, ...] = ()

    @property
    def dependencies(self):
        values = {
            item.get("entity_id")
            for item in (*self.path_details, *self.source_details)
            if item.get("entity_id")
        }
        return tuple(sorted(values))

    def to_dict(self):
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "robot_target_type": self.robot_target_type,
            "robot_target_id": self.robot_target_id,
            "control": self.control,
            "disabled_until": self.disabled_until,
            "busy": self.busy.to_dict(),
            "accessible": self.accessible.to_dict(),
            "path_details": list(self.path_details),
            "source_details": list(self.source_details),
            "dependencies": list(self.dependencies),
        }


class FakeSnapshot:
    def __init__(self, now: datetime, values: dict[str, FakeInput], zones=None):
        self.evaluated_at = now
        self.values = values
        self.zones = zones or {}
        self.snapshot_id = "fake-snapshot"

    def to_dict(self):
        return {
            "snapshot_id": self.snapshot_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "values": {k: v.to_dict() for k, v in self.values.items()},
            "zones": {k: v.to_dict() for k, v in self.zones.items()},
        }


class FakeProvider:
    def __init__(self, now: datetime, policy: PreflightPolicy | None = None):
        self.now = now
        self.policy = policy or PreflightPolicy()
        self.bindings = bindings_from_options({})
        self.cleaning_zones = [
            CleaningZone(
                zone_id="kitchen",
                name="Kitchen",
                robot_target_type=RobotTargetType.SEGMENT,
                robot_target_id="16",
            ),
            CleaningZone(
                zone_id="hall",
                name="Hall",
                robot_target_type=RobotTargetType.SEGMENT,
                robot_target_id="17",
            ),
        ]
        self.hass = SimpleNamespace(states={})
        self.values = {
            "vacuum.available": FakeInput("vacuum.available", True, source_entity_id="vacuum.robot", raw_value="idle", live_value=True),
            "vacuum.activity": FakeInput("vacuum.activity", "idle", source_entity_id="vacuum.robot", raw_value="idle", live_value="idle"),
            "vacuum.battery_percent": FakeInput("vacuum.battery_percent", 80, source_entity_id="sensor.battery", raw_value=80, live_value=80),
            "vacuum.charging": FakeInput("vacuum.charging", False, source_entity_id="binary_sensor.charging", raw_value=False, live_value=False),
            "dock.available": FakeInput("dock.available", True, effective_available=False, live_available=False),
            "dock.robot_docked": FakeInput("dock.robot_docked", False, effective_available=False, live_available=False),
            "dock.clean_water": FakeInput("dock.clean_water", None, effective_available=False, live_available=False),
            "dock.dirty_water": FakeInput("dock.dirty_water", None, effective_available=False, live_available=False),
            "dock.detergent": FakeInput("dock.detergent", None, effective_available=False, live_available=False),
            "mop.attached": FakeInput("mop.attached", None, effective_available=False, live_available=False),
            "dnd.active": FakeInput("dnd.active", False, effective_available=True, live_available=True),
            "dnd.starts_at": FakeInput("dnd.starts_at", "22:00:00", effective_available=True, live_available=True),
            "dnd.ends_at": FakeInput("dnd.ends_at", "08:00:00", effective_available=True, live_available=True),
        }
        self.zone_snapshots = {
            "kitchen": self._zone_snapshot("kitchen", "Kitchen", "16"),
            "hall": self._zone_snapshot("hall", "Hall", "17"),
        }
        self.invalid_targets: set[str] = set()

    @staticmethod
    def _zone_snapshot(zone_id, name, target_id):
        return FakeZoneSnapshot(
            zone_id=zone_id,
            name=name,
            robot_target_type="segment",
            robot_target_id=target_id,
            busy=FakeInput(f"zone.{zone_id}.busy", False, live_value=False),
            accessible=FakeInput(f"zone.{zone_id}.accessible", True, live_value=True),
        )

    def snapshot(self, now=None):
        return FakeSnapshot(now or self.now, self.values, self.zone_snapshots)

    def target_configuration_valid(self, zone: CleaningZone):
        if zone.zone_id in self.invalid_targets:
            return False, "segment_not_found"
        return True, None

    def _resolve_registry_entity(self, entity_id, registry_id):
        return entity_id, False

    def _resolve_condition_entity(self, condition):
        return condition.entity_id, False


class Stage050CleaningZoneModelTests(unittest.TestCase):
    def test_cleaning_zone_has_exactly_one_segment_target(self):
        zone = CleaningZone.from_dict({
            "zone_id": "kitchen", "name": "Kitchen",
            "robot_target_type": "segment", "robot_target_id": "16",
        })
        self.assertEqual(zone.target_key, ("segment", "16"))
        self.assertEqual(zone.robot_target_type, RobotTargetType.SEGMENT)

    def test_cleaning_zone_can_use_robot_zone_target(self):
        zone = CleaningZone.from_dict({
            "zone_id": "under_table", "name": "Under table",
            "robot_target_type": "zone", "robot_target_id": "[100,200,300,400]",
        })
        self.assertEqual(zone.robot_target_type, RobotTargetType.ZONE)
        self.assertEqual(zone.robot_target_id, "[100,200,300,400]")

    def test_zone_requires_physical_target(self):
        with self.assertRaisesRegex(ValueError, "missing_robot_target"):
            CleaningZone.from_dict({"zone_id": "x", "name": "X", "robot_target_type": "segment"})

    def test_disabled_until_is_time_bounded(self):
        now = datetime(2026, 8, 12, 15, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        zone = CleaningZone.from_dict({
            "zone_id": "x", "name": "X", "robot_target_type": "segment", "robot_target_id": "1",
            "control": "disabled_until", "disabled_until": (now + timedelta(hours=1)).isoformat(),
        })
        self.assertTrue(zone.disabled_at(now))
        self.assertFalse(zone.disabled_at(now + timedelta(hours=2)))

    def test_busy_and_paths_belong_to_whole_zone(self):
        zone = CleaningZone.from_dict({
            "zone_id": "kitchen", "name": "Kitchen", "robot_target_type": "segment", "robot_target_id": "16",
            "busy_sources": [{"entity_id": "binary_sensor.kitchen_presence", "value": "on"}],
            "access_paths": [
                {"name": "Hall", "conditions": [
                    {"entity_id": "binary_sensor.door_a", "value": "on"},
                    {"entity_id": "binary_sensor.gate", "value": "on"},
                ]},
                {"name": "Living", "conditions": [{"entity_id": "binary_sensor.door_b", "value": "on"}]},
            ],
        })
        self.assertEqual(len(zone.busy_sources), 1)
        self.assertEqual(len(zone.access_paths), 2)
        self.assertEqual(set(zone.dependency_entity_ids), {
            "binary_sensor.kitchen_presence", "binary_sensor.door_a", "binary_sensor.gate", "binary_sensor.door_b"
        })

    def test_zone_delay_overrides_roundtrip_and_resolve_global_defaults(self):
        zone = CleaningZone.from_dict({
            "zone_id": "kitchen", "name": "Kitchen",
            "robot_target_type": "segment", "robot_target_id": "16",
            "occupancy_clear_delay_seconds": 25,
            "access_stable_delay_seconds": "12",
        })
        self.assertEqual(zone.occupancy_clear_delay_seconds, 25)
        self.assertEqual(zone.access_stable_delay_seconds, 12)
        self.assertEqual(zone.to_dict()["occupancy_clear_delay_seconds"], 25)
        self.assertEqual(effective_zone_delay(None, 30), 30)
        self.assertEqual(effective_zone_delay(5, 30), 5)

    def test_access_path_and_or_semantics(self):
        self.assertEqual(aggregate_access_paths([(True, True), (False, False)]), (True, True))
        self.assertEqual(aggregate_access_paths([(False, True), (False, False)]), (False, False))
        self.assertEqual(aggregate_access_paths([(False, True), (False, True)]), (False, True))
        self.assertEqual(aggregate_access_paths([]), (True, True))

    def test_condition_registry_id_and_operators(self):
        cond = EntityCondition.from_dict({"entity_id":"binary_sensor.old","entity_registry_id":"reg-1","value":"on"})
        self.assertEqual(cond.to_dict()["entity_registry_id"], "reg-1")
        self.assertTrue(condition_matches("on", cond))
        self.assertTrue(condition_matches("12", EntityCondition.from_dict({"entity_id":"x","operator":"above","value":10})))
        self.assertTrue(condition_matches("open", EntityCondition.from_dict({"entity_id":"x","operator":"in","value":"open,ajar"})))

    def test_duplicate_targets_are_not_silently_dropped(self):
        zones = cleaning_zones_from_options([
            {"zone_id":"a","name":"A","robot_target_type":"segment","robot_target_id":"16"},
            {"zone_id":"b","name":"B","robot_target_type":"segment","robot_target_id":"16"},
        ])
        self.assertEqual(len(zones), 2)
        self.assertEqual(zones[0].target_key, zones[1].target_key)



    def test_schedule_model_accepts_only_cleaning_zone_target_type(self):
        with self.assertRaises(Exception):
            ScheduleDefinition.create(
                name="Legacy", enabled=True, weekdays=[0], dates=[], local_time="10:00",
                target_type="segments", targets=["16"],
            )

class Stage050BindingAndOverrideTests(unittest.TestCase):
    def test_binary_resource_normal_state_roundtrips(self):
        binding = CapabilityBinding.from_dict(
            "dock.detergent", {"binding_mode": "auto", "normal_state": "off"}
        )
        self.assertEqual(binding.normal_state, "off")
        self.assertEqual(binding.to_dict()["normal_state"], "off")

    def test_zone_stability_global_defaults_roundtrip(self):
        policy = PreflightPolicy.from_dict({
            "occupancy_clear_delay_seconds": 45,
            "access_stable_delay_seconds": "20",
        })
        self.assertEqual(policy.occupancy_clear_delay_seconds, 45)
        self.assertEqual(policy.access_stable_delay_seconds, 20)
        self.assertEqual(policy.to_dict()["occupancy_clear_delay_seconds"], 45)
        self.assertEqual(policy.to_dict()["access_stable_delay_seconds"], 20)

    def test_manual_binding_wins_disabled_and_registry_id_roundtrip(self):
        manual = CapabilityBinding.from_dict("dock.clean_water", {"binding_mode":"manual","entity_id":"sensor.water","entity_registry_id":"reg-123"})
        disabled = CapabilityBinding.from_dict("dnd.active", {"binding_mode":"disabled"})
        self.assertEqual(manual.binding_mode, BindingMode.MANUAL)
        self.assertEqual(manual.entity_registry_id, "reg-123")
        self.assertEqual(CapabilityBinding.from_dict("dock.clean_water", manual.to_dict()).entity_registry_id, "reg-123")
        self.assertEqual(disabled.binding_mode, BindingMode.DISABLED)

    def test_live_freeze_force_unavailable_and_persistence(self):
        manager = TestOverrideManager({})
        self.assertEqual(manager.apply("x", 10), (10, True, None))
        manager.set("x", OverrideMode.FREEZE, frozen_live_value=10)
        self.assertEqual(manager.apply("x", 99)[0], 10)
        manager.set("x", OverrideMode.FORCE, value=5, persistent=True)
        self.assertEqual(manager.apply("x", 99)[0], 5)
        self.assertIn("x", manager.persistent_dict())
        manager.set("x", OverrideMode.UNAVAILABLE)
        self.assertEqual(manager.apply("x", 99)[:2], (None, False))
        manager.set("x", OverrideMode.LIVE)
        self.assertIsNone(manager.get("x"))


class Stage050JobModelTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Europe/Kyiv")
        self.now = datetime(2026, 8, 12, 14, 0, tzinfo=self.tz)
        schedule = ScheduleDefinition.create(
            schedule_id="s", revision=1, name="Multi-zone", enabled=True,
            weekdays=[0,1,2,3,4,5,6], dates=[], local_time="15:00",
            targets=["kitchen", "hall"], execution_window_minutes=120,
        )
        occurrence = OccurrencePlanner(self.tz).next_for_schedule(schedule, self.now, inclusive=True)
        assert occurrence is not None
        self.job = JobInstance.from_occurrence(occurrence, self.now)

    def test_schedule_targets_are_cleaning_zone_ids(self):
        self.assertEqual(self.job.target_type, "cleaning_zones")
        self.assertEqual(set(self.job.zone_runs), {"kitchen", "hall"})

    def test_zone_execution_can_wait_and_finish_independently(self):
        kitchen = self.job.zone_runs["kitchen"]
        hall = self.job.zone_runs["hall"]
        kitchen.transition(ZoneJobState.WAIT, self.now)
        hall.transition(ZoneJobState.STARTING, self.now)
        hall.finish(ZoneResult.SUCCESS, "simulated_success", self.now + timedelta(seconds=5))
        self.assertEqual(kitchen.state, ZoneJobState.WAIT)
        self.assertEqual(hall.result, ZoneResult.SUCCESS)

    def test_skipped_zone_and_partial_success_roundtrip(self):
        self.job.zone_runs["kitchen"].finish(ZoneResult.SKIPPED, "zone_disabled", self.now)
        self.job.zone_runs["hall"].finish(ZoneResult.SUCCESS, "simulated_success", self.now)
        self.job.finish(JobResult.PARTIAL_SUCCESS, "partial_success", self.now)
        restored = JobInstance.from_dict(self.job.to_dict())
        self.assertEqual(restored.result, JobResult.PARTIAL_SUCCESS)
        self.assertEqual(restored.zone_runs["kitchen"].result, ZoneResult.SKIPPED)
        self.assertEqual(restored.zone_runs["hall"].result, ZoneResult.SUCCESS)


class Stage050PreflightTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Europe/Kyiv")
        self.now = datetime(2026, 8, 12, 15, 0, tzinfo=self.zone)
        schedule = ScheduleDefinition.create(
            schedule_id="s", revision=1, name="Kitchen+Hall", enabled=True,
            weekdays=[0,1,2,3,4,5,6], dates=[], local_time="15:00",
            targets=["kitchen", "hall"], execution_window_minutes=120,
        )
        occurrence = OccurrencePlanner(self.zone).next_for_schedule(schedule, self.now, inclusive=True)
        assert occurrence is not None
        self.job = JobInstance.from_occurrence(occurrence, self.now)

    def _evaluate(self, provider: FakeProvider, zone_id: str | None = None):
        engine = PreflightEngine(provider, SimulationPreflightProvider())
        if zone_id is not None:
            return engine.evaluate_zone(self.job, zone_id, PreflightPhase.AUTHORITATIVE, self.now)
        return engine.evaluate(self.job, PreflightPhase.AUTHORITATIVE, self.now)

    def test_zone_specific_busy_does_not_block_other_zone(self):
        provider = FakeProvider(self.now)
        provider.zone_snapshots["kitchen"].busy.effective_value = True
        kitchen = self._evaluate(provider, "kitchen")
        hall = self._evaluate(provider, "hall")
        self.assertIn("zone_busy", kitchen.blocker_codes)
        self.assertEqual(kitchen.decision, PreflightDecision.WAIT)
        self.assertNotIn("zone_busy", hall.blocker_codes)
        self.assertEqual(hall.decision, PreflightDecision.PASS)

    def test_unknown_zone_presence_is_blocking_wait(self):
        provider = FakeProvider(self.now)
        provider.zone_snapshots["kitchen"].busy.effective_available = False
        report = self._evaluate(provider, "kitchen")
        self.assertIn("zone_state_unknown", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.WAIT)

    def test_inaccessible_zone_waits(self):
        provider = FakeProvider(self.now)
        provider.zone_snapshots["kitchen"].accessible.effective_value = False
        report = self._evaluate(provider, "kitchen")
        self.assertIn("zone_access_blocked", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.WAIT)

    def test_disappeared_robot_target_fails_only_that_zone_preflight(self):
        provider = FakeProvider(self.now)
        provider.invalid_targets.add("kitchen")
        bad = self._evaluate(provider, "kitchen")
        good = self._evaluate(provider, "hall")
        self.assertIn("invalid_target", bad.blocker_codes)
        self.assertEqual(bad.decision, PreflightDecision.FAIL)
        self.assertNotIn("invalid_target", good.blocker_codes)

    def test_global_disabled_is_terminal_fail(self):
        provider = FakeProvider(self.now, PreflightPolicy(execution_gate=ExecutionGateMode.DISABLED))
        report = self._evaluate(provider, "kitchen")
        self.assertEqual(report.decision, PreflightDecision.FAIL)
        self.assertIn("global_disabled", report.blocker_codes)

    def test_global_disabled_until_future_fails_and_past_passes(self):
        future = FakeProvider(self.now, PreflightPolicy(execution_gate=ExecutionGateMode.DISABLED_UNTIL, disabled_until=(self.now+timedelta(hours=1)).isoformat()))
        self.assertIn("global_disabled_until", self._evaluate(future, "kitchen").blocker_codes)
        past = FakeProvider(self.now, PreflightPolicy(execution_gate=ExecutionGateMode.DISABLED_UNTIL, disabled_until=(self.now-timedelta(minutes=1)).isoformat()))
        self.assertNotIn("global_disabled_until", self._evaluate(past, "kitchen").blocker_codes)

    def test_low_battery_waits_while_charging_and_fails_when_not_charging(self):
        provider = FakeProvider(self.now, PreflightPolicy(default_min_battery_percent=20))
        provider.values["vacuum.battery_percent"].effective_value = 5
        provider.values["vacuum.charging"].effective_value = True
        blocker = next(b for b in self._evaluate(provider, "kitchen").blockers if b.code == "battery_insufficient")
        self.assertEqual(blocker.decision_class, PreflightDecision.WAIT)
        provider.values["vacuum.charging"].effective_value = False
        blocker = next(b for b in self._evaluate(provider, "kitchen").blockers if b.code == "battery_insufficient")
        self.assertEqual(blocker.decision_class, PreflightDecision.FAIL)

    def test_battery_check_can_be_disabled(self):
        provider = FakeProvider(self.now, PreflightPolicy(default_min_battery_percent=90))
        provider.values["vacuum.battery_percent"].effective_value = 5
        provider.bindings["vacuum.battery_percent"] = CapabilityBinding.from_dict(
            "vacuum.battery_percent", {"binding_mode":"disabled"}
        )
        self.assertNotIn("battery_insufficient", self._evaluate(provider, "kitchen").blocker_codes)

    def test_per_schedule_battery_override_is_used(self):
        self.job.cleaning_params["minimum_battery_percent"] = 90
        provider = FakeProvider(self.now, PreflightPolicy(default_min_battery_percent=20))
        provider.values["vacuum.battery_percent"].effective_value = 80
        self.assertIn("battery_insufficient", self._evaluate(provider, "kitchen").blocker_codes)

    def test_dnd_check_can_be_disabled_even_when_robot_dnd_is_on(self):
        provider = FakeProvider(self.now)
        provider.bindings["dnd.active"] = CapabilityBinding.from_dict(
            "dnd.active", {"binding_mode":"disabled"}
        )
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_value = "14:00:00"
        provider.values["dnd.ends_at"].effective_value = "16:00:00"
        report = self._evaluate(provider, "kitchen")
        self.assertNotIn("dnd_active", report.blocker_codes)
        self.assertNotIn("dnd_window", report.blocker_codes)
        self.assertNotIn("dnd_safety_margin", report.blocker_codes)

    def test_dnd_switch_off_means_no_time_restriction_even_inside_configured_window(self):
        provider = FakeProvider(self.now)
        provider.values["dnd.active"].effective_value = False
        provider.values["dnd.starts_at"].effective_value = "14:00:00"
        provider.values["dnd.ends_at"].effective_value = "16:00:00"
        report = self._evaluate(provider, "kitchen")
        self.assertNotIn("dnd_active", report.blocker_codes)
        self.assertNotIn("dnd_window", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.PASS)

    def test_dnd_enabled_uses_begin_end_and_handles_overnight_window(self):
        now = datetime(2026, 8, 12, 23, 0, tzinfo=self.zone)
        provider = FakeProvider(now)
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_value = "22:00:00"
        provider.values["dnd.ends_at"].effective_value = "08:00:00"
        schedule = ScheduleDefinition.create(
            schedule_id="dnd", revision=1, name="DND", enabled=True,
            weekdays=[0,1,2,3,4,5,6], dates=[], local_time="23:00",
            targets=["kitchen"], execution_window_minutes=600,
        )
        occurrence = OccurrencePlanner(self.zone).next_for_schedule(schedule, now, inclusive=True)
        self.assertIsNotNone(occurrence)
        job = JobInstance.from_occurrence(occurrence, now)
        report = PreflightEngine(provider, SimulationPreflightProvider()).evaluate_zone(
            job, "kitchen", PreflightPhase.AUTHORITATIVE, now
        )
        self.assertIn("dnd_active", report.blocker_codes)
        blocker = next(b for b in report.blockers if b.code == "dnd_active")
        self.assertEqual(blocker.decision_class, PreflightDecision.WAIT)
        self.assertEqual(blocker.estimated_release_at.hour, 8)
        self.assertEqual(blocker.estimated_release_at.date().isoformat(), "2026-08-13")

    def test_dnd_enabled_outside_window_does_not_block(self):
        provider = FakeProvider(self.now)
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_value = "22:00:00"
        provider.values["dnd.ends_at"].effective_value = "08:00:00"
        report = self._evaluate(provider, "kitchen")
        self.assertNotIn("dnd_active", report.blocker_codes)
        self.assertNotIn("dnd_window", report.blocker_codes)

    def test_dnd_disabled_ignores_missing_manual_begin_end_entities(self):
        provider = FakeProvider(self.now)
        provider.values["dnd.active"].effective_value = False
        provider.bindings["dnd.starts_at"] = CapabilityBinding.from_dict(
            "dnd.starts_at", {"binding_mode":"manual","entity_id":"time.deleted_begin"}
        )
        provider.bindings["dnd.ends_at"] = CapabilityBinding.from_dict(
            "dnd.ends_at", {"binding_mode":"manual","entity_id":"time.deleted_end"}
        )
        report = self._evaluate(provider, "kitchen")
        self.assertNotIn("invalid_configuration", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.PASS)

    def test_dnd_enabled_without_valid_begin_end_fails_configuration(self):
        provider = FakeProvider(self.now)
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_available = False
        provider.values["dnd.starts_at"].effective_value = None
        report = self._evaluate(provider, "kitchen")
        self.assertIn("invalid_configuration", report.blocker_codes)
        blocker = next(b for b in report.blockers if b.code == "invalid_configuration")
        self.assertEqual(blocker.details.get("problem"), "dnd_schedule_missing_or_invalid")

    def test_dnd_window_covering_deadline_is_terminal_fail(self):
        provider = FakeProvider(self.now)
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_value = "14:00:00"
        provider.values["dnd.ends_at"].effective_value = "18:00:00"
        report = self._evaluate(provider, "kitchen")
        self.assertIn("dnd_window", report.blocker_codes)
        self.assertEqual(next(b for b in report.blockers if b.code == "dnd_window").decision_class, PreflightDecision.FAIL)

    def test_binary_resource_uses_simple_ok_blocked_semantics(self):
        provider = FakeProvider(self.now)
        provider.values["dock.clean_water"] = FakeInput(
            "dock.clean_water", False, effective_available=True, live_available=True,
            source_entity_id="binary_sensor.clean_water", raw_value="off", live_value=False, status="ready",
        )
        provider.bindings["dock.clean_water"] = CapabilityBinding.from_dict(
            "dock.clean_water", {"binding_mode":"auto", "normal_state":"on"}
        )
        report = self._evaluate(provider, "kitchen")
        self.assertIn("clean_water_insufficient", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.WAIT)

    def test_detergent_binary_resource_blocks_when_not_normal(self):
        provider = FakeProvider(self.now)
        provider.values["dock.detergent"] = FakeInput(
            "dock.detergent", False, effective_available=True, live_available=True,
            source_entity_id="binary_sensor.detergent", raw_value="off", live_value=False, status="ready",
        )
        provider.bindings["dock.detergent"] = CapabilityBinding.from_dict(
            "dock.detergent", {"binding_mode":"auto", "normal_state":"on"}
        )
        report = self._evaluate(provider, "kitchen")
        self.assertIn("detergent_unavailable", report.blocker_codes)

    def test_binary_resource_disabled_is_ignored(self):
        provider = FakeProvider(self.now)
        provider.values["dock.clean_water"] = FakeInput(
            "dock.clean_water", False, effective_available=True, live_available=True,
            source_entity_id="binary_sensor.clean_water", raw_value="off", live_value=False, status="ready",
        )
        provider.bindings["dock.clean_water"] = CapabilityBinding.from_dict(
            "dock.clean_water", {"binding_mode":"disabled", "normal_state":"on"}
        )
        report = self._evaluate(provider, "kitchen")
        self.assertNotIn("clean_water_insufficient", report.blocker_codes)

    def test_minimum_start_window_uses_upcoming_dnd_boundary(self):
        now = datetime(2026, 8, 12, 21, 45, tzinfo=self.zone)
        provider = FakeProvider(now, PreflightPolicy(minimum_start_window_minutes=30))
        provider.values["dnd.active"].effective_value = True
        provider.values["dnd.starts_at"].effective_value = "22:00:00"
        provider.values["dnd.ends_at"].effective_value = "08:00:00"
        schedule = ScheduleDefinition.create(
            schedule_id="dnd-window", revision=1, name="DND window", enabled=True,
            weekdays=[0,1,2,3,4,5,6], dates=[], local_time="21:45",
            targets=["kitchen"], execution_window_minutes=720,
        )
        occurrence = OccurrencePlanner(self.zone).next_for_schedule(schedule, now, inclusive=True)
        self.assertIsNotNone(occurrence)
        job = JobInstance.from_occurrence(occurrence, now)
        report = PreflightEngine(provider, SimulationPreflightProvider()).evaluate_zone(
            job, "kitchen", PreflightPhase.AUTHORITATIVE, now
        )
        self.assertIn("insufficient_time_window", report.blocker_codes)
        blocker = next(b for b in report.blockers if b.code == "insufficient_time_window")
        self.assertEqual(blocker.details.get("minimum_minutes"), 30)
        self.assertEqual(blocker.details.get("cutoff_reason"), "dnd_start")
        self.assertEqual(blocker.details.get("remaining_minutes"), 15)
        self.assertEqual(blocker.decision_class, PreflightDecision.WAIT)
        self.assertEqual(blocker.estimated_release_at.hour, 8)

    def test_per_schedule_start_window_override_is_used(self):
        self.job.cleaning_params["minimum_start_window_minutes"] = 180
        provider = FakeProvider(self.now, PreflightPolicy(minimum_start_window_minutes=0))
        report = self._evaluate(provider, "kitchen")
        self.assertIn("insufficient_time_window", report.blocker_codes)
        blocker = next(b for b in report.blockers if b.code == "insufficient_time_window")
        self.assertEqual(blocker.details.get("minimum_minutes"), 180)
        self.assertTrue(blocker.details.get("schedule_override"))

    def test_legacy_dnd_margin_migrates_to_global_start_window(self):
        policy = PreflightPolicy.from_dict({"minimum_start_window_minutes": 10, "dnd_safety_margin_minutes": 30})
        self.assertEqual(policy.minimum_start_window_minutes, 30)
        self.assertNotIn("dnd_safety_margin_minutes", policy.to_dict())

    def test_deleted_manual_binding_is_invalid_configuration(self):
        provider = FakeProvider(self.now)
        provider.bindings["dnd.active"] = CapabilityBinding.from_dict("dnd.active", {"binding_mode":"manual","entity_id":"binary_sensor.deleted"})
        report = self._evaluate(provider, "kitchen")
        self.assertIn("invalid_configuration", report.blocker_codes)
        self.assertEqual(report.decision, PreflightDecision.FAIL)

    def test_synthetic_blocker_is_clearly_prefixed(self):
        provider = FakeProvider(self.now)
        synthetic = SimulationPreflightProvider()
        synthetic.set_blocker("room_busy", True, self.job.job_id)
        report = PreflightEngine(provider, synthetic).evaluate_zone(self.job, "kitchen", PreflightPhase.AUTHORITATIVE, self.now)
        self.assertIn("synthetic_room_busy", report.blocker_codes)
        blocker = next(b for b in report.blockers if b.code == "synthetic_room_busy")
        self.assertTrue(blocker.details["synthetic"])

    def test_zone_stability_delay_arms_exact_recheck(self):
        provider = FakeProvider(self.now)
        release = self.now + timedelta(seconds=10)
        provider.zone_snapshots["kitchen"].busy = FakeInput(
            "zone.kitchen.busy", True, live_value=False, stabilizing=True,
            stabilizing_until=release, configured_delay_seconds=30,
        )
        report = self._evaluate(provider, "kitchen")
        self.assertIn("zone_busy", report.blocker_codes)
        self.assertEqual(report.next_recheck_at, release)
        blocker = next(item for item in report.blockers if item.code == "zone_busy")
        self.assertEqual(blocker.estimated_release_at, release)
        self.assertTrue(blocker.details["stabilizing"])



class Stage050ReleaseBoundaryTests(unittest.TestCase):

    def test_settings_ui_uses_cleaning_zones_not_ha_areas(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for key in ("panel.schedule_execution", "panel.robot_capabilities", "panel.resources_dock_and_dnd", "panel.cleaning_zones", "panel.real_input_overrides"):
            self.assertIn(f'this._tr("{key}")', panel)
        self.assertNotIn("HA Area", panel)
        self.assertNotIn("Home Assistant Areas", panel)
        self.assertNotIn("RoomProfile", panel)
        self.assertIn("robot_target_id", panel)
        self.assertNotIn("robot_target_ids", panel)

    def test_schedule_ui_selects_only_whole_cleaning_zones(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("cleaning_zones", panel)
        self.assertIn("data-target=", panel)
        self.assertIn("const source = this._editor.cleaning_zones", panel)
        self.assertNotIn("data-target-area", panel)

    def test_all_new_websocket_handlers_are_registered(self):
        backend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        names = (
            "websocket_settings_get", "websocket_settings_update_policy", "websocket_settings_update_binding",
            "websocket_settings_update_zone", "websocket_settings_delete_zone", "websocket_settings_rescan",
            "websocket_settings_validate", "websocket_preflight_test", "websocket_testing_override_set",
            "websocket_testing_override_clear", "websocket_testing_preset",
        )
        for name in names:
            self.assertIn(f"async_register_command(hass, {name})", backend)

    def test_zone_save_allows_duplicate_physical_target_for_execution_dedupe(self):
        backend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        planner = (MODULE_DIR / "execution_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("item.target_key == zone.target_key", backend)
        self.assertIn("dict.fromkeys", planner)

    def test_scheduler_supports_independent_zone_lifecycle_partial_success_and_skip(self):
        text = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        self.assertIn("evaluate_zone(", text)
        self.assertIn("ZoneResult.SKIPPED", text)
        self.assertIn("JobResult.PARTIAL_SUCCESS", text)
        self.assertIn("zone.disabled_at(job.planned_start)", text)
        self.assertIn("zone_disabled_until", text)

    def test_scheduler_engine_has_no_direct_physical_service_path(self):
        text = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("hass.services.async_call", text)
        self.assertIn("ExecutionManager", text)
        dry = (MODULE_DIR / "execution_backend.py").read_text(encoding="utf-8")
        self.assertIn("class DryRunExecutionBackend", dry)
        self.assertIn("class RealExecutionBackend", dry)
        self.assertIn("DryRunExecutionBackend has no reference", dry)

    def test_reset_clears_dry_run_environment_without_touching_real_history(self):
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        store = (MODULE_DIR / "job_store.py").read_text(encoding="utf-8")
        self.assertIn("self.overrides.clear()", engine)
        self.assertIn("self.execution.inject_fault_once(None)", engine)
        self.assertIn("clear_dry_run_data", engine)
        self.assertIn("execution_mode is not ExecutionMode.DRY_RUN", store)
        reset = engine[engine.index("async def async_reset_dry_run"):engine.index("def next_transition_at")]
        self.assertNotIn("async_update_entry", reset)

    def test_dependency_index_is_event_driven(self):
        dep = (MODULE_DIR / "dependency_index.py").read_text(encoding="utf-8")
        self.assertIn("async_track_state_change_event", dep)
        self.assertIn("DEBOUNCE", dep.upper())
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        self.assertIn("self.dependency_index.update_job", engine)
        self.assertIn("async_recheck_jobs", engine)


    def test_unmapped_legacy_area_is_preserved_but_disabled_until_target_is_selected(self):
        migration = (MODULE_DIR / "migrations.py").read_text(encoding="utf-8")
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        self.assertIn("__migration_target_required__:", migration)
        self.assertIn('schedule["enabled"] = False', migration)
        self.assertIn('return False, "migration_target_required"', provider)

    def test_live_current_readiness_tracks_only_pending_zones_after_partial_start(self):
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        self.assertIn("pending_zone_ids = tuple", engine)
        self.assertIn("PreflightPhase.CURRENT_PREVIEW", engine)
        self.assertIn("zone_ids=pending_zone_ids", engine)

    def test_migration_splits_early_multitarget_profiles_and_drops_area_runtime(self):
        migration = (MODULE_DIR / "migrations.py").read_text(encoding="utf-8")
        self.assertIn("for target in targets", migration)
        self.assertIn("CONF_CLEANING_ZONES", migration)
        self.assertIn("options.pop(CONF_ROOM_PROFILES", migration)


    def test_dnd_model_uses_enable_switch_plus_separate_begin_and_end_times(self):
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        preflight = (MODULE_DIR / "preflight.py").read_text(encoding="utf-8")
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('"dnd.starts_at"', provider)
        self.assertIn('_dnd_candidate(vacuum, role)', provider)
        self.assertIn('key in {"dnd.starts_at", "dnd.ends_at"}', provider)
        self.assertIn('snapshot.values["dnd.starts_at"]', preflight)
        self.assertIn('inside_actual, dnd_release_at = _dnd_window', preflight)
        self.assertIn('const translationKey = `common.binding.${key}`', panel)
        self.assertEqual(PANEL_RU["common.binding.dnd.starts_at"], "Начало режима «Не беспокоить»")
        self.assertEqual(PANEL_RU["common.binding.dnd.active"], "Режим «Не беспокоить» используется")

    def test_settings_sections_use_separate_edit_forms(self):
        panel = (ROOT / "custom_components/vacuum_schedule/frontend/panel.js").read_text()
        self.assertIn('settings-edit-binding', panel)
        self.assertIn('data-key="${this._escape(row.key)}"', panel)
        self.assertIn('_bindingSingleEditorHtml', panel)
        self.assertIn('binding-single-save', panel)
        self.assertIn('binding-single-refresh', panel)
        self.assertNotIn('settings-edit-bindings', panel)
        self.assertNotIn('_bindingGroupEditorHtml', panel)
        self.assertNotIn('settings-bindings-save', panel)
        self.assertIn('this._view === "settings" && this._roomDraft', panel)
        self.assertNotIn('</section>${this._roomEditorHtml()}', panel)

    def test_settings_page_frontend_smoke_renders_with_per_row_summary(self):
        panel_path = MODULE_DIR / "frontend" / "panel.js"
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
if (!Panel) throw new Error('panel component was not registered');
const panel = new Panel();
panel._translations = JSON.parse(fs.readFileSync({json.dumps(str(MODULE_DIR / 'frontend' / 'localization' / 'ru.json'))}, 'utf8'));
panel._fallbackTranslations = panel._translations;
panel._hass = {{ language: 'ru' }};
panel._data = {{ entries: [] }};
panel._schedulerData = {{ entries: [] }};
panel._settingsData = {{
  bindings: [{{ key:'capability.fan_mode', binding_mode:'auto', support_status:'detected', effective_input:{{effective:true}}, auto_candidate:{{}}, value_mapping:{{}}, thresholds:{{}} }}],
  related_devices: [], cleaning_zones: [], validation: [], policy: {{}}, summary: {{}}, vacuum: {{}}
}};
const html = panel._settingsHtml();
if (!html.includes('settings-edit-binding')) throw new Error('per-row Edit button missing');
if (!html.includes('Возможности робота')) throw new Error('Settings page did not render');
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_testing_override_rows_use_cleaning_zone_names(self):
        panel_path = MODULE_DIR / "frontend" / "panel.js"
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
if (!Panel) throw new Error('panel component was not registered');
const panel = new Panel();
panel._translations = JSON.parse(fs.readFileSync({json.dumps(str(MODULE_DIR / 'frontend' / 'localization' / 'ru.json'))}, 'utf8'));
panel._fallbackTranslations = panel._translations;
panel._hass = {{ language: 'ru' }};
panel._settingsData = {{
  cleaning_zones: [{{ zone_id:'5c124aaad06556f99432e1eb019441ef', name:'Балкон гостиной' }}],
  inputs: {{ values: {{}}, zones: {{ '5c124aaad06556f99432e1eb019441ef': {{
    busy: {{ key:'zone.5c124aaad06556f99432e1eb019441ef.busy', live:false, effective:false }},
    accessible: {{ key:'zone.5c124aaad06556f99432e1eb019441ef.accessible', live:true, effective:true }}
  }} }} }},
  overrides: {{ active: {{}} }}
}};
const html = panel._overrideRowsHtml();
if (!html.includes('Балкон гостиной — занятость')) throw new Error('friendly busy label missing');
if (!html.includes('Балкон гостиной — доступность')) throw new Error('friendly accessibility label missing');
if (!html.includes('zone.5c124aaad06556f99432e1eb019441ef.busy')) throw new Error('technical key must remain as diagnostic text');
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_settings_tab_navigation_does_not_wait_for_websocket_before_render(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("Navigation must never wait for a WebSocket round-trip", panel)
        nav = panel.index("const requestedView = el.dataset.view")
        immediate_render = panel.index("this._render();", nav)
        load_settings = panel.index("this._loadSettings(false)", nav)
        self.assertLess(immediate_render, load_settings)

    def test_settings_large_pickers_are_searchable_and_height_limited(self):
        panel = (ROOT / "custom_components/vacuum_schedule/frontend/panel.js").read_text()
        self.assertIn('class="search-select-input"', panel)
        self.assertIn('search-select-menu', panel)
        self.assertIn('max-height:min(240px,42vh)', panel)
        self.assertIn('this._tr("panel.search_by_name_or_entity_id")', panel)
        self.assertIn('this._tr("panel.search_segment_by_name_or_id")', panel)

    def test_cleaning_zone_summary_is_localized_and_semantic(self):
        panel = (ROOT / "custom_components/vacuum_schedule/frontend/panel.js").read_text()
        self.assertIn('_zonePermissionText', panel)
        for key in (
            "panel.cleaning_a7a7549",
            "panel.configured_currently_busy",
            "panel.configured_currently_not_busy",
            "panel.configured_currently_accessible",
            "panel.configured_currently_inaccessible",
        ):
            self.assertIn(f'this._tr("{key}")', panel)
        self.assertNotIn('${this._escape(r.control||"enabled")}', panel)

    def test_settings_overview_tables_have_explicit_headers_and_shared_header_style(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        compact = "".join(panel.split())
        for ru, en in (
            ("Возможность", "Capability"),
            ("Параметр", "Parameter"),
            ("Состояние настройки", "Configuration state"),
            ("Значение / поддержка", "Value / support"),
            ("Текущее значение", "Current value"),
            ("Зона уборки", "Cleaning zone"),
            ("Цель робота", "Robot target"),
            ("Уборка", "Cleaning"),
            ("Занятость", "Occupancy"),
            ("Доступность", "Accessibility"),
            ("Действия", "Actions"),
        ):
            key = _panel_key(ru, en)
            expected = "".join(f'this._tr("{key}")'.split())
            self.assertIn(expected, compact)
        self.assertIn('class="settings-summary-table mobile-card-table"', panel)
        self.assertIn('class="zone-summary-table mobile-card-table"', panel)
        self.assertIn('background:var(--table-header-background-color,var(--vs-neutral-quiet-fill))', panel)

    def test_detected_capability_without_runtime_value_shows_supported_not_dash(self):
        panel_path = MODULE_DIR / "frontend" / "panel.js"
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
const panel = new Panel();
panel._translations = JSON.parse(fs.readFileSync({json.dumps(str(MODULE_DIR / 'frontend' / 'localization' / 'ru.json'))}, 'utf8'));
panel._fallbackTranslations = panel._translations;
panel._hass = {{ language: 'ru' }};
const html = panel._bindingSummaryHtml([{{ key:'capability.fan_mode', support_status:'detected', effective_input:{{effective:null}} }}], true);
if (!html.includes('Поддерживается')) throw new Error('detected capability is not rendered as supported');
if (!html.includes('Значение / поддержка')) throw new Error('capability value header missing');
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_simple_binary_resource_model_has_no_numeric_water_threshold_ui(self):
        bindings = (MODULE_DIR / "bindings.py").read_text(encoding="utf-8")
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        preflight = (MODULE_DIR / "preflight.py").read_text(encoding="utf-8")
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('"dock.detergent"', bindings)
        self.assertIn('normal_state', bindings)
        self.assertIn('_infer_binary_normal_state', provider)
        self.assertIn('_check_binary_resource', preflight)
        self.assertIn('minimum_start_window_minutes', preflight)
        for key in ("panel.normal_sensor_state", "panel.minimum_remaining_battery", "panel.minimum_remaining_start_window_min"):
            self.assertIn(f'this._tr("{key}")', panel)
        for obsolete in ('Запас перед началом DND, мин', 'Чистая вода: LOW, %', 'Грязная вода: FULL, %', 'Семантика числового значения'):
            self.assertNotIn(obsolete, PANEL_RU.values())

    def test_global_and_schedule_thresholds_are_symmetric_in_ui(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        frontend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        config_flow = (MODULE_DIR / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn('id="policy-battery"', panel)
        self.assertIn('id="policy-window"', panel)
        self.assertIn('data-field="minimum_battery_percent"', panel)
        self.assertIn('data-duration-minutes="minimum_start_window_minutes"', panel)
        self.assertIn('"minimum_start_window_minutes": params.get("minimum_start_window_minutes")', frontend)
        self.assertIn('CONF_MINIMUM_START_WINDOW_MINUTES = "minimum_start_window_minutes"', config_flow)
        self.assertNotIn('data-policy-field="dnd_safety_margin_minutes"', panel)
        self.assertNotIn('data-policy-field="default_min_battery_percent"', panel)

    def test_roborock_cleaning_fluid_binary_sensor_is_discoverable_and_problem_means_off_is_normal(self):
        capabilities = (MODULE_DIR / "capabilities.py").read_text(encoding="utf-8")
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        self.assertIn('"cleaning_fluid"', capabilities)
        self.assertIn('"clean_fluid"', provider)
        self.assertIn('live_device_class == "problem"', provider)
        self.assertIn('registry_device_class == "problem"', provider)
        self.assertIn('return "off"', provider)

    def test_battery_autodiscovery_prefers_dedicated_percentage_sensor(self):
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        start = provider.index('if key == "vacuum.battery_percent"')
        end = provider.index('if key == "vacuum.charging"', start)
        block = provider[start:end]
        self.assertLess(block.index("self._battery_candidate(vacuum)"), block.index('for attr in ("battery_level"'))
        self.assertIn('"battery_sensor_entity"', block)
        self.assertIn('"legacy_vacuum_battery_attribute"', block)

    def test_battery_discovery_excludes_charge_boolean_and_accepts_ha_battery_sensor(self):
        capabilities = (MODULE_DIR / "capabilities.py").read_text(encoding="utf-8")
        self.assertIn('_BATTERY_TOKENS = ("battery", "battery_level", "battery_percent", "battery_percentage")', capabilities)
        self.assertNotIn('_BATTERY_TOKENS = ("battery", "charge")', capabilities)
        self.assertIn('domain not in {"sensor", "number"}', capabilities)
        self.assertIn('device_class == "battery"', capabilities)
        self.assertIn('unit in {"%", "percent", "percentage"}', capabilities)
        self.assertIn('primary_device_id', (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8"))

    def test_zone_stability_delay_ui_and_backend_are_present(self):
        policy = (MODULE_DIR / "bindings.py").read_text(encoding="utf-8")
        zone_model = (MODULE_DIR / "cleaning_zones.py").read_text(encoding="utf-8")
        provider = (MODULE_DIR / "input_provider.py").read_text(encoding="utf-8")
        preflight = (MODULE_DIR / "preflight.py").read_text(encoding="utf-8")
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("occupancy_clear_delay_seconds", policy)
        self.assertIn("access_stable_delay_seconds", policy)
        self.assertIn("occupancy_clear_delay_seconds", zone_model)
        self.assertIn("access_stable_delay_seconds", zone_model)
        self.assertIn("_stabilize_zone_input", provider)
        self.assertIn("stabilizing_until", provider)
        self.assertIn("estimated_release_at=getattr(zone.busy", preflight)
        self.assertIn("estimated_release_at=getattr(zone.accessible", preflight)
        self.assertIn("async_track_state_change_event", engine)
        self.assertIn('id="policy-occupancy-delay"', panel)
        self.assertIn('id="policy-access-delay"', panel)
        self.assertIn('id="zone-occupancy-delay"', panel)
        self.assertIn('id="zone-access-delay"', panel)
        self.assertIn('this._tr("panel.busy_value_s", {p1: remaining})', panel)
        self.assertIn('this._tr("panel.inaccessible_value_s", {p1: remaining})', panel)
        self.assertIn("_syncZoneCountdownTimer", panel)

    def test_ui_buttons_use_mdi_icons_and_unified_color_family(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for icon in (
            '_mdi("pencil-outline")', '_mdi("delete-outline")', '_mdi("content-save-outline")',
            '_mdi("refresh")', '_mdi("plus")', '_mdi("cog-outline")', '_mdi("flask-outline")',
            '"calendar-clock"', '"view-dashboard-outline"',
        ):
            self.assertIn(icon, panel)
        self.assertIn('button.primary { color:var(--vs-brand-on); background:var(--vs-brand-fill);', panel)
        self.assertIn('button.ghost { color:var(--vs-neutral-on); background:var(--vs-neutral-fill);', panel)
        self.assertIn('button.danger { color:var(--vs-danger-on); background:var(--vs-danger-fill);', panel)
        self.assertIn('--vs-brand-fill:var(--wa-color-brand-fill-normal,var(--ha-color-fill-primary-normal-resting,var(--primary-color)))', panel)
        self.assertIn('--vs-danger-fill:var(--wa-color-danger-fill-normal,var(--ha-color-fill-danger-normal-resting,var(--error-color)))', panel)
        self.assertNotIn('color-mix(', panel)
        self.assertNotIn('>↻ ', panel)
        self.assertNotIn('>✓ ', panel)
        self.assertNotIn('>＋ ', panel)

    def test_non_obvious_writes_have_snackbar_feedback(self):
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for token in ('_notify(', '.toast-host', '.ui-toast'):
            self.assertIn(token, panel)
        for key in (
            "panel.global_settings_saved", "panel.applied", "panel.preset_applied",
            "panel.rediscovery_completed", "panel.configuration_validation_completed", "panel.simulation_reset",
        ):
            self.assertIn(f'this._tr("{key}")', panel)
        self.assertIn('this._notify(this._error, "error", 4200)', panel)


if __name__ == "__main__":
    unittest.main()
