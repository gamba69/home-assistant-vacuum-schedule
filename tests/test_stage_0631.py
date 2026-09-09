"""Regression tests for deadline boundary finishes only unstarted zones, engine boundary path uses own deadline not successor displacement, and materializer preserves current jobs and keeps one future placeholder.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import (  # noqa: E402
    JobInstance,
    JobState,
    ZoneJobState,
    ZoneResult,
)
from notification_formatting import localized_reason  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402
from preflight_models import (  # noqa: E402
    Blocker,
    PreflightDecision,
    PreflightPhase,
    PreflightReport,
    dominant_fail_reason,
)
from schedule import ScheduleDefinition, ScheduleValidationError  # noqa: E402
from time_utils import instant_delta  # noqa: E402

MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
ENGINE = MODULE / "scheduler_engine.py"
STORE = MODULE / "job_store.py"
SCHEDULE = MODULE / "schedule.py"
PLANNER = MODULE / "planner.py"
INPUT_PROVIDER = MODULE / "input_provider.py"
CALENDAR = MODULE / "calendar.py"


def _schedule(
    *,
    schedule_id: str = "s-0631",
    local_time: str = "20:00",
    dates: list[str] | None = None,
    weekdays: list[int] | None = None,
    execution_window_minutes: int = 120,
    prewarning_minutes: int = 15,
) -> ScheduleDefinition:
    return ScheduleDefinition.create(
        schedule_id=schedule_id,
        revision=1,
        name="Stage 0.6.31",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5, 6] if weekdays is None and dates is None else (weekdays or []),
        dates=dates or [],
        local_time=local_time,
        targets=["zone-a", "zone-b", "zone-c", "zone-d"],
        execution_window_minutes=execution_window_minutes,
        prewarning_minutes=prewarning_minutes,
    )


def _job(now: datetime) -> JobInstance:
    planner = OccurrencePlanner(now.tzinfo or ZoneInfo("UTC"))
    occurrence = planner.next_for_schedule(_schedule(), now)
    assert occurrence is not None
    return JobInstance.from_occurrence(occurrence, now)




def test_deadline_boundary_finishes_only_unstarted_zones():
    zone = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 8, 14, 19, 0, tzinfo=zone)
    job = _job(now)
    boundary = job.deadline_at

    wait_run = job.zone_runs["zone-a"]
    starting_run = job.zone_runs["zone-b"]
    running_run = job.zone_runs["zone-c"]
    planned_run = job.zone_runs["zone-d"]

    wait_run.transition(ZoneJobState.WAIT, now)
    starting_run.transition(ZoneJobState.STARTING, now)
    running_run.transition(ZoneJobState.STARTING, now)
    running_run.transition(ZoneJobState.RUNNING, now + timedelta(seconds=1))

    changed = job.finish_unstarted_zones("deadline_expired", boundary)
    assert changed is True
    assert wait_run.state is ZoneJobState.FINISHED
    assert wait_run.result is ZoneResult.FAILED
    assert wait_run.reason_code == "deadline_expired"
    assert planned_run.state is ZoneJobState.FINISHED
    assert planned_run.reason_code == "deadline_expired"
    assert starting_run.state is ZoneJobState.STARTING
    assert starting_run.result is None
    assert running_run.state is ZoneJobState.RUNNING
    assert running_run.result is None


def test_engine_boundary_path_uses_own_deadline_not_successor_displacement():
    source = ENGINE.read_text(encoding="utf-8")
    start = source.index("async def _prepare_job")
    end = source.index("if (\n            job.state is JobState.PLANNED", start)
    boundary = source[start:end]
    assert "instant_ge(now, job.deadline_at)" in boundary
    assert "DISPLACED_BY_NEXT_OCCURRENCE" not in boundary
    assert "_finish_unstarted_zones" in boundary


def test_materializer_preserves_current_jobs_and_keeps_one_future_placeholder():
    source = ENGINE.read_text(encoding="utf-8")
    start = source.index("def _materialize_missing")
    end = source.index("def _finish(", start)
    materializer = source[start:end]
    assert "Keep one future placeholder while preserving independent current Jobs" in materializer
    assert "any(instant_gt(job.planned_start, now) for job in existing)" in materializer
    assert "if existing:" not in materializer
    assert "active_scheduled_job_exists" not in (MODULE / "job_store.py").read_text(encoding="utf-8")


def test_authoritative_fail_preserves_concrete_blocker_reason():
    now = datetime(2026, 8, 14, 20, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    report = PreflightReport(
        decision=PreflightDecision.FAIL,
        phase=PreflightPhase.AUTHORITATIVE,
        blockers=(
            Blocker(
                code="vacuum_busy",
                decision_class=PreflightDecision.WAIT,
                source_key="vacuum.activity",
            ),
            Blocker(
                code="battery_insufficient",
                decision_class=PreflightDecision.FAIL,
                source_key="vacuum.battery_percent",
            ),
            Blocker(
                code="clean_water_insufficient",
                decision_class=PreflightDecision.FAIL,
                source_key="dock.clean_water",
            ),
        ),
        evaluated_at=now,
        input_snapshot_id="snapshot-0631",
    )
    assert dominant_fail_reason(report) == "battery_insufficient"
    assert localized_reason("battery_insufficient", "ru") == "Недостаточный заряд аккумулятора"
    assert localized_reason("clean_water_insufficient", "en") == "Clean-water state blocks cleaning"


def test_durable_history_is_separate_from_bounded_operational_ui_history():
    source = STORE.read_text(encoding="utf-8")
    assert "_OPERATIONAL_HISTORY_LIMIT = 100" in source
    assert 'f"vacuum_schedule.{entry_id}.job_history"' in source
    assert "self.terminal_archive: list[JobInstance] = []" in source
    assert '"terminal_jobs": [item.to_dict() for item in self.terminal_archive]' in source
    assert "self.terminal_archive.append(job)" in source
    assert "self.terminal_archive = self.terminal_archive[-" not in source
    assert "self.events.append(event)" in source
    assert '"migrated_terminal_snapshot"' in (MODULE / "storage_migrations.py").read_text(encoding="utf-8")


def test_scheduler_emits_append_only_lifecycle_and_user_action_events():
    source = ENGINE.read_text(encoding="utf-8")
    assert 'self.store.append_event(' in source
    assert 'clock_offset_seconds' in source
    assert '"user_action"' in source
    assert '"wait_cycle": job.wait_cycle' in STORE.read_text(encoding="utf-8")
    assert '"zones": {' in STORE.read_text(encoding="utf-8")


def test_spring_dst_execution_window_is_elapsed_time_not_wall_time():
    zone = ZoneInfo("Europe/Kyiv")
    schedule = _schedule(
        schedule_id="spring",
        local_time="02:30",
        dates=["2026-03-29"],
        execution_window_minutes=120,
        prewarning_minutes=0,
    )
    planner = OccurrencePlanner(zone)
    occurrence = planner.next_for_schedule(
        schedule, datetime(2026, 3, 28, 12, 0, tzinfo=zone)
    )
    assert occurrence is not None
    assert occurrence.planned_start.hour == 2
    assert occurrence.deadline_at.hour == 5
    assert occurrence.planned_start.utcoffset() != occurrence.deadline_at.utcoffset()
    assert instant_delta(occurrence.deadline_at, occurrence.planned_start) == timedelta(hours=2)


def test_fall_dst_execution_window_is_elapsed_time_not_wall_time():
    zone = ZoneInfo("Europe/Kyiv")
    schedule = _schedule(
        schedule_id="fall",
        local_time="02:30",
        dates=["2026-10-25"],
        execution_window_minutes=120,
        prewarning_minutes=0,
    )
    planner = OccurrencePlanner(zone)
    occurrence = planner.next_for_schedule(
        schedule, datetime(2026, 10, 24, 12, 0, tzinfo=zone)
    )
    assert occurrence is not None
    assert occurrence.deadline_at.hour == 3
    assert occurrence.deadline_at.fold == 1
    assert instant_delta(occurrence.deadline_at, occurrence.planned_start) == timedelta(hours=2)


def test_zero_execution_window_is_rejected_and_legacy_zero_is_migrated():
    with pytest.raises(ScheduleValidationError, match="invalid_execution_window"):
        _schedule(execution_window_minutes=0)

    valid = _schedule(execution_window_minutes=1)
    raw = valid.to_dict()
    raw["execution_window_minutes"] = 0
    restored = ScheduleDefinition.from_dict(raw)
    assert restored.execution_window_minutes == 1

    panel = PANEL.read_text(encoding="utf-8")
    flow = (MODULE / "config_flow.py").read_text(encoding="utf-8")
    assert 'data-duration-minutes="execution_window_minutes" type="text"' in panel
    assert '_parseDurationHm(el.value,{min:key==="execution_window_minutes"?1:0,max:10080})' in panel
    assert "NumberSelectorConfig(min=1, max=10080" in flow


def test_scheduler_time_comparisons_use_absolute_instant_helpers():
    engine = ENGINE.read_text(encoding="utf-8")
    planner = PLANNER.read_text(encoding="utf-8")
    assert "instant_ge(now, job.deadline_at)" in engine
    assert "instant_gt(job.planned_start, now)" in engine
    assert "instant_lt(now, job.effective_start)" in engine
    assert "deadline = instant_add(" in planner
    assert "warning = instant_add(" in planner
    assert "instant_delta(item.planned_start, now)" in planner


def test_spring_dst_prewarning_is_elapsed_time_not_wall_time():
    zone = ZoneInfo("Europe/Kyiv")
    schedule = _schedule(
        schedule_id="spring-warning",
        local_time="05:30",
        dates=["2026-03-29"],
        execution_window_minutes=60,
        prewarning_minutes=120,
    )
    planner = OccurrencePlanner(zone)
    occurrence = planner.next_for_schedule(
        schedule, datetime(2026, 3, 28, 12, 0, tzinfo=zone)
    )
    assert occurrence is not None
    assert occurrence.planned_start.hour == 5
    assert occurrence.warning_at.hour == 2
    assert occurrence.planned_start.utcoffset() != occurrence.warning_at.utcoffset()
    assert instant_delta(occurrence.planned_start, occurrence.warning_at) == timedelta(hours=2)


def test_negative_execution_window_remains_invalid_during_legacy_load():
    valid = _schedule(execution_window_minutes=1)
    raw = valid.to_dict()
    raw["execution_window_minutes"] = -1
    with pytest.raises(ScheduleValidationError, match="invalid_execution_window"):
        ScheduleDefinition.from_dict(raw)


def test_zone_stabilization_and_calendar_ranges_use_absolute_instants():
    inputs = INPUT_PROVIDER.read_text(encoding="utf-8")
    calendar = CALENDAR.read_text(encoding="utf-8")
    assert "until = instant_add(stable_since, timedelta(seconds=delay_seconds))" in inputs
    assert "if instant_le(until, now):" in inputs
    assert "max(free_since_candidates, key=as_utc)" in inputs
    assert "min(open_path_since_candidates, key=as_utc)" in inputs
    assert "search_start = instant_add(start_date, -timedelta(minutes=max_window))" in calendar
    assert "instant_le(previous.planned_start, now)" in calendar
    assert "instant_gt(self._display_end(item), start_date)" in calendar
