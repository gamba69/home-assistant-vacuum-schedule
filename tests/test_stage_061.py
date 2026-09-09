"""Regression tests for execution gate summary controls never use wrapping form actions, execution gate buttons are compact icon controls, and summary cards have one fixed compact height.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"
MANIFEST = ROOT / "custom_components" / "vacuum_schedule" / "manifest.json"
BACKEND = ROOT / "custom_components" / "vacuum_schedule" / "frontend.py"


class Stage061CompactStatusUiTests(unittest.TestCase):

    def test_execution_gate_summary_controls_never_use_wrapping_form_actions(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn("summary-card summary-card-gate", panel)
        self.assertIn("mini-actions summary-actions", panel)
        self.assertIn(".summary-actions { display:flex; flex:0 0 auto; align-items:center; gap:4px; flex-wrap:nowrap; margin:0; }", panel)
        self.assertNotIn(".notification-event-checks,.job-actions,.mini-actions", panel)

    def test_execution_gate_buttons_are_compact_icon_controls(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn('class="ghost icon-only compact-icon-action quick-gate summary-action"', panel)
        self.assertIn('class="ghost icon-only compact-icon-action quick-gate-until summary-action"', panel)
        self.assertIn('this._mdi("power")', panel)
        self.assertIn('this._mdi("clock-outline")', panel)
        self.assertIn("button.icon-only.compact-icon-action {", panel)
        self.assertIn("--vs-compact-action-size:28px;", panel)

    def test_summary_cards_have_one_fixed_compact_height(self):
        panel = PANEL.read_text(encoding="utf-8")
        self.assertIn("height:44px; min-height:44px", panel)
        self.assertIn("grid-template-columns:.95fr 1.35fr 1.15fr 1fr", panel)


if __name__ == "__main__":
    unittest.main()
