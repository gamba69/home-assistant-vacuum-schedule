"""Regression tests for recipients are wrapped in card and no floating recipients header regression.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components/vacuum_schedule/frontend/panel.js"
MANIFEST = ROOT / "custom_components/vacuum_schedule/manifest.json"
FRONTEND = ROOT / "custom_components/vacuum_schedule/frontend.py"


def test_recipients_are_wrapped_in_card():
    panel = PANEL.read_text(encoding="utf-8")
    assert '<section class="entry-card notification-users-card">' in panel
    assert '_notificationRecipientTableHtml(settings)' in panel
    assert 'notification-recipient-table' in panel
    assert 'notification-users-header' in panel

def test_no_floating_recipients_header_regression():
    panel = PANEL.read_text(encoding="utf-8")
    assert '\n      <div class="entry-header notification-users-header">' not in panel
    assert '<section class="entry-card notification-users-card"><div class="entry-header notification-users-header">' in panel
