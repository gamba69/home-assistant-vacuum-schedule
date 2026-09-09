"""UI terminology and shared cleaning-profile presentation for 0.11.9."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def _localization(lang: str) -> dict:
    return json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))




def test_schedule_and_statistics_share_compact_cleaning_profile_formatter():
    panel = PANEL.read_text(encoding="utf-8")
    cleaning = panel[panel.index("  _cleaningChips(row) {"):panel.index("\n\n  _scheduleOverrideSummary", panel.index("  _cleaningChips(row) {"))]
    statistics = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    assert "this._compactCleaningProfileText(row.cleaning_summary || {})" in cleaning
    assert "const profileLabel=(params)=>this._compactCleaningProfileText(params);" in statistics


def test_schedule_execution_policy_is_separate_from_cleaning_profile():
    panel = PANEL.read_text(encoding="utf-8")
    cleaning = panel[panel.index("  _cleaningChips(row) {"):panel.index("\n\n  _scheduleOverrideSummary", panel.index("  _cleaningChips(row) {"))]
    assert 'class="schedule-cleaning-profile"' in cleaning
    assert 'class="schedule-execution-policy"' in cleaning
    assert "zone_execution_policy" in cleaning


def test_median_is_med_everywhere_in_frontend_localization():
    for lang in ("ru", "en"):
        data = _localization(lang)
        assert data["panel.median"] == "Med"
        joined = "\n".join(value for value in data.values() if isinstance(value, str))
        assert re.search(r"медиан|median", joined, re.IGNORECASE) is None


def test_battery_card_uses_task_terminology():
    assert _localization("ru")["panel.battery_per_job"] == "Расход на задание"
    assert _localization("en")["panel.battery_per_job"] == "Battery per task"


def test_no_literal_user_facing_job_word_remains_in_frontend_localization():
    for lang in ("ru", "en"):
        values = "\n".join(value for value in _localization(lang).values() if isinstance(value, str))
        assert re.search(r"\bJobs?\b", values) is None


