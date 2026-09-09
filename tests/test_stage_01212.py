"""Future schedule projection refresh contracts."""
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

PANEL = MODULE / "frontend" / "panel.js"


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


def _store_without_ha() -> JobStore:
    store = object.__new__(JobStore)
    store.active = {}
    store.history = []
    store.terminal_archive = []
    store.events = []
    store.debug_state = {}
    return store




def test_refresh_ui_uses_agreed_wording_and_opt_in_checkbox():
    ru = (MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert '"panel.refresh_schedule": "Обновить расписание"' in ru
    assert '"panel.refresh_future_jobs": "Обновить будущие задания?"' in ru
    assert '"panel.restore_early_executed_jobs": "Восстановить досрочно выполненные задания"' in ru
    assert "_confirmActionWithCheckbox" in panel
    assert 'restore_early_executed:!!choice.checked' in panel
    assert 'type:"vacuum_schedule/scheduler/rebuild_schedule"' in panel
    assert 'class="ghost rebuild-schedule"' in panel


def test_rebuild_endpoint_and_engine_are_explicit_and_do_not_archive_removed_projection():
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/scheduler/rebuild_schedule"' in frontend
    assert 'vol.Optional("restore_early_executed", default=False): bool' in frontend
    assert "async_rebuild_schedule_projection" in frontend
    assert "async_register_command(hass, websocket_scheduler_rebuild_schedule)" in frontend
    rebuild = engine[engine.index("async def async_rebuild_schedule_projection"):engine.index("async def async_reconcile_current_timeline")]
    assert "discard_active_projection" in rebuild
    assert "self.store.archive(job)" not in rebuild
    assert '"timeline_reconciled"' not in rebuild
    assert "job.execution_attempts or job.state in (JobState.STARTING, JobState.RUNNING)" in rebuild
    assert "job.origin is not JobOrigin.SCHEDULED" in rebuild


def test_discard_projection_removes_trace_without_terminal_history():
    store = _store_without_ha()
    planner = OccurrencePlanner("Europe/Kyiv")
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    occurrence = planner.next_for_schedule(_schedule(), now, inclusive=True)
    assert occurrence is not None
    job = JobInstance.from_occurrence(occurrence, now)
    job.execution_mode = ExecutionMode.REAL
    store.active[job.job_id] = job
    store.events = [{"job_id": job.job_id, "event_type": "created"}, {"job_id": "other", "event_type": "created"}]

    removed = store.discard_active_projection(job.job_id)
    assert removed is job
    assert job.job_id not in store.active
    assert store.history == []
    assert store.terminal_archive == []
    assert store.events == [{"job_id": "other", "event_type": "created"}]


def test_rearm_generation_preserves_same_canonical_occurrence_twice():
    store = _store_without_ha()
    planner = OccurrencePlanner("Europe/Kyiv")
    now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    occurrence = planner.next_for_schedule(_schedule(), now, inclusive=True)
    assert occurrence is not None

    first = JobInstance.from_occurrence(occurrence, now)
    first.execution_mode = ExecutionMode.REAL
    first.metadata["force_execution"] = {"selected_at": now.isoformat(), "committed": True}
    first.finish(JobResult.SUCCESS, "execution_success", now + timedelta(minutes=10))
    assert first.execution_source is JobExecutionSource.FORCE
    store.archive(first)

    second = JobInstance.from_occurrence(occurrence, now + timedelta(minutes=11))
    second.execution_mode = ExecutionMode.REAL
    second.metadata["occurrence_rearm_generation"] = store.next_occurrence_rearm_generation(
        occurrence.occurrence_id, ExecutionMode.REAL
    )
    assert second.metadata["occurrence_rearm_generation"] == 1
    store.add_active(second)
    second.finish(JobResult.SUCCESS, "execution_success", now + timedelta(minutes=20))
    store.archive(second)

    matching = [job for job in store.terminal_archive if job.occurrence_id == occurrence.occurrence_id]
    assert len(matching) == 2
    assert {store._occurrence_rearm_generation(job) for job in matching} == {0, 1}


def test_rebuild_helper_delegates_consumption_semantics_to_refresh_policy():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    helper = engine[engine.index("def _rebuild_occurrence_candidate"):engine.index("async def async_rebuild_schedule_projection")]
    assert "select_schedule_refresh_occurrence" in helper
    assert "consumed_lookup" in helper
    assert "self.store.by_occurrence_id" in helper
