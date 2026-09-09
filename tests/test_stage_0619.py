"""Regression tests for route specific panel deep links are built before rendering, mobile test actions are safe uri actions functionally, and telegram uri actions are url buttons not callbacks functionally.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANAGER = MODULE / "notification_manager.py"
FORMATTING = MODULE / "notification_formatting.py"




def test_route_specific_panel_deep_links_are_built_before_rendering():
    manager = MANAGER.read_text(encoding="utf-8")
    for token in (
        'params: dict[str, str] = {"config_entry": self.entry_id}',
        'params["message"] = message_id',
        'open_path = self._panel_path(view="notifications", message_id=event.event_id)',
        'routed_event = self._event_for_delivery(',
    ):
        assert token in manager
    # Job messages no longer deep-link directly into the verbose Job page.
    section = manager[manager.index('def _event_for_delivery'):manager.index('def _render_for_channel')]
    assert 'view="status"' not in section


def test_mobile_test_actions_are_safe_uri_actions_functionally():
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticAction, SemanticNotificationEvent, render_mobile_app
    from notification_models import NotificationClass, NotificationEventType

    event = SemanticNotificationEvent(
        event_id="test-event:test:1",
        semantic_type="vacuum.system.test",
        event_type=NotificationEventType.TEST,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 14, 12, 0),
        open_path="/vacuum-schedule?view=notifications&delivery=d1",
        actions=(
            SemanticAction("TEST_OPEN", "Open", uri="/vacuum-schedule?view=notifications&delivery=d1"),
            SemanticAction("TEST_DANGER", "Skip (demo)", uri="/vacuum-schedule?view=notifications", destructive=True),
        ),
    )
    rendered = render_mobile_app(event, "en", action_token=lambda command: f"TOKEN:{command}", open_path=event.open_path)
    assert rendered.data["url"] == event.open_path
    assert rendered.data["clickAction"] == event.open_path
    assert rendered.data["actions"][0] == {
        "action": "URI", "title": "Open", "uri": "/vacuum-schedule?view=notifications&delivery=d1"
    }
    assert rendered.data["actions"][1]["action"] == "URI"
    assert rendered.data["actions"][1]["destructive"] is True
    assert all(not action["action"].startswith("TOKEN:") for action in rendered.data["actions"])


def test_telegram_uri_actions_are_url_buttons_not_callbacks_functionally():
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticAction, SemanticNotificationEvent, render_telegram
    from notification_models import NotificationClass, NotificationEventType

    event = SemanticNotificationEvent(
        event_id="test-event:test:2",
        semantic_type="vacuum.system.test",
        event_type=NotificationEventType.TEST,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 14, 12, 1),
        actions=(SemanticAction("TEST_OPEN", "Open", uri="https://ha.example/vacuum-schedule?delivery=d2"),),
    )
    rendered = render_telegram(event, "en", callback_token=lambda command: f"CB:{command}", open_url="https://ha.example/vacuum-schedule?delivery=d2")
    assert rendered.inline_keyboard == ((("Open", "https://ha.example/vacuum-schedule?delivery=d2"),),)


def test_notifications_get_can_include_deep_linked_record_outside_recent_page():
    frontend = FRONTEND.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'vol.Optional("delivery_id"): str' in frontend
    assert 'vol.Optional("event_id"): str' in frontend
    assert 'msg.get("event_id")' in frontend
    assert 'selected = self.store.get_event(selected_event_id)' in manager
    assert 'records.append(selected)' in manager


def test_delivery_log_expands_and_highlights_specific_record():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'this._notificationMessageSelectedId = null',
        'params.get("message")',
        'class="notification-message-row"',
        '_notificationMessageDetailHtml()',
        '_notificationTechnicalValueHtml("event_id",message.event_id)',
    ):
        assert token in panel


def test_job_deep_link_focuses_related_job():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'params.get("job")' in panel
    assert 'data-job-id="${this._escape(job.job_id)}"' in panel
    assert 'if(job.tagName==="DETAILS")job.open=true' in panel
    assert 'job.classList.add("deep-link-target")' in panel
