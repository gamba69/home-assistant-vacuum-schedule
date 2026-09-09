"""Flexible weekly schedule contracts for Vacuum Schedule 0.10.0."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


def _flex(**changes):
    payload = dict(
        schedule_id="flex-a",
        revision=3,
        name="Flexible",
        enabled=True,
        weekdays=[0, 1, 2, 3, 4, 5],
        dates=[],
        local_time="09:00",
        targets=["kitchen"],
        cleaning_params={
            "cleaning_mode": "vac_and_mop",
            "cleaning_mode_entity_id": "select.cleaning_mode",
            "fan_mode": "balanced",
            "passes": 1,
            "water_mode": "standard",
            "water_mode_entity_id": "select.water_mode",
        },
        weekday_times={
            "mon": "15:00",
            "tue": "17:00",
            "wed": "15:00",
            "thu": "17:00",
            "fri": "15:00",
            "sat": "11:00",
        },
        weekday_overrides={
            "tue": {
                "cleaning_mode": "vacuum",
                "cleaning_mode_entity_id": "select.cleaning_mode",
            },
            "thu": {
                "cleaning_mode": "vacuum",
                "cleaning_mode_entity_id": "select.cleaning_mode",
            },
            "sat": {"passes": 2},
        },
    )
    payload.update(changes)
    return ScheduleDefinition.create(**payload)




def test_legacy_uniform_schedule_is_migrated_in_memory_without_semantic_change():
    restored = ScheduleDefinition.from_dict({
        "schedule_id": "legacy", "revision": 7, "name": "Legacy", "enabled": True,
        "weekdays": [0, 2, 4], "dates": [], "local_time": "15:00",
        "target_type": "cleaning_zones", "targets": ["kitchen"],
        "cleaning_params": {"passes": 1},
    })
    assert restored.revision == 7
    assert {day: value.isoformat(timespec="minutes") for day, value in restored.weekday_times.items()} == {
        0: "15:00", 2: "15:00", 4: "15:00"
    }
    assert restored.weekday_overrides == {}


def test_weekly_days_have_independent_start_times():
    planner = OccurrencePlanner("Europe/Kyiv")
    schedule = _flex()
    monday = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    first = planner.next_for_schedule(schedule, monday, inclusive=True)
    second = planner.next_for_schedule(schedule, first.planned_start, inclusive=False)
    assert first.planned_start.isoformat() == "2026-08-24T15:00:00+03:00"
    assert second.planned_start.isoformat() == "2026-08-25T17:00:00+03:00"


def test_occurrence_snapshots_only_its_weekday_corrections():
    planner = OccurrencePlanner("Europe/Kyiv")
    schedule = _flex()
    monday_evening = datetime(2026, 8, 24, 18, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    tuesday = planner.next_for_schedule(schedule, monday_evening, inclusive=True)
    assert tuesday.planned_start.date().isoformat() == "2026-08-25"
    assert tuesday.cleaning_params["cleaning_mode"] == "vacuum"
    assert tuesday.cleaning_params["cleaning_mode_entity_id"] == "select.cleaning_mode"
    assert tuesday.cleaning_params["fan_mode"] == "balanced"
    assert tuesday.cleaning_params["passes"] == 1


def test_saturday_pass_override_does_not_rewrite_base_profile():
    schedule = _flex()
    saturday = schedule.effective_cleaning_params_for_date(date(2026, 8, 29))
    monday = schedule.effective_cleaning_params_for_date(date(2026, 8, 24))
    assert saturday["passes"] == 2
    assert monday["passes"] == 1
    assert schedule.cleaning_params["passes"] == 1


def test_flexible_fields_roundtrip_and_change_execution_revision():
    schedule = _flex()
    restored = ScheduleDefinition.from_dict(schedule.to_dict())
    assert restored.weekday_times == schedule.weekday_times
    assert restored.weekday_overrides == schedule.weekday_overrides
    changed = schedule.revised(weekday_times={**schedule.to_dict()["weekday_times"], "1": "18:00"})
    assert changed.revision == schedule.revision + 1


def test_uniform_legacy_local_time_edit_still_updates_all_weekdays():
    schedule = ScheduleDefinition.create(
        schedule_id="uniform", revision=2, name="Uniform", enabled=True,
        weekdays=[0, 2, 4], dates=[], local_time="15:00", targets=["kitchen"],
    )
    changed = schedule.revised(local_time="16:30")
    assert changed.revision == 3
    assert {value.isoformat(timespec="minutes") for value in changed.weekday_times.values()} == {"16:30"}


def test_specific_date_without_weekday_uses_legacy_date_time_fallback():
    schedule = ScheduleDefinition.create(
        schedule_id="date", revision=1, name="Date", enabled=True,
        weekdays=[], dates=["2026-08-30"], local_time="12:45", targets=["kitchen"],
        weekday_times={},
    )
    occurrence = OccurrencePlanner("Europe/Kyiv").next_for_schedule(
        schedule, datetime(2026, 8, 30, 8, 0, tzinfo=ZoneInfo("Europe/Kyiv")), inclusive=True
    )
    assert occurrence.planned_start.isoformat() == "2026-08-30T12:45:00+03:00"


def test_frontend_exposes_weekly_schedule_and_sparse_daily_corrections():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    for token in (
        "_weekdayScheduleRows()", "_dailyOverrideRows()", "data-weekday-enabled",
        "data-weekday-time", "data-day-override", "panel.weekly_schedule",
        "panel.daily_corrections", "panel.use_base_profile",
    ):
        assert token in panel


def test_entry_schema_minor_migration_materializes_flexible_weekly_fields():
    const = (MODULE / "const.py").read_text(encoding="utf-8")
    migration = (MODULE / "migrations.py").read_text(encoding="utf-8")
    assert "ENTRY_MINOR_VERSION: Final = 6" in const
    assert 'schedule["weekday_times"]' in migration
    assert 'schedule["weekday_overrides"] = {}' in migration
