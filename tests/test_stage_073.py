"""Regression contracts for Vacuum Schedule 0.7.4 planned-start recovery."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_explicit_recovery_uses_planned_start_only():
    engine = (MODULE / "scheduler_engine.py").read_text()
    section = engine[
        engine.index("def _timeline_candidate_occurrence"):
        engine.index("def _dematerialize_stale_future_jobs")
    ]
    assert "current_relevant_for_schedule" not in section
    assert "next_for_schedule(schedule, now, inclusive=True)" in section
    assert "planned_start >= now" in section
    assert "deadline_at" in section
    assert "must never make a past occurrence eligible" in section


def test_explicit_dry_run_recovery_always_starts_new_generation():
    engine = (MODULE / "scheduler_engine.py").read_text()
    section = engine[
        engine.index("async def async_reconcile_current_timeline"):
        engine.index("async def async_reconcile(self)")
    ]
    assert "if self.execution_mode is ExecutionMode.DRY_RUN:" in section
    assert "self._dry_run_timeline_generation += 1" in section
    assert "if reset_test_clock:" in section
    assert "self.clock.reset()" in section


def test_recovery_confirmation_explains_past_occurrence_rule_in_both_languages():
    import json
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text())
        text = data["panel.restore_current_jobs_confirm"]
        if lang == "en":
            assert "scheduled start" in text
            assert "past occurrence" in text
            assert "execution window" in text
        else:
            assert "время запуска" in text
            assert "Прошедший occurrence" in text
            assert "окно запуска" in text




def test_planner_next_start_is_tomorrow_not_past_window_or_day_after_tomorrow():
    import importlib
    import sys
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    module_path = str(MODULE)
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    schedule_mod = importlib.import_module("schedule")
    planner_mod = importlib.import_module("planner")

    schedule = schedule_mod.ScheduleDefinition(
        schedule_id="bedroom",
        revision=1,
        name="Bedroom",
        enabled=True,
        weekdays=(0, 1, 2, 3, 4, 5, 6),
        dates=(),
        local_time=time(10, 0),
        target_type="cleaning_zones",
        targets=("bedroom",),
        prewarning_minutes=15,
        execution_window_minutes=120,
    )
    now = datetime(2026, 8, 17, 11, 51, tzinfo=ZoneInfo("Europe/Kyiv"))
    occurrence = planner_mod.OccurrencePlanner("Europe/Kyiv").next_for_schedule(
        schedule, now, inclusive=True
    )
    assert occurrence is not None
    assert occurrence.planned_start == datetime(
        2026, 8, 18, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv")
    )
