"""Regression tests for canonical schedule-slot consumption and Active Jobs deduplication.

These tests ensure a schedule revision cannot resurrect an already executed planned slot,
while explicit schedule refresh can still re-arm an early execution when requested.
"""
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
from custom_components.vacuum_schedule.job import JobExecutionSource, JobInstance, JobResult
from custom_components.vacuum_schedule.job_store import JobStore
from custom_components.vacuum_schedule.planner import OccurrencePlanner
from custom_components.vacuum_schedule.schedule import ScheduleDefinition
from custom_components.vacuum_schedule.schedule_refresh import select_schedule_refresh_occurrence

PANEL = MODULE / "frontend" / "panel.js"
ENGINE = MODULE / "scheduler_engine.py"


def _store_without_ha() -> JobStore:
    store = object.__new__(JobStore)
    store.active = {}
    store.history = []
    store.terminal_archive = []
    store.events = []
    store.debug_state = {}
    return store


def _schedule(*, revision: int) -> ScheduleDefinition:
    return ScheduleDefinition.create(
        schedule_id="kitchen",
        revision=revision,
        name="Kitchen",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        dates=[],
        local_time="15:00",
        targets=["zone_kitchen"],
        force_enabled=False,
        force_max_advance_minutes=0,
    )


def _same_slot_occurrences(now: datetime):
    planner = OccurrencePlanner("Europe/Kyiv")
    old = planner.next_for_schedule(_schedule(revision=1), now, inclusive=True)
    new = planner.next_for_schedule(_schedule(revision=2), now, inclusive=True)
    assert old is not None and new is not None
    assert old.planned_start == new.planned_start
    assert old.occurrence_id != new.occurrence_id
    return planner, old, new


def _early_terminal(old_occurrence, now: datetime) -> JobInstance:
    job = JobInstance.from_occurrence(old_occurrence, now)
    job.execution_mode = ExecutionMode.REAL
    job.metadata["force_execution"] = {
        "selected_at": now.isoformat(),
        "committed": True,
    }
    job.finish(JobResult.SUCCESS, "execution_success", now + timedelta(minutes=20))
    assert job.execution_source is JobExecutionSource.FORCE
    return job


def test_schedule_slot_lookup_consumes_same_planned_time_across_revisions():
    """Revision-aware occurrence ids must still share one physical schedule slot."""
    now = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    _planner, old_occurrence, new_occurrence = _same_slot_occurrences(now)
    store = _store_without_ha()
    completed = _early_terminal(old_occurrence, now)
    store.archive(completed)

    assert store.by_occurrence_id(new_occurrence.occurrence_id, ExecutionMode.REAL) is None
    owner = store.by_schedule_slot(
        new_occurrence.schedule_id,
        new_occurrence.planned_start,
        ExecutionMode.REAL,
    )
    assert owner is completed


def test_plain_refresh_does_not_resurrect_cross_revision_early_execution():
    """Without the restore checkbox, refresh skips a future slot already executed early under an older revision."""
    now = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    planner, old_occurrence, new_occurrence = _same_slot_occurrences(now)
    store = _store_without_ha()
    completed = _early_terminal(old_occurrence, now)
    store.archive(completed)
    current_schedule = _schedule(revision=2)

    def consumed_lookup(occurrence):
        return store.by_occurrence_id(occurrence.occurrence_id, ExecutionMode.REAL) or store.by_schedule_slot(
            occurrence.schedule_id,
            occurrence.planned_start,
            ExecutionMode.REAL,
        )

    selected, rearmed_from, restored_early = select_schedule_refresh_occurrence(
        planner,
        current_schedule,
        now,
        consumed_lookup=consumed_lookup,
        restore_early_executed=False,
    )
    assert selected is not None
    assert selected.planned_start > new_occurrence.planned_start
    assert rearmed_from is None
    assert restored_early is False


def test_refresh_checkbox_can_rearm_cross_revision_early_execution():
    """The explicit restore option may deliberately recreate the same future slot under the current revision."""
    now = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    planner, old_occurrence, new_occurrence = _same_slot_occurrences(now)
    store = _store_without_ha()
    completed = _early_terminal(old_occurrence, now)
    store.archive(completed)
    current_schedule = _schedule(revision=2)

    def consumed_lookup(occurrence):
        return store.by_occurrence_id(occurrence.occurrence_id, ExecutionMode.REAL) or store.by_schedule_slot(
            occurrence.schedule_id,
            occurrence.planned_start,
            ExecutionMode.REAL,
        )

    selected, rearmed_from, restored_early = select_schedule_refresh_occurrence(
        planner,
        current_schedule,
        now,
        consumed_lookup=consumed_lookup,
        restore_early_executed=True,
    )
    assert selected is not None
    assert selected.planned_start == new_occurrence.planned_start
    assert selected.occurrence_id == new_occurrence.occurrence_id
    assert rearmed_from is completed
    assert restored_early is True


def test_normal_reconcile_uses_canonical_slot_and_repairs_old_duplicate_projection():
    """The engine must consume by schedule_id+planned_start and remove persisted unstarted duplicate projections."""
    engine = ENGINE.read_text(encoding="utf-8")
    consumed = engine[engine.index("def _occurrence_consumed"):engine.index("def _next_unconsumed_occurrence")]
    assert "self.store.by_schedule_slot(" in consumed
    assert "def _dematerialize_shadowed_schedule_slots" in consumed
    assert 'job.metadata.get("rearmed_by_schedule_refresh")' in consumed
    assert "discard_active_projection(job.job_id)" in consumed


def test_active_jobs_frontend_shows_one_scheduled_row_per_schedule():
    """Active Jobs keeps the current/highest-priority scheduled row while independent manual jobs remain visible."""
    panel = PANEL.read_text(encoding="utf-8")
    helper = panel[panel.index("_visibleActiveJobs(jobs)"):panel.index("_activeJobsHtml(entry)")]
    assert 'if(state==="RUNNING")return 0' in helper
    assert 'if(state==="STARTING")return 1' in helper
    assert 'if(state==="WAIT")return 2' in helper
    assert 'if(state==="PLANNED")return 3' in helper
    assert 'String(job?.origin||"").toUpperCase()!=="SCHEDULED"' in helper
    assert "passthrough.push(job)" in helper
    assert "const jobs = this._visibleActiveJobs(entry?.active_jobs || [])" in panel
