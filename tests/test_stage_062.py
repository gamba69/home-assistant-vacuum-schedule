"""Regression tests for refresh is icon only with tooltip, schedule row actions are icon only with tooltips, and add schedule is short text action.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"
MANIFEST = ROOT / "custom_components" / "vacuum_schedule" / "manifest.json"
BACKEND = ROOT / "custom_components" / "vacuum_schedule" / "frontend.py"


class Stage062CompactScheduleActionsTests(unittest.TestCase):

    def test_refresh_is_icon_only_with_tooltip(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn('class="ghost icon-only refresh"', panel)
        self.assertIn('title="${this._tr("panel.refresh")}"', panel)
        self.assertIn('aria-label="${this._tr("panel.refresh")}"', panel)
        self.assertNotIn('<span>${this._tr("panel.refresh")}</span>', panel)

    def test_schedule_row_actions_are_icon_only_with_tooltips(self):
        panel = PANEL.read_text(encoding="utf-8")
        for token in (
            'class="ghost icon-only compact-icon-action schedule-toggle"',
            'class="primary icon-only compact-icon-action edit"',
            'class="danger icon-only compact-icon-action delete"',
            'title="${this._tr("panel.edit_schedule")}"',
            'title="${this._tr("panel.delete_schedule")}"',
        ):
            self.assertIn(token, panel)
        self.assertIn('.schedule-actions { display:flex; gap:4px; align-items:flex-start;', panel)
        self.assertNotIn('class="ghost icon-only schedule-run-now"', panel)

    def test_add_schedule_is_short_text_action(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn('this._mdi("calendar-plus-outline")', panel)
        self.assertIn('${this._tr("panel.add")}', panel)
        self.assertNotIn('${this._t("+ Добавить", "+ Add")}', panel)


if __name__ == "__main__":
    unittest.main()
