"""Regression contracts for Vacuum Schedule 0.9.0 page headings."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
LOCALE = MODULE / "frontend" / "localization"




def test_page_headings_use_dedicated_keys_in_tab_order():
    panel = PANEL.read_text(encoding="utf-8")
    expected = (
        'this._tr("panel.page_scheduler_status")',
        'this._tr("panel.page_user_notifications")',
        'this._tr("panel.page_scheduler_settings")',
        'this._tr("panel.page_dry_run_parameters")',
        'this._tr("panel.page_cleaning_schedule")',
    )
    for token in expected:
        assert token in panel


def test_russian_page_headings_match_approved_copy():
    ru = json.loads((LOCALE / "ru.json").read_text(encoding="utf-8"))
    assert ru["panel.page_scheduler_status"] == "Состояние планировщика"
    assert ru["panel.page_cleaning_schedule"] == "Расписание уборки"
    assert ru["panel.page_user_notifications"] == "Уведомления пользователей"
    assert ru["panel.page_scheduler_settings"] == "Настройка планировщика"
    assert ru["panel.page_dry_run_parameters"] == "Параметры Dry-Run"


def test_english_page_headings_match_approved_copy():
    en = json.loads((LOCALE / "en.json").read_text(encoding="utf-8"))
    assert en["panel.page_scheduler_status"] == "Scheduler status"
    assert en["panel.page_cleaning_schedule"] == "Cleaning schedule"
    assert en["panel.page_user_notifications"] == "User notifications"
    assert en["panel.page_scheduler_settings"] == "Scheduler settings"
    assert en["panel.page_dry_run_parameters"] == "Dry-Run parameters"


def test_schedule_table_uses_job_label_without_changing_schedule_domain_term():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads((LOCALE / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALE / "en.json").read_text(encoding="utf-8"))
    assert '<th>${this._tr("panel.job")}</th>' in panel
    assert ru["panel.job"] == "Задание"
    assert en["panel.job"] == "Task"
    assert ru["panel.schedule"] == "Расписание"
    assert en["panel.schedule"] == "Schedule"


def test_short_tab_labels_are_not_repurposed_as_page_headings():
    panel = PANEL.read_text(encoding="utf-8")
    tabs = panel[panel.index("  _tabsHtml() {"):panel.index("  _optionsHtml(")]
    assert 'this._tr("panel.notifications")' in tabs
    assert 'this._tr("panel.dry_run_tab")' in tabs


