"""Statistics attribution and compact profile UI contracts for 0.10.21."""
from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"
sys.path.insert(0, str(MODULE))

from statistics_models import aggregate_records  # noqa: E402


def _historical_record(zones):
    return {
        "job_id": "job-old",
        "schedule_id": "schedule-1",
        "schedule_name": "Old clean",
        "schedule_revision": 1,
        "result": "SUCCESS",
        "origin": "SCHEDULED",
        "execution_mode": "REAL",
        "cleaning_params_snapshot": {
            "cleaning_mode": "vac_and_mop",
            "fan_mode": "balanced",
            "mop_mode": "fast",
            "water_mode": "standard",
            "passes": 2,
        },
        "time": {"physical_execution_seconds": 900.0, "wait": {"total_seconds": 0.0, "count": 0, "by_reason_seconds": {}}, "pause_seconds": 0.0},
        "battery": {"consumed_percent": 12.0},
        "area": {"floor_cleaned_m2": 30.0, "processed_m2": 60.0, "physical_cleaned_m2": 30.0},
        "water_usage": {"available": True, "clean_used_ml_eq": 300.0, "dirty_gained_ml_eq": 180.0},
        "zones": zones,
    }


def test_current_nominal_area_fills_old_multi_zone_attribution_without_rewriting_record():
    record = _historical_record([
        {"zone_id": "small", "zone_name": "Small", "result": "SUCCESS", "attribution": "unavailable"},
        {"zone_id": "large", "zone_name": "Large", "result": "SUCCESS", "attribution": "unavailable"},
    ])
    aggregate = aggregate_records(
        [record],
        current_zone_nominal_areas={"small": 10.0, "large": 20.0},
    )
    rows = {row["zone_id"]: row for row in aggregate["zones"]["rows"]}
    assert rows["small"]["floor_area_m2"]["median"] == 10.0
    assert rows["large"]["floor_area_m2"]["median"] == 20.0
    assert rows["small"]["cleaning_seconds"]["median"] == 300.0
    assert rows["large"]["cleaning_seconds"]["median"] == 600.0
    assert rows["small"]["estimated"] == 1 and rows["large"]["estimated"] == 1
    assert "nominal_area_m2" not in record["zones"][0]
    assert "nominal_area_m2" not in record["zones"][1]


def test_saved_historical_nominal_area_wins_over_current_setting():
    record = _historical_record([
        {"zone_id": "small", "zone_name": "Small", "result": "SUCCESS", "nominal_area_m2": 5.0, "attribution": "unavailable"},
        {"zone_id": "large", "zone_name": "Large", "result": "SUCCESS", "nominal_area_m2": 25.0, "attribution": "unavailable"},
    ])
    aggregate = aggregate_records(
        [record],
        current_zone_nominal_areas={"small": 10.0, "large": 20.0},
    )
    rows = {row["zone_id"]: row for row in aggregate["zones"]["rows"]}
    assert rows["small"]["floor_area_m2"]["median"] == 5.0
    assert rows["large"]["floor_area_m2"]["median"] == 25.0


def test_no_saved_or_current_nominal_basis_remains_unavailable():
    record = _historical_record([
        {"zone_id": "one", "zone_name": "One", "result": "SUCCESS", "attribution": "unavailable"},
        {"zone_id": "two", "zone_name": "Two", "result": "SUCCESS", "attribution": "unavailable"},
    ])
    aggregate = aggregate_records([record], current_zone_nominal_areas={})
    for row in aggregate["zones"]["rows"]:
        assert row["floor_area_m2"]["count"] == 0
        assert row["cleaning_seconds"]["count"] == 0
        assert row["unavailable"] == 1


def test_statistics_controls_share_geometry_and_date_is_a_normal_form_control():
    panel = PANEL.read_text(encoding="utf-8")
    assert "input[type=text], input[type=time], input[type=date], input[type=number]" in panel
    assert "button.statistics-period-toggle{min-height:34px!important;height:34px;" in panel
    assert "button.history-report-tab { min-height:34px!important; height:34px;" in panel


def test_statistics_and_schedule_list_share_compact_profile_renderer():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_compactPresetLabel(kind, value)" in panel
    assert "_compactCleaningProfileParts(params, { includeDefaultPasses = true } = {})" in panel
    assert "const profile = this._compactCleaningProfileText(row.cleaning_summary || {});" in panel
    assert 'const describe=(override)=>this._compactCleaningProfileText({...base,...(override||{})});' in panel
    assert 'const profileLabel=(params)=>this._compactCleaningProfileText(params);' in panel
    assert "`${p.passes ?? 1}×`" in panel
    assert "_cleaningProfileFieldVisible(params, key)" in panel


def test_compact_ru_profile_words_are_short_but_meaningful():
    ru = json.loads(RU.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ru["common.preset_short.cleaning_mode.vac_and_mop"] == "Пылесос+мойка"
    assert ru["common.preset_short.fan_mode.balanced"] == "Баланс"
    assert ru["common.preset_short.mop_mode.fast"] == "быстро"
    assert ru["common.preset_short.water_mode.standard"] == "стандарт"
    assert ru["panel.mop_short"] == "Мойка"
    assert ru["panel.water_short"] == "Вода"
    assert en["common.preset_short.cleaning_mode.vac_and_mop"] == "Vacuum+mop"


