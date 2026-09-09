"""Behavioral regression tests for rebuilding the future schedule projection."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import ModuleType
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))
pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)
ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
ha_core = sys.modules.setdefault("homeassistant.core", ModuleType("homeassistant.core"))
ha_core.HomeAssistant = object
ha_helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
ha_storage = sys.modules.setdefault("homeassistant.helpers.storage", ModuleType("homeassistant.helpers.storage"))
ha_storage.Store = object

from custom_components.vacuum_schedule.execution_models import ExecutionMode
from custom_components.vacuum_schedule.job import JobInstance, JobResult
from custom_components.vacuum_schedule.planner import OccurrencePlanner
from custom_components.vacuum_schedule.schedule import ScheduleDefinition
from custom_components.vacuum_schedule.schedule_refresh import (
    decide_schedule_refresh_consumption,
    select_schedule_refresh_occurrence,
)


def _schedule() -> ScheduleDefinition:
    return ScheduleDefinition.create(
        name="Daily",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        dates=[],
        local_time="16:40",
        targets=["zone_a"],
        force_enabled=False,
        force_max_advance_minutes=0,
    )


def _future_occurrence(now: datetime):
    occurrence = OccurrencePlanner("Europe/Kyiv").next_for_schedule(_schedule(), now, inclusive=True)
    assert occurrence is not None
    assert occurrence.planned_start > now
    return occurrence


def _terminal_job(now: datetime, *, force: bool) -> JobInstance:
    occurrence = _future_occurrence(now)
    job = JobInstance.from_occurrence(occurrence, now)
    job.execution_mode = ExecutionMode.REAL
    if force:
        job.metadata["force_execution"] = {
            "selected_at": now.isoformat(),
            "committed": True,
        }
        job.finish(JobResult.SUCCESS, "execution_success", now + timedelta(minutes=10))
    else:
        job.finish(JobResult.FAILED, "deadline_expired", now + timedelta(minutes=1))
    return job


def test_skipped_or_failed_future_consumption_is_rearmed_by_plain_refresh():
    """A stale terminal cursor must not make refresh jump from tomorrow to the day after."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    consumed = _terminal_job(now, force=False)
    decision = decide_schedule_refresh_consumption(
        consumed,
        planned_start=consumed.planned_start,
        now=now,
        restore_early_executed=False,
    )
    assert decision.materialize is True
    assert decision.rearm is True
    assert decision.restored_early is False


def test_early_executed_future_occurrence_stays_consumed_without_checkbox():
    """Plain refresh preserves a deliberate early execution of a still-future slot."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    consumed = _terminal_job(now, force=True)
    decision = decide_schedule_refresh_consumption(
        consumed,
        planned_start=consumed.planned_start,
        now=now,
        restore_early_executed=False,
    )
    assert decision.materialize is False
    assert decision.rearm is False


def test_early_executed_future_occurrence_returns_with_checkbox():
    """The confirmation checkbox explicitly re-arms an early-executed future slot."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    consumed = _terminal_job(now, force=True)
    decision = decide_schedule_refresh_consumption(
        consumed,
        planned_start=consumed.planned_start,
        now=now,
        restore_early_executed=True,
    )
    assert decision.materialize is True
    assert decision.rearm is True
    assert decision.restored_early is True


def test_started_nonterminal_occurrence_is_never_duplicated_by_refresh():
    """A protected active execution remains authoritative during a projection rebuild."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    occurrence = _future_occurrence(now)
    active = JobInstance.from_occurrence(occurrence, now)
    active.execution_mode = ExecutionMode.REAL
    active.metadata["force_execution"] = {"selected_at": now.isoformat(), "committed": True}
    decision = decide_schedule_refresh_consumption(
        active,
        planned_start=active.planned_start,
        now=now,
        restore_early_executed=True,
    )
    assert decision.materialize is False
    assert decision.rearm is False


def test_refresh_candidate_returns_tomorrow_instead_of_stale_day_after_projection():
    """The real candidate selector must rebuild from now, not continue the old cursor."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    planner = OccurrencePlanner("Europe/Kyiv")
    schedule = _schedule()
    tomorrow = planner.next_for_schedule(schedule, now, inclusive=True)
    assert tomorrow is not None
    day_after = planner.next_for_schedule(schedule, tomorrow.planned_start, inclusive=False)
    assert day_after is not None

    stale_consumption = JobInstance.from_occurrence(tomorrow, now)
    stale_consumption.execution_mode = ExecutionMode.REAL
    stale_consumption.finish(JobResult.FAILED, "deadline_expired", now + timedelta(minutes=1))

    # This represents the stale projection the UI showed before refresh. The
    # engine discards it before candidate selection; it must not act as a cursor.
    stale_day_after_projection = JobInstance.from_occurrence(day_after, now)
    assert stale_day_after_projection.planned_start > tomorrow.planned_start

    selected, rearmed_from, restored_early = select_schedule_refresh_occurrence(
        planner,
        schedule,
        now,
        consumed_lookup=lambda occurrence: (
            stale_consumption if occurrence.occurrence_id == tomorrow.occurrence_id else None
        ),
        restore_early_executed=False,
    )
    assert selected is not None
    assert selected.occurrence_id == tomorrow.occurrence_id
    assert selected.planned_start == tomorrow.planned_start
    assert rearmed_from is stale_consumption
    assert restored_early is False
