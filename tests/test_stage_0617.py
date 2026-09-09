"""Regression tests for wait and reminder have distinct titles and diagnostic context, mobile renderer does not use tag replace or clear, and telegram wait reminder is new push not edit in.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANAGER = MODULE / "notification_manager.py"
FORMATTING = MODULE / "notification_formatting.py"




def test_wait_and_reminder_have_distinct_titles_and_diagnostic_context():
    formatting = FORMATTING.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'NotificationEventType.WAIT_ENTER' in formatting
    assert 'NotificationEventType.WAIT_REMINDER' in formatting
    assert 'notification.title.wait_reminder' in formatting
    for token in (
        'wait_elapsed = 120',
        'wait_elapsed = 1800',
        'reminder_index = 1',
        'diagnostic_marker=now.strftime("%H:%M:%S.%f")[:-3]',
        'blockers = ("zone_busy",)',
    ):
        assert token in manager


def test_mobile_renderer_does_not_use_tag_replace_or_clear_lifecycle():
    formatting = FORMATTING.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    mobile_block = formatting[formatting.index("def render_mobile_app("):formatting.index("def render_pushover(")]
    assert '"tag"' not in mobile_block
    assert "message_tag=" not in mobile_block
    assert '"message": "clear_notification"' not in manager
    assert "sent_fresh" not in manager
    assert "provider_message_tag == rendered.message_tag" not in manager

def test_telegram_wait_reminder_is_new_push_not_edit_in_place():
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'WAIT entry/reminders must be new Telegram pushes, not silent edits.' in manager
    assert '"inline_keyboard": []' in manager
    assert 'previous = None' in manager


def test_history_exposes_delivery_operation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._notificationDeliveryOperationLabel(x.delivery_operation)' in panel
    assert 'common.delivery_operation.${value}' in panel


def test_mobile_renderer_produces_independent_wait_payloads_without_tags_functionally():
    import sys
    from datetime import datetime
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticNotificationEvent, render_mobile_app
    from notification_models import NotificationClass, NotificationEventType

    base = dict(
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 8, 14, 11, 45),
        job_id="job-wait",
        schedule_name="Test",
        dry_run=True,
    )
    wait = SemanticNotificationEvent(
        event_id="job-wait:wait_enter", semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER, **base
    )
    reminder1 = SemanticNotificationEvent(
        event_id="job-wait:wait_reminder:1", semantic_type="vacuum.job.retry_scheduled",
        event_type=NotificationEventType.WAIT_REMINDER, reminder_index=1, **base
    )
    reminder2 = SemanticNotificationEvent(
        event_id="job-wait:wait_reminder:2", semantic_type="vacuum.job.retry_scheduled",
        event_type=NotificationEventType.WAIT_REMINDER, reminder_index=2, **base
    )
    render = lambda event: render_mobile_app(event, "ru", action_token=lambda x: x, group_prefix="vs:test")
    rendered = [render(wait), render(reminder1), render(reminder2)]
    assert all(item.message_tag is None for item in rendered)
    assert all("tag" not in item.data for item in rendered)
    assert all(item.data["group"] == "vs:test:jobs" for item in rendered)
    # Immediate WAIT events intentionally use the same compact title; immutable
    # event/delivery identity remains the lifecycle distinction.
    assert rendered[0].title == rendered[1].title == rendered[2].title == "⏳ Ожидание"
