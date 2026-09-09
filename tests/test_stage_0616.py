"""Regression tests for routing test bypasses only master switch and returns diagnostics, recipient presence and class filters are not bypassed by, and testing ui shows per route results and localized reasons.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE_DIR / "frontend" / "panel.js"
FRONTEND = MODULE_DIR / "frontend.py"
MANAGER = MODULE_DIR / "notification_manager.py"
MANIFEST = MODULE_DIR / "manifest.json"




def test_routing_test_bypasses_only_master_switch_and_returns_diagnostics():
    manager = MANAGER.read_text(encoding="utf-8")
    for token in (
        "ignore_master_enabled: bool = False",
        '"master_bypassed": bool(ignore_master_enabled and not settings.enabled)',
        "if not settings.enabled and not ignore_master_enabled:",
        "summary[\"suppressed\"] += 1",
        "summary[\"failed\"] += 1",
        "summary[\"sent\"] += 1",
        "ignore_master_enabled=True",
        'summary["deliveries_created"]',
    ):
        assert token in manager


def test_recipient_presence_and_class_filters_are_not_bypassed_by_routing_test():
    manager = MANAGER.read_text(encoding="utf-8")
    # The diagnostic path still uses the normal channel suppression function.
    assert "suppression = self._channel_suppression(recipient, channel, event)" in manager
    for reason in (
        'return "recipient_disabled"',
        'return "channel_disabled"',
        'return "event_filtered"',
        'return "presence_unknown" if presence == "unknown" else "not_home"',
        'return "presence_unknown" if presence == "unknown" else "not_away"',
    ):
        assert reason in manager


def test_testing_ui_shows_per_route_results_and_localized_reasons():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        "_notificationRoutingTestResultHtml()",
        "_notificationSuppressionLabel(reason)",
        'this._tr("panel.sent")',
        'this._tr("panel.suppressed")',
        'this._tr("panel.failed")',
        "panel.the_global_notification_master_switch_is_currently_off_it_was_bypassed_f",
        "panel.event_filtered_by_channel_rules",
        "panel.presence_state_is_unknown",
        "panel.recipient_is_not_home",
        "panel.recipient_is_home",
    ):
        assert token in panel


def test_routing_test_toast_reports_real_sent_suppressed_failed_counts():
    panel = PANEL.read_text(encoding="utf-8")
    assert "this._notificationRoutingTestResult=result" in panel
    assert "const sent=Number(result.sent||0),suppressed=Number(result.suppressed||0),failed=Number(result.failed||0)" in panel
    assert "Создано доставок:" not in panel
