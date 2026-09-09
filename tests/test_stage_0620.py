"""Regression tests for wait test controls and notification event names are localized, russian wait settings do not expose internal wait state, and wait message renderer is localized in ru and en.

Covers retained behavioral contracts from earlier development stages.
"""
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_wait_test_controls_and_notification_event_names_are_localized():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_notificationEventLabel(value)' in panel
    assert 'wait_enter: this._tr("panel.wait_entry")' in panel
    assert 'wait_reminder: this._tr("panel.wait_reminder")' in panel
    assert '["wait_enter","WAIT"]' not in panel
    assert 'this._t("WAIT reminder","WAIT reminder")' not in panel
    assert 'this._notificationEventLabel(x.event_type)' in panel
    assert 'this._notificationEventLabel(v)' in panel


def test_russian_wait_settings_do_not_expose_internal_wait_state_name():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'this._tr("panel.wait_entry")',
        'this._tr("panel.wait_delay_seconds")',
        'this._tr("panel.send_wait_reminders")',
        'this._tr("panel.wait_reminders")',
    ):
        assert token in panel


def test_wait_message_renderer_is_localized_in_ru_and_en_functionally():
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticNotificationEvent, render_plain
    from notification_models import NotificationClass, NotificationEventType

    base = dict(
        event_id="test-wait",
        semantic_type="vacuum.job.preflight_blocked",
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 8, 14, 14, 0),
        schedule_name="Тест",
        wait_elapsed_seconds=120,
    )
    wait = SemanticNotificationEvent(event_type=NotificationEventType.WAIT_ENTER, **base)
    reminder = SemanticNotificationEvent(
        event_type=NotificationEventType.WAIT_REMINDER,
        reminder_index=1,
        **{**base, "event_id": "test-wait-reminder", "semantic_type": "vacuum.job.retry_scheduled"},
    )

    ru_wait = render_plain(wait, "ru")
    ru_reminder = render_plain(reminder, "ru")
    en_wait = render_plain(wait, "en")
    en_reminder = render_plain(reminder, "en")

    assert ru_wait.title == "⏳ Уборка ожидает запуска"
    assert "Ожидание: 2 мин" in ru_wait.message
    assert ru_reminder.title == "🔔 Уборка всё ещё ожидает запуска"
    assert "Ожидание: 2 мин" in ru_reminder.message
    assert "Напоминание: #1" in ru_reminder.message
    assert en_wait.title == "⏳ Cleaning is waiting to start"
    assert en_reminder.title == "🔔 Cleaning is still waiting to start"
    assert "Reminder: #1" in en_reminder.message
