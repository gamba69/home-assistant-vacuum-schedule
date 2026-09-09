"""Regression contracts for Vacuum Schedule 0.9.0 schedule tab label."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
LOCALE = MODULE / "frontend" / "localization"




def test_schedule_tab_is_singular_in_both_languages():
    ru = json.loads((LOCALE / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALE / "en.json").read_text(encoding="utf-8"))
    assert ru["panel.schedules"] == "Расписание"
    assert en["panel.schedules"] == "Schedule"


def test_schedule_tab_still_uses_dedicated_tab_key():
    panel = PANEL.read_text(encoding="utf-8")
    tabs = panel[panel.index("  _tabsHtml() {"):panel.index("  _optionsHtml(")]
    assert '["schedules", "calendar-clock", this._tr("panel.schedules")]' in tabs


