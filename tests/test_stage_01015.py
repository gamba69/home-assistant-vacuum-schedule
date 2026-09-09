"""Regression tests for active jobs use requested columns and force window start, active job labels are human facing, and schedule rows show revision inline status below and no.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"




def test_active_jobs_use_requested_columns_and_force_window_start():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _activeJobsHtml(entry) {")
    end = panel.index("\n  _executionModeLabel(mode) {", start)
    body = panel[start:end]
    expected = [
        'this._tr("panel.job")',
        'this._tr("panel.now_short")',
        'this._tr("panel.planned")',
        'this._tr("panel.latest_start")',
        'this._tr("panel.early_from")',
        'this._tr("panel.actions")',
    ]
    positions = [body.index(item) for item in expected]
    assert positions == sorted(positions)
    assert 'this._activeLatestCell(job)' in body
    assert 'this._activeEarlyCell(job)' in body
    assert 'this._formatPlannedDateTime(job.planned_start)' in body
    assert 'this._tr("panel.state")' not in body
    assert 'this._tr("panel.readiness_short")' not in body


def test_active_job_labels_are_human_facing():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ru["panel.job"] == "Задание"
    assert ru["panel.early_from"] == "Досрочно"
    assert ru["panel.planned"] == "Запланировано"
    assert ru["panel.now_short"] == "Сейчас"
    assert ru["panel.readiness_short"] == "Готовность"
    assert en["panel.job"] == "Task"


def test_schedule_rows_show_revision_inline_status_below_and_no_target_type_caption():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _listHtml() {")
    end = panel.index("\n  _stateLabel(value) {", start)
    body = panel[start:end]
    assert 'class="schedule-name-line"' in body
    assert 'class="schedule-revision">(${this._tr("panel.revision_short")} ${row.revision})' in body
    assert 'class="schedule-row-state"' in body
    target_cell = body[body.index('<td data-label="${this._tr("panel.targets")}">'):body.index('<td data-label="${this._tr("panel.cleaning")}">')]
    assert '_targetTypeLabel(row.target_type)' not in target_cell
    assert 'this._tr("panel.job")' in body


def test_schedule_override_summary_uses_singular_correction_label():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.correction"] == "Корректировка"
    assert '<b>${this._tr("panel.correction")}:</b>' in panel


def test_statistics_tabs_and_body_share_one_outer_card_without_summary_heading():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _statisticsHtml() {")
    end = panel.index("\n\n  _maintenanceActionLabel", start)
    body = panel[start:end]
    assert 'class="entry-card statistics-report-card">${active==="forecast"?"":periodNav}${tabNav}${recordsCaption}<div class="statistics-tab-body">' in body
    summary_start = body.index('const summary=`')
    summary_end = body.index('const waitReasonEntries=', summary_start)
    summary = body[summary_start:summary_end]
    assert 'panel.execution_results' not in summary
    assert 'statistics-summary-caption' not in summary
    assert 'statistics-global-caption' in body
    assert 'class="entry-card statistics-tab-nav"' not in body


