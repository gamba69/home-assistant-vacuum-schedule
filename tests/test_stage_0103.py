"""Force Condition Engine contracts for Vacuum Schedule 0.10.7."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from force_conditions import ForceConditionEngine, force_dependencies, select_force_candidate  # noqa: E402
from job import JobInstance, JobState  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


class _State:
    def __init__(self, state: str, last_changed: datetime | None = None) -> None:
        self.state = state
        self.last_changed = last_changed


class _States:
    def __init__(self, values: dict[str, _State] | None = None) -> None:
        self.values = values or {}

    def get(self, entity_id: str):
        return self.values.get(entity_id)


class _Hass:
    def __init__(self, values: dict[str, _State] | None = None) -> None:
        self.states = _States(values)


def _force_schedule(**changes) -> ScheduleDefinition:
    payload = dict(
        schedule_id="force-a",
        revision=4,
        name="Force test",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        dates=[],
        local_time="17:00",
        targets=["kitchen"],
        force_enabled=True,
        force_max_advance_minutes=360,
        force_priority=120,
        force_condition_groups=[
            [
                {
                    "condition_id": "presence",
                    "type": "binary",
                    "entity_id": "binary_sensor.empty",
                    "state": "on",
                    "for_minutes": 5,
                }
            ],
            [
                {
                    "condition_id": "away",
                    "type": "person",
                    "entity_id": "person.owner",
                    "state": "not_home",
                }
            ],
        ],
    )
    payload.update(changes)
    return ScheduleDefinition.create(**payload)




def test_force_schedule_roundtrip_revision_and_dependencies():
    schedule = _force_schedule()
    restored = ScheduleDefinition.from_dict(schedule.to_dict())
    assert restored.force_enabled is True
    assert restored.force_max_advance_minutes == 360
    assert restored.force_priority == 120
    assert restored.force_condition_groups == schedule.force_condition_groups
    changed = schedule.revised(force_priority=121)
    assert changed.revision == schedule.revision + 1
    assert force_dependencies(schedule.force_config()) == (
        "binary_sensor.empty",
        "person.owner",
    )


def test_legacy_schedule_defaults_to_force_disabled_without_semantic_revision_change():
    restored = ScheduleDefinition.from_dict({
        "schedule_id": "legacy-force",
        "revision": 9,
        "name": "Legacy",
        "enabled": True,
        "weekdays": [0],
        "dates": [],
        "local_time": "17:00",
        "targets": ["kitchen"],
    })
    assert restored.revision == 9
    assert restored.force_config() == {
        "enabled": False,
        "max_advance_minutes": 0,
        "priority": 0,
        "preempts_scheduled": False,
        "condition_groups": [],
    }
    migration = (MODULE / "migrations.py").read_text(encoding="utf-8")
    assert 'schedule.setdefault("force_enabled", False)' in migration
    assert 'schedule.setdefault("force_max_advance_minutes", 0)' in migration
    assert 'schedule.setdefault("force_priority", 0)' in migration


def test_occurrence_and_job_snapshot_force_policy():
    zone = ZoneInfo("Europe/Kyiv")
    schedule = _force_schedule()
    occurrence = OccurrencePlanner(zone).next_for_schedule(
        schedule,
        datetime(2026, 8, 24, 10, 0, tzinfo=zone),
        inclusive=True,
    )
    assert occurrence is not None
    assert occurrence.force_config["enabled"] is True
    assert occurrence.force_config["priority"] == 120
    job = JobInstance.from_occurrence(occurrence, datetime(2026, 8, 24, 10, 1, tzinfo=zone))
    assert job.state is JobState.PLANNED
    assert job.manual_release_at is None
    assert job.metadata["force_snapshot"]["max_advance_minutes"] == 360
    changed = schedule.revised(force_priority=999)
    assert job.metadata["force_snapshot"]["priority"] == 120
    assert changed.force_priority == 999


def test_or_between_groups_and_and_inside_group():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    hass = _Hass({
        "binary_sensor.empty": _State("on", now - timedelta(minutes=10)),
        "sensor.temperature": _State("19", now - timedelta(hours=1)),
        "person.owner": _State("work", now - timedelta(hours=2)),
    })
    engine = ForceConditionEngine(hass)
    groups = [
        [
            {"condition_id": "a", "type": "binary", "entity_id": "binary_sensor.empty", "state": "on"},
            {"condition_id": "b", "type": "numeric", "entity_id": "sensor.temperature", "operator": "gte", "value": 21},
        ],
        [
            {"condition_id": "c", "type": "person", "entity_id": "person.owner", "state": "not_home"},
        ],
    ]
    result = engine.evaluate(job_id="job", groups=groups, now=now)
    assert result["groups"][0]["matched"] is False
    assert result["groups"][1]["matched"] is True
    assert result["matched"] is True


def test_entity_numeric_person_binary_and_unknown_semantics():
    now = datetime(2026, 8, 23, 12, 0)
    hass = _Hass({
        "sensor.mode": _State("eco", now - timedelta(minutes=10)),
        "sensor.value": _State("42.5", now - timedelta(minutes=10)),
        "person.owner": _State("office", now - timedelta(minutes=10)),
        "binary_sensor.door": _State("off", now - timedelta(minutes=10)),
        "sensor.unknown": _State("unknown", now - timedelta(minutes=10)),
    })
    engine = ForceConditionEngine(hass)
    conditions = [
        {"condition_id": "eq", "type": "entity", "entity_id": "sensor.mode", "operator": "eq", "value": "eco"},
        {"condition_id": "ne", "type": "entity", "entity_id": "sensor.mode", "operator": "ne", "value": "turbo"},
        {"condition_id": "gt", "type": "numeric", "entity_id": "sensor.value", "operator": "gt", "value": 40},
        {"condition_id": "gte", "type": "numeric", "entity_id": "sensor.value", "operator": "gte", "value": 42.5},
        {"condition_id": "lt", "type": "numeric", "entity_id": "sensor.value", "operator": "lt", "value": 50},
        {"condition_id": "lte", "type": "numeric", "entity_id": "sensor.value", "operator": "lte", "value": 42.5},
        {"condition_id": "away", "type": "person", "entity_id": "person.owner", "state": "not_home"},
        {"condition_id": "off", "type": "binary", "entity_id": "binary_sensor.door", "state": "off"},
    ]
    assert engine.evaluate(job_id="all", groups=[conditions], now=now)["matched"] is True
    unknown = engine.evaluate(
        job_id="unknown",
        groups=[[{"condition_id": "u", "type": "entity", "entity_id": "sensor.unknown", "operator": "ne", "value": "x"}]],
        now=now,
    )
    missing = engine.evaluate(
        job_id="missing",
        groups=[[{"condition_id": "m", "type": "binary", "entity_id": "binary_sensor.missing", "state": "off"}]],
        now=now,
    )
    assert unknown["matched"] is False
    assert missing["matched"] is False


def test_people_absent_requires_at_least_n_valid_away_people():
    now = datetime(2026, 8, 23, 12, 0)
    hass = _Hass({
        "person.a": _State("work", now - timedelta(hours=2)),
        "person.b": _State("home", now - timedelta(minutes=30)),
        "person.c": _State("gym", now - timedelta(hours=1)),
        "person.d": _State("unavailable", now - timedelta(hours=3)),
    })
    engine = ForceConditionEngine(hass)
    condition = {
        "condition_id": "people",
        "type": "people_absent",
        "entity_ids": ["person.a", "person.b", "person.c", "person.d"],
        "minimum_absent": 2,
    }
    result = engine.evaluate(job_id="people", groups=[[condition]], now=now)
    detail = result["groups"][0]["conditions"][0]
    assert result["matched"] is True
    assert detail["absent_count"] == 2
    assert detail["states"]["person.d"] is None


def test_for_minutes_tracks_continuous_truth_and_exact_maturity():
    now = datetime(2026, 8, 23, 12, 0)
    hass = _Hass({"sensor.value": _State("12", now - timedelta(minutes=2))})
    engine = ForceConditionEngine(hass)
    condition = {
        "condition_id": "held",
        "type": "numeric",
        "entity_id": "sensor.value",
        "operator": "gte",
        "value": 10,
        "for_minutes": 5,
    }
    first = engine.evaluate(job_id="dwell", groups=[[condition]], now=now)
    detail = first["groups"][0]["conditions"][0]
    assert first["matched"] is False
    assert 179 <= detail["remaining_seconds"] <= 181
    assert first["next_transition_at"] == (now + timedelta(minutes=3)).isoformat()

    # Change the physical value and HA last_changed while it remains satisfying.
    # Runtime dwell must continue from the engine's original true_since.
    hass.states.values["sensor.value"] = _State("15", now + timedelta(minutes=1))
    mature = engine.evaluate(job_id="dwell", groups=[[condition]], now=now + timedelta(minutes=3, seconds=1))
    assert mature["matched"] is True


def test_force_candidate_selection_is_priority_then_planned_start_then_job_id():
    evaluations = [
        {"job_id": "low", "eligible": True, "priority": 10, "planned_start": "2026-08-23T17:00:00+03:00"},
        {"job_id": "late", "eligible": True, "priority": 20, "planned_start": "2026-08-23T18:00:00+03:00"},
        {"job_id": "early-b", "eligible": True, "priority": 20, "planned_start": "2026-08-23T17:30:00+03:00"},
        {"job_id": "early-a", "eligible": True, "priority": 20, "planned_start": "2026-08-23T17:30:00+03:00"},
    ]
    assert select_force_candidate(evaluations) == "early-a"
    assert select_force_candidate([{**evaluations[0], "eligible": False}]) is None


def test_force_evaluation_stays_read_only_while_execution_is_delegated_to_arbiter():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    section = source[source.index("def _evaluate_force_candidates"):source.index("def _set_execution_lease_wait")]
    assert '"stage": "LIVE_EXECUTION"' in section
    assert '"early_execution_enabled": True' in section
    assert "manual_release_at =" not in section
    assert ".transition(JobState.WAIT" not in section
    assert "async_start_ready_zones" not in section
    arbiter = source[source.index("async def _arbitrate_jobs"):source.index("async def async_reconcile_current_timeline")]
    assert 'candidate_class="forced"' in arbiter
    assert "async_start_ready_zones" in arbiter
    assert 'selected_job.metadata["force_execution"]' in arbiter

def test_frontend_exposes_force_editor_condition_types_and_candidate_readiness():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    for token in (
        "_forceEditorHtml()",
        "_forceConditionRow(",
        "_forceReadinessHtml(",
        "data-force-field",
        "data-force-condition-field",
        '"entity"',
        '"numeric"',
        '"person"',
        '"people_absent"',
        '"binary"',
        "panel.force_selected_candidate",
        "panel.force_conditions_not_met",
    ):
        assert token in panel
