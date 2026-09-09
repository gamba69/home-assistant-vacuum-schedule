"""Regression contracts for Vacuum Schedule 0.8.12 Companion App deep links."""

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANAGER = MODULE / "notification_manager.py"




def test_mobile_renderer_can_separate_ios_navigation_url_from_android_click_action():
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticNotificationEvent, render_mobile_app
    from notification_models import NotificationClass, NotificationEventType

    path = "/vacuum-schedule?config_entry=abc123&view=status&job=job-42&delivery=delivery-7"
    ios_url = "homeassistant://navigate" + path
    event = SemanticNotificationEvent(
        event_id="event:0811:1",
        semantic_type="vacuum.job.started",
        event_type=NotificationEventType.STARTED,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 19, 13, 0),
        job_id="job-42",
        open_path=path,
    )
    rendered = render_mobile_app(
        event,
        "en",
        action_token=lambda command: command,
        open_path=path,
        ios_open_url=ios_url,
    )
    assert rendered.data["url"] == ios_url
    assert rendered.data["clickAction"] == path


def test_manager_builds_companion_navigation_uri_without_losing_query_string():
    source = MANAGER.read_text(encoding="utf-8")
    assert 'return f"homeassistant://navigate{relative}"' in source
    assert 'ios_open_url=self._mobile_app_navigation_uri(event.open_path)' in source
    assert 'replace(action, uri=self._mobile_app_navigation_uri(action.uri))' in source


def test_relative_uri_action_can_be_rendered_as_companion_navigation_uri():
    sys.path.insert(0, str(MODULE))
    from notification_formatting import SemanticAction, SemanticNotificationEvent, render_mobile_app
    from notification_models import NotificationClass, NotificationEventType

    path = "/vacuum-schedule?config_entry=abc123&view=notifications&delivery=delivery-7"
    nav = "homeassistant://navigate" + path
    event = SemanticNotificationEvent(
        event_id="event:0811:2",
        semantic_type="vacuum.system.test",
        event_type=NotificationEventType.TEST,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 19, 13, 1),
        actions=(SemanticAction("OPEN", "Open", uri=nav),),
    )
    rendered = render_mobile_app(event, "en", action_token=lambda command: command)
    assert rendered.data["actions"][0]["uri"] == nav


def test_absolute_external_uri_action_is_not_targeted_by_manager_rewrite():
    source = MANAGER.read_text(encoding="utf-8")
    assert 'if action.uri and str(action.uri).startswith("/") else action' in source
