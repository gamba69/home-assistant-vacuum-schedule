"""Regression tests for mobile app renderer sends independent push with group open, mobile app terminal notification is also independent and has, and pushover renderer uses explicit options targets and absolute open.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE_DIR))

from notification_formatting import (  # noqa: E402
    SemanticAction,
    SemanticNotificationEvent,
    render_generic_notify,
    render_mobile_app,
    render_pushover,
)
from notification_models import (  # noqa: E402
    NotificationClass,
    NotificationEventType,
)

PANEL = MODULE_DIR / "frontend" / "panel.js"
FRONTEND = MODULE_DIR / "frontend.py"
MANAGER = MODULE_DIR / "notification_manager.py"
FORMATTING = MODULE_DIR / "notification_formatting.py"
MANIFEST = MODULE_DIR / "manifest.json"


def _wait_event() -> SemanticNotificationEvent:
    now = datetime(2026, 8, 14, 10, 30)
    return SemanticNotificationEvent(
        event_id="job-phone:wait_enter",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=now,
        job_id="job-phone",
        schedule_name="Утро",
        zones=("Кухня", "Коридор"),
        planned_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=35),
        blockers=("battery_low",),
        wait_elapsed_seconds=600,
        dry_run=True,
        actions=(
            SemanticAction("RECHECK", "Повторить"),
            SemanticAction("SKIP", "Пропустить"),
        ),
    )




def test_mobile_app_renderer_sends_independent_push_with_group_open_path_and_actions():
    event = _wait_event()
    rendered = render_mobile_app(
        event,
        "ru",
        action_token=lambda command: f"TOKEN:{command}",
        open_path="/vacuum-schedule",
        group_prefix="vacuum_schedule:entry",
    )
    assert rendered.message_tag is None
    assert "tag" not in rendered.data
    assert rendered.data["group"] == "vacuum_schedule:entry:jobs"
    assert rendered.data["url"] == "/vacuum-schedule"
    assert rendered.data["clickAction"] == "/vacuum-schedule"
    actions = rendered.data["actions"]
    assert actions[0] == {"action": "TOKEN:RECHECK", "title": "Повторить"}
    assert actions[1]["action"] == "TOKEN:SKIP"
    assert actions[1]["destructive"] is True
    assert rendered.title == "⏳ Ожидание 10 мин"
    assert rendered.message == "Утро\n10:20 · Низкий заряд"
    assert "Кухня" not in rendered.message
    assert "Коридор" not in rendered.message


def test_mobile_app_terminal_notification_is_also_independent_and_has_no_actions():
    wait = _wait_event()
    finished = SemanticNotificationEvent(
        event_id="job-phone:finished",
        semantic_type="vacuum.job.completed",
        event_type=NotificationEventType.FINISHED,
        notification_class=NotificationClass.INFO,
        severity="success",
        created_at=wait.created_at + timedelta(minutes=30),
        job_id=wait.job_id,
        schedule_name=wait.schedule_name,
        zones=wait.zones,
        planned_at=wait.planned_at,
        deadline_at=wait.deadline_at,
        result="SUCCESS",
        dry_run=True,
        actions=(),
    )
    rendered = render_mobile_app(
        finished,
        "ru",
        action_token=lambda command: f"TOKEN:{command}",
        group_prefix="vacuum_schedule:entry",
    )
    assert rendered.message_tag is None
    assert "tag" not in rendered.data
    assert "actions" not in rendered.data
    assert rendered.actions == ()
    assert "Уборка завершена" in rendered.title

def test_pushover_renderer_uses_explicit_options_targets_and_absolute_open_url_without_actions():
    event = _wait_event()
    rendered = render_pushover(
        event,
        "ru",
        open_url="https://ha.example/vacuum-schedule",
        options={
            "targets": ["iphone", "ipad"],
            "priority": 2,
            "sound": "siren",
            "ttl": 900,
            "retry": 30,
            "expire": 300,
        },
    )
    assert rendered.targets == ("iphone", "ipad")
    assert rendered.data["priority"] == 2
    assert rendered.data["sound"] == "siren"
    assert rendered.data["ttl"] == 900
    assert rendered.data["retry"] == 30
    assert rendered.data["expire"] == 300
    assert rendered.data["url"] == "https://ha.example/vacuum-schedule"
    assert "Открыть Vacuum Schedule" == rendered.data["url_title"]
    assert rendered.actions == ()


def test_pushover_renderer_does_not_auto_escalate_priority():
    rendered = render_pushover(_wait_event(), "en", options={})
    assert "priority" not in rendered.data
    assert "retry" not in rendered.data
    assert "expire" not in rendered.data


def test_generic_renderer_is_provider_neutral_and_has_no_actions_or_data():
    rendered = render_generic_notify(_wait_event(), "ru")
    assert rendered.actions == ()
    assert rendered.data == {}
    assert rendered.targets == ()
    assert rendered.message_tag is None


def test_manager_has_mobile_independent_push_pushover_and_safe_entity_fallback():
    manager = MANAGER.read_text(encoding="utf-8")
    for token in (
        "render_mobile_app",
        "render_pushover",
        "render_generic_notify",
        "Mobile App tag/replacement/clear semantics are",
        "delivery_id is the sole",
        '"delivery_operation": "sent_basic"',
        "Provider-specific Companion/Pushover",
    ):
        assert token in manager
    assert '"message": "clear_notification"' not in manager
    assert 'provider_message_tag == rendered.message_tag' not in manager


def test_channel_discovery_advertises_actions_but_not_update_semantics_for_mobile_service():
    manager = MANAGER.read_text(encoding="utf-8")
    assert '"supports_actionable": transport is TransportType.MOBILE_APP' in manager
    assert '"supports_updates": False' in manager
    assert 'actionable = channel.target_kind == "service"' in manager
    assert '"supports_actionable": telegram_actionable if transport is TransportType.TELEGRAM else False' in manager


def test_recipient_editor_explains_mobile_pushover_and_generic_renderer_behavior():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        "panel.every_allowed_event_is_sent_as_a_separate_push_duplicate_business_delive",
        "panel.pushover_receives_text_a_vacuum_schedule_link_and_the_priority_sound_tt",
        "panel.safe_fallback_title_and_text_only_without_transport_specific_fields_or_i",
        'effectiveTransport==="pushover"&&providerRichPayload',
    ):
        assert token in panel
    assert "стабильному tag" not in panel


def test_semantic_formatting_layer_still_has_no_home_assistant_dependency():
    formatting = FORMATTING.read_text(encoding="utf-8")
    assert "homeassistant" not in formatting
    assert "render_mobile_app" in formatting
    assert "render_pushover" in formatting
    assert "render_generic_notify" in formatting
