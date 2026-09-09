"""Statistics calendar-filter and compact-unit frontend contracts for 0.10.21."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"




def test_statistics_uses_explicit_from_to_dates_and_no_period_select():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'id="statistics-from" type="date"' in panel
    assert 'id="statistics-to" type="date"' in panel
    assert 'id="statistics-period"' not in panel
    assert 'f.period' not in panel
    assert 'const start=this._statisticsRangeIso(f.date_from,false);' in panel
    assert 'const end=this._statisticsRangeIso(f.date_to,true);' in panel
    assert 'if(start) payload.start=start;' in panel
    assert 'if(end) payload.end=end;' in panel


def test_all_requested_quick_calendar_ranges_are_present_above_report_tabs():
    panel = PANEL.read_text(encoding="utf-8")
    for key in (
        "statistics_today",
        "statistics_yesterday",
        "statistics_day_before_yesterday",
        "statistics_current_week",
        "statistics_previous_week",
        "statistics_current_month",
        "statistics_previous_month",
    ):
        assert f'panel.{key}' in panel
    assert 'const periodNav=`<div class="statistics-period-tabs"' in panel
    assert 'data-statistics-from="${range.from}"' in panel
    assert 'data-statistics-to="${range.to}"' in panel
    assert '${active==="forecast"?"":periodNav}${tabNav}${recordsCaption}' in panel


def test_ru_quick_range_labels_match_approved_wording():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.date_from"] == "С"
    assert ru["panel.date_to"] == "По"
    assert [ru[f"panel.statistics_{key}"] for key in (
        "today", "yesterday", "day_before_yesterday", "current_week",
        "previous_week", "current_month", "previous_month",
    )] == ["Сегодня", "Вчера", "Позавчера", "Текущая неделя", "Прошлая неделя", "Текущий месяц", "Прошлый месяц"]


def test_profile_tables_use_mm_ss_and_units_in_headers_only():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'const tableDuration=' in panel
    assert 'const tableDuration=(v)=>this._formatDurationSeconds(v);' in panel
    assert 'tableTime(x.cleaning_seconds,"median")' in panel
    assert 'tableTime(x.cleaning_seconds,"percentile")' in panel
    for key in (
        "panel.battery_percent_header",
        "panel.floor_area_m2_header",
        "panel.clean_water_ml_per_m2_header",
        "panel.dirty_water_ml_per_m2_header",
    ):
        assert key in panel
    assert 'tableMetric(x.battery_consumed_percent,"median")' in panel
    assert 'tableMetric(x.floor_area_m2||x.area_m2,"median")' in panel
    assert 'tableMetric(x.clean_water_ml_eq_per_m2,"median")' in panel
    assert 'tableMetric(x.dirty_water_ml_eq_per_m2,"median")' in panel


def test_ru_profile_table_headers_include_units():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    assert ru["panel.battery_percent_header"] == "Аккумулятор, %"
    assert ru["panel.floor_area_m2_header"] == "Площадь пола, м²"
    assert ru["panel.clean_water_ml_per_m2_header"] == "Чистая, мл/м²"
    assert ru["panel.dirty_water_ml_per_m2_header"] == "Грязная, мл/м²"


def test_frontend_exposes_plain_water_units_without_equivalent_jargon():
    panel = PANEL.read_text(encoding="utf-8")
    ru_text = RU.read_text(encoding="utf-8")
    en_text = EN.read_text(encoding="utf-8")
    visible = panel + ru_text + en_text
    for forbidden in ("ml-eq", "l-eq", "мл-экв", "л-экв", "ml-equivalent"):
        assert forbidden not in visible
    ru = json.loads(ru_text)
    assert ru["panel.ml_unit"] == "мл"
    assert ru["panel.ml_per_m2_unit"] == "мл/м²"
    assert ru["panel.l_unit"] == "л"
