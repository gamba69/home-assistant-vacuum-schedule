"""Regression tests for schedule list has no additional run action, active table uses compact planned and relative windows, and relative window format is signed hh mm.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"




def test_schedule_list_has_no_additional_run_action():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _listHtml() {")
    end = panel.index("\n  _stateLabel(value) {", start)
    block = panel[start:end]
    assert 'schedule-run-now' not in block
    assert 'schedule-toggle' in block
    assert 'class="primary icon-only compact-icon-action edit"' in block
    assert 'class="danger icon-only compact-icon-action delete"' in block


def test_active_table_uses_compact_planned_and_relative_windows():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _activeJobsHtml(entry) {")
    end = panel.index("\n  _executionModeLabel(mode) {", start)
    block = panel[start:end]
    assert 'this._formatPlannedDateTime(job.planned_start)' in block
    assert 'this._activeLatestCell(job)' in block
    assert 'this._activeEarlyCell(job)' in block
    assert 'this._formatDateTime(job.deadline_at)' not in block
    assert 'this._formatDateTime(job.force.window_start)' not in block


def test_relative_window_format_is_signed_hh_mm():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._formatHoursMinutes(minutes, "+")' in panel
    assert 'this._formatHoursMinutes(minutes, "-")' in panel
    assert 'String(hours).padStart(2,"0")' in panel
    assert 'String(mins).padStart(2,"0")' in panel


def test_today_is_omitted_only_by_active_planned_formatter():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _formatPlannedDateTime(value) {")
    end = panel.index("\n  _formatHoursMinutes", start)
    block = panel[start:end]
    assert 'this._dateOrdinal(d) === this._dateOrdinal(this._displayNow())' in block
    assert 'return this._formatTimeOnly(value)' in block
    assert 'return this._formatDateTime(value)' in block


def test_editor_places_hh_mm_timing_block_immediately_after_schedule_time():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _editorHtml() {")
    end = panel.index("\n  async _debugAction", start)
    block = panel[start:end]
    local_time = block.index('data-field="local_time" type="time"')
    timing = block.index('${this._scheduleTimingHtml()}')
    cleaning = block.index('this._tr("panel.base_cleaning_profile")')
    assert local_time < timing < cleaning
    assert 'data-field="execution_window_minutes" type="number"' not in block


def test_editor_uses_hh_mm_for_window_and_early_offset_and_stores_minutes():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'data-duration-minutes="execution_window_minutes" type="text"' in panel
    assert 'data-duration-minutes="force_max_advance_minutes" type="text"' in panel
    assert 'pattern="\\\\d{1,3}:[0-5]\\\\d"' in panel
    assert 'this._form[key]=parsed' in panel
    assert '_parseDurationHm(value, { min = 0, max = 10080 } = {})' in panel
    assert 'key==="execution_window_minutes"?1:0' in panel


def test_force_conditions_are_separate_from_early_time_setting():
    panel = PANEL.read_text(encoding="utf-8")
    force_start = panel.index("  _forceEditorHtml() {")
    timing_start = panel.index("  _scheduleTimingHtml() {")
    force_block = panel[force_start:timing_start]
    assert 'this._tr("panel.early_conditions")' in force_block
    assert 'data-force-field="priority"' in force_block
    assert 'max_advance_hours' not in force_block
    assert 'data-force-field="enabled"' not in force_block


def test_ru_labels_match_compact_timing_semantics():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ru["panel.latest_start"] == "Не позднее"
    assert ru["panel.early_from"] == "Досрочно"
    assert ru["panel.execution_window_hm"] == "Окно запуска, ч:мин"
    assert ru["panel.maximum_advance_hm"] == "Максимально досрочно, ч:мин"
    assert ru["panel.early_conditions"] == "Условия досрочного выполнения"
    assert en["panel.latest_start"] == "No later than"
