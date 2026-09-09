"""Regression tests for occurrence materializes as planned snapshot, terminal finish is idempotent and immutable, and wait tracks current continuous interval.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE_DIR))

from job import JobInstance, JobReason, JobResult, JobState  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402
from preflight import (  # noqa: E402
    PreflightDecision,
    PreflightPhase,
    SimulationPreflightProvider,
)
from schedule import ScheduleDefinition  # noqa: E402


PANEL_EN = json.loads((MODULE_DIR / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
PANEL_RU = json.loads((MODULE_DIR / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))

def _panel_key(ru: str, en: str | None = None) -> str:
    for key, value in PANEL_RU.items():
        if value == ru and (en is None or PANEL_EN.get(key) == en):
            return key
    raise AssertionError(f"Missing localization pair: {ru!r} / {en!r}")


class Stage040ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = ZoneInfo("Europe/Kyiv")
        self.schedule = ScheduleDefinition.create(
            schedule_id="schedule-a",
            revision=1,
            name="Kitchen",
            enabled=True,
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            dates=[],
            local_time="20:00",
            targets=["kitchen"],
            prewarning_minutes=15,
            execution_window_minutes=120,
        )
        self.planner = OccurrencePlanner(self.zone)
        self.now = datetime(2026, 8, 11, 19, 0, tzinfo=self.zone)
        self.occurrence = self.planner.next_for_schedule(self.schedule, self.now)
        assert self.occurrence is not None

    def test_occurrence_materializes_as_planned_snapshot(self) -> None:
        job = JobInstance.from_occurrence(self.occurrence, self.now)
        self.assertEqual(job.state, JobState.PLANNED)
        self.assertTrue(job.simulation)
        self.assertEqual(job.schedule_revision, 1)
        self.assertEqual(job.targets, ("kitchen",))
        self.assertEqual(job.warning_at.hour, 19)
        self.assertEqual(job.warning_at.minute, 45)

    def test_terminal_finish_is_idempotent_and_immutable(self) -> None:
        job = JobInstance.from_occurrence(self.occurrence, self.now)
        changed = job.finish(
            JobResult.FAILED, JobReason.DEADLINE_EXPIRED.value, self.now
        )
        self.assertTrue(changed)
        generation = job.transition_generation
        changed_again = job.finish(
            JobResult.SUCCESS, JobReason.SIMULATED_SUCCESS.value, self.now + timedelta(seconds=1)
        )
        self.assertFalse(changed_again)
        self.assertEqual(job.result, JobResult.FAILED)
        self.assertEqual(job.reason_code, JobReason.DEADLINE_EXPIRED.value)
        self.assertEqual(job.transition_generation, generation)
        job.transition(JobState.RUNNING, self.now + timedelta(seconds=2))
        self.assertEqual(job.state, JobState.FINISHED)

    def test_wait_tracks_current_continuous_interval(self) -> None:
        job = JobInstance.from_occurrence(self.occurrence, self.now)
        first = self.now + timedelta(hours=1)
        second = first + timedelta(seconds=2)
        job.transition(JobState.WAIT, first)
        job.transition(JobState.PLANNED, first + timedelta(seconds=1))
        self.assertIsNone(job.first_wait_at)
        job.transition(JobState.WAIT, second)
        self.assertEqual(job.first_wait_at, second)
        self.assertEqual(job.wait_cycle, 2)

    def test_serialization_roundtrip(self) -> None:
        job = JobInstance.from_occurrence(self.occurrence, self.now)
        job.blockers = ("room_busy",)
        job.current_blockers = ("room_busy",)
        job.current_preflight_decision = PreflightDecision.WAIT.value
        job.advisory_blockers = ("battery_low",)
        job.advisory_preflight_decision = PreflightDecision.WAIT.value
        job.advisory_checked_at = self.now + timedelta(minutes=45)
        restored = JobInstance.from_dict(job.to_dict())
        self.assertEqual(restored.to_dict(), job.to_dict())


class Stage040PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        zone = ZoneInfo("Europe/Kyiv")
        schedule = ScheduleDefinition.create(
            schedule_id="schedule-a",
            revision=1,
            name="Kitchen",
            enabled=True,
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            dates=[],
            local_time="20:00",
            targets=["kitchen"],
        )
        planner = OccurrencePlanner(zone)
        now = datetime(2026, 8, 11, 19, 0, tzinfo=zone)
        occurrence = planner.next_for_schedule(schedule, now)
        assert occurrence is not None
        self.job = JobInstance.from_occurrence(occurrence, now)
        self.provider = SimulationPreflightProvider()

    def test_pass_without_blockers(self) -> None:
        report = self.provider.evaluate(self.job, PreflightPhase.AUTHORITATIVE)
        self.assertEqual(report.decision, PreflightDecision.PASS)

    def test_wait_blocker(self) -> None:
        self.provider.set_blocker("room_busy", True, self.job.job_id)
        report = self.provider.evaluate(self.job, PreflightPhase.AUTHORITATIVE)
        self.assertEqual(report.decision, PreflightDecision.WAIT)
        self.assertEqual(report.blockers, ("room_busy",))

    def test_global_disable_is_fail_and_dominates_wait(self) -> None:
        self.provider.set_blocker("room_busy", True, self.job.job_id)
        self.provider.set_blocker("global_disabled", True)
        report = self.provider.evaluate(self.job, PreflightPhase.AUTHORITATIVE)
        self.assertEqual(report.decision, PreflightDecision.FAIL)
        self.assertIn("global_disabled", report.blockers)

    def test_clear_all_removes_global_and_per_job_blockers(self) -> None:
        self.provider.set_blocker("vacuum_busy", True)
        self.provider.set_blocker("room_busy", True, self.job.job_id)
        self.provider.clear_all()
        report = self.provider.evaluate(self.job, PreflightPhase.AUTHORITATIVE)
        self.assertEqual(report.decision, PreflightDecision.PASS)
        self.assertEqual(report.blockers, ())


class Stage040ReleaseBoundaryTests(unittest.TestCase):
    def test_scheduler_engine_has_no_physical_executor_call(self) -> None:
        text = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        forbidden = (
            "hass.services.async_call",
            "executor.async_start",
            "executor.async_stop",
            "executor.async_pause",
            "executor.async_return_home",
            "vacuum.start",
            "vacuum.send_command",
        )
        for needle in forbidden:
            self.assertNotIn(needle, text)

    def test_frontend_contains_selected_three_view_design(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for token in ('"status"', '"schedules"', '"testing"', "SIMULATION"):
            self.assertIn(token, text)

    def test_operational_summary_cards_are_single_line(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn(".summary-card {", text)
        self.assertIn("flex-direction:row", text)
        self.assertIn("white-space:nowrap", text)
        self.assertNotIn("flex-direction:column; gap:6px", text)

    def test_all_scheduler_states_have_distinct_visual_classes(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for css_class in (
            ".state-PLANNED",
            ".state-WAIT",
            ".state-STARTING",
            ".state-RUNNING",
            ".state-FINISHED-SUCCESS",
            ".state-FINISHED-FAILED",
        ):
            self.assertIn(css_class, text)
        self.assertIn("_stateChipClass(job)", text)
        self.assertIn('job.result === "SUCCESS"', text)
        self.assertIn('job.result === "FAILED"', text)

    def test_history_uses_localized_colored_result_chips(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("_historyResultHtml(job.result)", text)
        self.assertIn('`common.job_result.${value}`', text)
        self.assertEqual(PANEL_RU["common.job_result.SUCCESS"], "Успешно")
        self.assertEqual(PANEL_RU["common.job_result.FAILED"], "Ошибка")

    def test_job_details_are_user_facing_before_technical_section(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for key in (
            "panel.scheduled_start",
            "panel.pre_start_warning",
            "panel.latest_allowed_start",
            "panel.current_readiness_to_start",
            "panel.preliminary_check",
            "panel.cleaning_targets",
            "panel.cleaning_settings",
        ):
            self.assertIn(f'this._tr("{key}")', text)
        for leaked in (
            'this._t("Occurrence", "Occurrence")',
            'this._t("Текущие блокеры", "Current blockers")',
            'this._t("Advisory blockеры", "Advisory blockers")',
            '<b>Deadline</b>',
            'Object.entries(job.cleaning_params',
        ):
            self.assertNotIn(leaked, text)

    def test_job_details_resolve_targets_and_hide_raw_target_ids(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        backend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        self.assertIn("job.targets_display", text)
        self.assertIn("_jobTargetLabels(job)", text)
        self.assertIn('job["targets_display"] = labels', backend)
        self.assertIn("_async_robot_segment_options", backend)

    def test_user_summary_does_not_expose_wait_run_or_scheduler_jargon_in_russian(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertNotIn('<small>WAIT ', text)
        self.assertNotIn(' · RUN ', text)
        self.assertIn('this._tr("panel.execution")', text)
        self.assertIn('this._tr("panel.waiting")', text)
        self.assertIn('this._tr("panel.running")', text)

    def test_relative_datetime_labels_refresh_at_scheduler_midnight(self) -> None:
        text = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for key, label in (
            ("common.relative.day_before_yesterday", "позавчера"),
            ("common.relative.yesterday", "вчера"),
            ("common.relative.today", "сегодня"),
            ("common.relative.tomorrow", "завтра"),
            ("common.relative.day_after_tomorrow", "послезавтра"),
        ):
            self.assertEqual(PANEL_RU[key], label)
            self.assertIn(f'this._tr("{key}")', text)
        self.assertIn("_dateOrdinal", text)
        self.assertIn("_schedulerOffsetSeconds", text)
        self.assertIn("_scheduleMidnightRefresh", text)
        self.assertIn("_zonedMidnightEpochMs", text)
        self.assertIn('timeZone: this._timeZone()', text)
        self.assertIn("this._clearMidnightRefresh();", text)

    def test_multi_entry_navigation_uses_home_assistant_config_entry_context(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        backend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        self.assertIn('params.get("config_entry")', panel)
        self.assertIn('query.set("config_entry", entryId)', panel)
        self.assertIn('this._tr("panel.robot_vacuum")', panel)
        self.assertIn('vacuum_schedule.last_config_entry', panel)
        self.assertEqual(PANEL_RU['panel.vacuum_schedule'], 'Планировщик уборки')
        self.assertIn('this._tr("panel.vacuum_schedule")', panel)
        self.assertIn('visibleEntries = entries.filter((entry) => entry.entry_id === selectedId)', panel)
        self.assertIn('vacuum-schedule-panel-0635', panel)
        self.assertIn('vacuum-schedule-panel-0635', backend)

    def test_hybrid_preflight_keeps_snapshot_and_live_readiness_separate(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        job = (MODULE_DIR / "job.py").read_text(encoding="utf-8")
        for token in (
            "current_blockers",
            "current_preflight_decision",
            "advisory_preflight_decision",
            "advisory_checked_at",
        ):
            self.assertIn(token, job)
        self.assertIn("_capture_advisory_snapshot", engine)
        self.assertIn("_update_current_preflight", engine)
        self.assertIn("if job.advisory_checked_at is not None", engine)
        self.assertIn('this._tr("panel.now_short")', panel)
        self.assertIn('this._tr("panel.check")', panel)
        self.assertIn('this._tr("panel.ready_to_start")', panel)
        self.assertIn("job.current_blockers", panel)
        self.assertIn("job.advisory_blockers", panel)
        self.assertIn("job.advisory_checked_at", panel)
        self.assertNotIn('this._t("Нет препятствий", "No blockers")', panel)

    def test_editor_url_keeps_config_entry_context_explicit(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('new URLSearchParams({ config_entry: entryId, editor: "1" })', panel)
        self.assertIn('this._replaceContextUrl(this._schedulerEntryId);', panel)

    def test_testing_view_exposes_confirmed_dry_run_reset(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        backend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        engine = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        store = (MODULE_DIR / "job_store.py").read_text(encoding="utf-8")
        self.assertIn('this._tr("panel.reset_dry_run")', panel)
        self.assertIn("_confirmAction", panel)
        self.assertNotIn("window.confirm", panel)
        self.assertIn("vacuum_schedule/debug/reset_dry_run", panel)
        self.assertIn("debug/reset_dry_run", backend)
        self.assertIn("async_reset_dry_run", engine)
        self.assertIn("clear_dry_run_data", store)
        self.assertIn("self.clock.reset()", engine)
        self.assertIn("self.overrides.clear()", engine)
        self.assertIn("await self.async_reconcile()", engine)

    def test_search_select_filter_hides_non_matching_options_visually(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        self.assertIn('option.hidden=!match', panel)
        self.assertIn('button.search-select-option[hidden] { display:none !important; }', panel)
        self.assertIn('String(option.dataset.search||"").includes(query)', panel)

    def test_active_jobs_have_explicit_column_headers_and_mobile_labels(self) -> None:
        panel = (MODULE_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
        for key in (
            "panel.job",
            "panel.now_short",
            "panel.planned",
            "panel.latest_start",
            "panel.early_from",
            "panel.actions",
        ):
            self.assertIn(f'this._tr("{key}")', panel)
        self.assertIn('class="job-list-header"', panel)
        active = panel[panel.index('  _activeJobsHtml(entry) {'):panel.index('\n  _executionModeLabel(mode) {')]
        self.assertNotIn('this._tr("panel.state")', active)
        self.assertNotIn('this._tr("panel.readiness_short")', active)
        self.assertIn('data-label=', panel)
        self.assertIn('content:attr(data-label)', panel)
        self.assertIn('vacuum-schedule-panel-0635', panel)


if __name__ == "__main__":
    unittest.main()
