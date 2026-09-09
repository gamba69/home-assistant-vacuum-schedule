"""0.12.54 manual-start semantics, action API and schedule-ID visibility."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


def _schedule() -> ScheduleDefinition:
    return ScheduleDefinition.create(
        name="Mixed",
        enabled=True,
        paused=False,
        weekdays=[0, 1],
        dates=[],
        local_time="18:00",
        targets=["kitchen"],
        cleaning_params={
            "cleaning_mode": "vac_and_mop",
            "fan_mode": "turbo",
            "mop_mode": "standard",
            "water_mode": "medium",
            "passes": 1,
        },
        weekday_overrides={
            0: {"cleaning_mode": "vacuum", "fan_mode": "quiet"},
            1: {"cleaning_mode": "vac_and_mop", "water_mode": "high"},
        },
    )


def _engine_source() -> str:
    return (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")


def _method(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start) + len(start))]


def test_weekday_profiles_are_distinct_for_manual_vs_moved_occurrence_contract():
    schedule = _schedule()
    monday = datetime(2026, 9, 7, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    planner = OccurrencePlanner("Europe/Kyiv")
    future = planner.next_for_schedule(schedule, monday)
    assert future is not None
    assert future.planned_start.date().isoformat() == "2026-09-07"  # same-day next occurrence

    monday_profile = schedule.effective_cleaning_params_for_date(monday.date())
    tuesday_profile = schedule.effective_cleaning_params_for_date(datetime(2026, 9, 8).date())
    assert monday_profile["cleaning_mode"] == "vacuum"
    assert monday_profile["fan_mode"] == "quiet"
    assert monday_profile.get("mop_mode", "") == ""
    assert monday_profile.get("water_mode", "") == ""
    assert tuesday_profile["cleaning_mode"] == "vac_and_mop"
    assert tuesday_profile["water_mode"] == "high"
    assert monday_profile != tuesday_profile


def test_additional_run_resolves_current_local_day_profile_not_future_job_snapshot():
    source = _engine_source()
    schedule_run = _method(source, "async def async_run_schedule_now", "async def async_run_job_additional")
    job_run = _method(source, "async def async_run_job_additional", "async def async_run_schedule_early")

    for block in (schedule_run, job_run):
        assert "local_date = self._scheduler_local_date(now)" in block
        assert "schedule.effective_cleaning_params_for_date(local_date)" in block
        assert "cleaning_params=dict(effective_params)" in block
        assert 'job.metadata["manual_profile_date"] = local_date.isoformat()' in block

    assert "cleaning_params=dict(template.cleaning_params)" not in job_run
    assert "schedule_revision=schedule.revision" in job_run
    assert "targets=tuple(schedule.targets)" in job_run


def test_manual_early_run_keeps_materialized_occurrence_profile():
    source = _engine_source()
    early = _method(source, "async def async_run_schedule_early", "async def async_smart_run_schedule")
    start_now = _method(source, "async def async_start_job_now", "async def async_skip_job")

    assert "_pending_scheduled_job_for_schedule" in early
    assert "future_only=True" in early
    assert "return await self.async_start_job_now" in early
    assert "effective_cleaning_params_for_date" not in early
    assert "job.cleaning_params =" not in start_now
    assert "cleaning_params=" not in start_now


def test_smart_run_prefers_pending_current_day_and_falls_back_to_additional():
    source = _engine_source()
    smart = _method(source, "async def async_smart_run_schedule", "async def async_start_job_now")
    assert "today = self._scheduler_local_date(now)" in smart
    assert "local_date=today" in smart
    assert 'job.metadata["smart_run_resolution"] = "scheduled_occurrence"' in smart
    assert "job = await self.async_start_job_now(" in smart
    assert 'job.metadata["smart_run_resolution"] = "additional_run"' in smart
    assert "job = await self.async_run_schedule_now(" in smart
    assert 'return {"resolution": "scheduled_occurrence", "job": job}' in smart
    assert 'return {"resolution": "additional_run", "job": job}' in smart


def test_home_assistant_action_surface_covers_runtime_ui_controls():
    init = (MODULE / "__init__.py").read_text(encoding="utf-8")
    services = (MODULE / "services.yaml").read_text(encoding="utf-8")
    for name in (
        "additional_run",
        "run_early",
        "smart_run",
        "start_job_now",
        "skip_job",
        "cancel_job",
        "pause_job",
        "resume_job",
        "recheck_job",
        "set_schedule_enabled",
        "set_schedule_paused",
        "set_execution_gate",
        "rebuild_schedule",
    ):
        assert f"{name}:" in services
    for token in (
        "SERVICE_ADDITIONAL_RUN",
        "SERVICE_RUN_EARLY",
        "SERVICE_SMART_RUN",
        "SERVICE_PAUSE_JOB",
        "SERVICE_RESUME_JOB",
        "SERVICE_SET_SCHEDULE_PAUSED",
        "SERVICE_REBUILD_SCHEDULE",
    ):
        assert token in init
    assert "supports_response=SupportsResponse.OPTIONAL" in init


def test_action_translations_explain_the_two_profile_dates_and_smart_resolution():
    base = MODULE
    ru = json.loads((base / "translations" / "ru.json").read_text(encoding="utf-8"))["services"]
    assert ru["additional_run"]["name"] == "Дополнительный запуск"
    assert "текущ" in ru["additional_run"]["description"].lower()
    assert ru["run_early"]["name"] == "Раньше вручную"
    assert "исходного дня" in ru["run_early"]["description"].lower()
    assert ru["smart_run"]["name"] == "Смарт-запуск"
    assert "если за текущий" in ru["smart_run"]["description"].lower()


def test_schedule_table_exposes_full_schedule_id_for_automations():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    for locale in ("en", "ru", "uk"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["panel.schedule_id"]
    # Since 0.12.56 the stable ID is deliberately kept in the Job cell instead
    # of consuming a separate desktop column.
    assert 'class="schedule-id-line"' in panel
    assert '<span>ID:</span> <code' in panel
    assert '${this._escape(row.schedule_id)}</code>' in panel
    assert 'class="schedule-id-cell"' not in panel
    assert "user-select:all" in panel
