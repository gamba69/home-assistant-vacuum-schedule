"""Regression coverage for reactive multi-person early execution in 0.12.22."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from force_conditions import ForceConditionEngine  # noqa: E402
from job import JobOrigin  # noqa: E402
from scheduler_arbiter import ArbiterCandidate, SchedulerArbiter  # noqa: E402


class _State:
    def __init__(self, state: str, last_changed: datetime) -> None:
        self.state = state
        self.last_changed = last_changed


class _States:
    def __init__(self, values: dict[str, _State]) -> None:
        self.values = values

    def get(self, entity_id: str):
        return self.values.get(entity_id)


class _Hass:
    def __init__(self, values: dict[str, _State]) -> None:
        self.states = _States(values)


def test_second_person_transition_satisfies_people_absent_threshold() -> None:
    now = datetime(2026, 9, 3, 8, 0)
    hass = _Hass({
        "person.first": _State("not_home", now - timedelta(minutes=20)),
        "person.second": _State("home", now - timedelta(minutes=5)),
    })
    engine = ForceConditionEngine(hass)
    condition = {
        "condition_id": "both_away",
        "type": "people_absent",
        "entity_ids": ["person.first", "person.second"],
        "minimum_absent": 2,
        "for_minutes": 0,
    }

    before = engine.evaluate(job_id="job", groups=[[condition]], now=now)
    assert before["matched"] is False
    assert before["groups"][0]["conditions"][0]["absent_count"] == 1

    transition_at = now + timedelta(seconds=1)
    hass.states.values["person.second"] = _State("not_home", transition_at)
    after = engine.evaluate(job_id="job", groups=[[condition]], now=transition_at)
    assert after["matched"] is True
    assert after["groups"][0]["conditions"][0]["absent_count"] == 2


def test_force_listener_survives_notification_registration_and_is_refreshed() -> None:
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    force_start = source.index("    def _subscribe_force_inputs")
    force_end = source.index("    def _subscribe_notification_actions", force_start)
    force_block = source[force_start:force_end]
    notification_start = force_end
    notification_end = source.index("    def _watchdog_callback", notification_start)
    notification_block = source[notification_start:notification_end]
    settings_start = source.index("    async def async_settings_changed")
    settings_end = source.index("    async def async_real_execution_readiness", settings_start)
    settings_block = source[settings_start:settings_end]

    assert "self._request_zone_reconcile()" in force_block
    assert "async_create_task(self.async_reconcile())" not in force_block
    assert "_force_input_unsub" not in notification_block
    assert settings_block.index("self._subscribe_zone_inputs()") < settings_block.index(
        "self._subscribe_force_inputs()"
    ) < settings_block.index("await self.async_reconcile()")


def _force_candidate(
    job_id: str,
    *,
    planned_start: datetime,
    priority: int,
) -> ArbiterCandidate:
    return ArbiterCandidate(
        job_id=job_id,
        origin=JobOrigin.SCHEDULED,
        planned_start=planned_start,
        manual_triggered_at=None,
        manual_release_at=None,
        ready_zone_ids=("zone",),
        candidate_class="forced",
        force_priority=priority,
        preempts_scheduled=True,
    )


def test_multiple_preemptive_candidates_remain_deterministic() -> None:
    now = datetime(2026, 9, 3, 8, 0)
    candidates = [
        _force_candidate("low", planned_start=now + timedelta(hours=1), priority=10),
        _force_candidate("high-late", planned_start=now + timedelta(hours=3), priority=50),
        _force_candidate("high-near", planned_start=now + timedelta(hours=2), priority=50),
    ]

    ordered = SchedulerArbiter().ordered(candidates)
    assert [item.job_id for item in ordered] == ["high-near", "high-late", "low"]
    assert SchedulerArbiter().select(candidates, lease_owner=None).job_id == "high-near"
