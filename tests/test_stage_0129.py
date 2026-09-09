"""Regression tests for schedule pause is persistent but does not change revision, paused schedule keeps normal calendar planning but disabled does, and schedule pause uses non wait suppression path and force.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from schedule import ScheduleDefinition  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402

PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"


def _schedule(**patch):
    values = dict(
        name="Kitchen",
        enabled=True,
        paused=False,
        weekdays=[0],
        dates=[],
        local_time="18:00",
        targets=["kitchen"],
        execution_window_minutes=120,
    )
    values.update(patch)
    return ScheduleDefinition.create(**values)




def test_schedule_pause_is_persistent_but_does_not_change_revision():
    schedule = _schedule()
    paused = schedule.revised(paused=True)
    assert paused.paused is True
    assert paused.enabled is True
    assert paused.revision == schedule.revision
    roundtrip = ScheduleDefinition.from_dict(paused.to_dict())
    assert roundtrip.paused is True
    assert roundtrip.revision == schedule.revision


def test_paused_schedule_keeps_normal_calendar_planning_but_disabled_does_not():
    planner = OccurrencePlanner("Europe/Kyiv")
    after = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
    paused = _schedule(paused=True)
    disabled = _schedule(enabled=False)
    assert planner.next_for_schedule(paused, after) is not None
    assert planner.next_for_schedule(disabled, after) is None


def test_schedule_pause_uses_non_wait_suppression_path_and_force_exclusion():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    prepare = source[source.index("async def _prepare_job"):source.index("def _force_readiness")]
    assert "self._execution_gate_suppression_reason(now) or self._schedule_pause_suppression_reason(job)" in prepare
    assert "self._suppress_unstarted_zones(job, suppression_reason, now)" in prepare
    assert "self._finish(job, JobResult.SUPPRESSED, suppression_reason, now)" in prepare
    assert "return changed, ()" in prepare
    force_start = source.index("def _evaluate_force_candidates")
    force = source[force_start:source.index("async def _", force_start + 10)]
    assert "or schedule.paused" in force


def test_paused_schedule_rejects_manual_and_early_start_server_side():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    additional = source[source.index("async def async_run_job_additional"):source.index("async def async_start_job_now")]
    early = source[source.index("async def async_start_job_now"):source.index("async def async_skip_job")]
    assert 'if schedule.paused:\n                raise ValueError("schedule_paused")' in additional
    assert 'if schedule.paused:\n                raise ValueError("schedule_paused")' in early


def test_status_payload_exposes_schedule_pause_state():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    block = source[source.index("def _job_status_dict"):source.index("def job_details_payload")]
    assert 'payload["schedule_paused"] = bool(schedule.paused)' in block
    assert 'payload["schedule_enabled"] = bool(schedule.enabled)' in block


def test_now_column_owns_current_physical_execution_controls():
    panel = PANEL.read_text(encoding="utf-8")
    current = panel[panel.index("  _currentExecutionActionsHtml(job) {"):panel.index("  _nowHtml(job) {")]
    row = panel[panel.index("  _jobRowActionsHtml(job) {"):panel.index("  _activeJobsHtml(entry) {")]
    assert 'button("pause"' in current
    assert 'button("resume"' in current
    assert 'button("cancel"' in current
    assert 'panel.pause_current_execution' in current
    assert 'panel.stop_current_execution' in current
    assert 'button("pause"' not in row
    assert 'button("resume"' not in row
    assert 'button("cancel"' not in row


def test_actions_column_owns_schedule_pause_and_occurrence_actions():
    panel = PANEL.read_text(encoding="utf-8")
    row = panel[panel.index("  _jobRowActionsHtml(job) {"):panel.index("  _activeJobsHtml(entry) {")]
    assert 'button("schedule_pause"' in row
    assert 'button("schedule_resume"' in row
    assert 'button("additional_run"' in row
    assert 'button("start_now"' in row
    assert 'button("skip"' in row
    assert 'const runnable=!job?.schedule_paused' in row
    handler = panel[panel.index('if(action==="schedule_pause"||action==="schedule_resume")'):]
    assert 'type:"vacuum_schedule/schedules/set_paused"' in handler


def test_paused_now_status_is_plain_orange_text_without_icon():
    panel = PANEL.read_text(encoding="utf-8")
    now = panel[panel.index("_nowHtml(job)"):panel.index("_resourceStatusText(resource)")]
    assert 'if(job?.schedule_paused)' in now
    paused_line = next(line for line in now.splitlines() if 'job?.schedule_paused' in line)
    assert 'job-now-paused' in paused_line
    assert 'this._mdi(' not in paused_line
    css = panel[panel.index('.job-now-paused'):panel.index('.job-list-header', panel.index('.job-now-paused'))]
    assert 'warning-color' in css


def test_now_typography_inherits_active_table_style():
    panel = PANEL.read_text(encoding="utf-8")
    css = panel[panel.index('.job-now-cell {'):panel.index('.job-list-header {', panel.index('.job-now-cell {'))]
    assert 'font-family:inherit' in css
    assert 'font-size:var(--vs-table-font-size)' in css
    assert '.job-now-main.readiness { font-family:inherit; font-size:inherit; line-height:inherit; font-weight:inherit; }' in css
    assert '.job-now-main b { font:inherit; font-weight:600;' in css


def test_schedule_pause_localization_is_explicit():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ru["panel.schedule_paused"] == "Расписание на паузе"
    assert ru["panel.pause_schedule"] == "Приостановить расписание"
    assert ru["panel.resume_schedule"] == "Возобновить расписание"
    assert en["panel.schedule_paused"] == "Schedule paused"
