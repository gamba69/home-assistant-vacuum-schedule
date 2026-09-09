"""Regression coverage for active forecasts, preemptive early execution and WAIT history."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobInstance, JobOrigin, JobState  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402
from scheduler_arbiter import ArbiterCandidate, SchedulerArbiter  # noqa: E402


def _schedule(*, preempts: bool = False) -> ScheduleDefinition:
    return ScheduleDefinition.create(
        name="Daily",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        dates=[],
        local_time="12:00",
        targets=["zone_a"],
        force_enabled=True,
        force_max_advance_minutes=180,
        force_priority=25,
        force_preempts_scheduled=preempts,
        force_condition_groups=[[{"type": "binary", "entity_id": "binary_sensor.ready", "state": "on"}]],
    )


def _candidate(job_id: str, *, kind: str, planned: datetime, preempts: bool = False) -> ArbiterCandidate:
    return ArbiterCandidate(
        job_id=job_id,
        origin=JobOrigin.MANUAL if kind == "manual" else JobOrigin.SCHEDULED,
        planned_start=planned,
        manual_triggered_at=planned if kind == "manual" else None,
        manual_release_at=None,
        ready_zone_ids=("zone_a",),
        candidate_class=kind,
        force_priority=25,
        preempts_scheduled=preempts,
    )


def test_preemptive_early_flag_roundtrips_in_occurrence_snapshot():
    schedule = ScheduleDefinition.from_dict(_schedule(preempts=True).to_dict())
    assert schedule.force_preempts_scheduled is True
    assert schedule.force_config()["preempts_scheduled"] is True


def test_preemptive_early_execution_outranks_scheduled_but_not_manual():
    now = datetime(2026, 9, 2, 10, 0)
    arbiter = SchedulerArbiter()
    scheduled = _candidate("scheduled", kind="scheduled", planned=now - timedelta(minutes=5))
    early = _candidate("early", kind="forced", planned=now + timedelta(hours=2), preempts=True)
    manual = _candidate("manual", kind="manual", planned=now)
    assert arbiter.select([scheduled, early], lease_owner=None).job_id == "early"
    assert arbiter.select([scheduled, early, manual], lease_owner=None).job_id == "manual"


def test_normal_early_execution_still_yields_to_due_scheduled_work():
    now = datetime(2026, 9, 2, 10, 0)
    arbiter = SchedulerArbiter()
    scheduled = _candidate("scheduled", kind="scheduled", planned=now - timedelta(minutes=5))
    early = _candidate("early", kind="forced", planned=now + timedelta(hours=2), preempts=False)
    assert arbiter.select([scheduled, early], lease_owner=None).job_id == "scheduled"


def test_prestart_wait_tracks_longest_continuous_episode_and_threshold():
    tz = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 9, 2, 8, 0, tzinfo=tz)
    occurrence = OccurrencePlanner("Europe/Kyiv").next_for_schedule(_schedule(), now, inclusive=True)
    assert occurrence is not None
    job = JobInstance.from_occurrence(occurrence, now)
    first = now + timedelta(minutes=1)
    job.transition(JobState.WAIT, first)
    job.transition(JobState.PLANNED, first + timedelta(seconds=130))
    job.transition(JobState.WAIT, first + timedelta(minutes=4))
    job.transition(JobState.STARTING, first + timedelta(minutes=4, seconds=30))
    job.transition(JobState.RUNNING, first + timedelta(minutes=4, seconds=31))
    assert job.max_prestart_wait_seconds == 130
    assert job.waited_before_start(120) is True
    assert job.waited_before_start(131) is False
    restored = JobInstance.from_dict(job.to_dict())
    assert restored.max_prestart_wait_seconds == 130
    assert restored.waited_before_start(120) is True


def test_frontend_contracts_show_forecast_preemption_and_wait_marker():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'this._tr("panel.forecast_value")' in panel
    assert '_formatForecastDuration(job.forecast_duration_seconds)' in panel
    assert 'data-force-field="preempts_scheduled"' in panel
    assert 'job.waited_before_start?this._tr("panel.with_wait"):sourceLabel' in panel
    assert ru["panel.force_preempts_scheduled"] == "Важнее плановых"
    assert ru["panel.force_preempts_scheduled_help"] == "Будет отодвигать выполнение плановых заданий и выполняться раньше"
    assert ru["panel.with_wait"] == "С ожиданием"
    force_eval = engine[engine.index("def _evaluate_force_candidates"):engine.index("def _next_future_planned_for_force")]
    assert 'if preempts_scheduled:' in force_eval
    assert '"reason": "preempts_scheduled"' in force_eval
    assert '"override_plan_protection": True' in force_eval
