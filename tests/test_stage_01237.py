"""Regression coverage for the 0.12.37 notification-policy spacing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_global_notification_message_types_have_visual_spacing():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[
        panel.index('      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.global_notification_policy")}'):
        panel.index('      <section class="entry-card notification-users-card">')
    ]
    assert block.count('class="field wide notification-policy-message-type"') == 5
    assert '.notification-policy-message-type { margin-top:8px; }' in panel
