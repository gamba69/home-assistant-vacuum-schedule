"""Regression tests for telegram transport roundtrips in recipient binding, semantic wait event renders localized business context, and telegram renderer uses html inline callbacks and stable job.

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
    render_plain,
    render_telegram,
    semantic_type_for,
)
from notification_models import (  # noqa: E402
    NotificationChannel,
    NotificationClass,
    NotificationEventType,
    NotificationRecipient,
    TransportType,
    normalize_notification_settings,
)

PANEL = MODULE_DIR / "frontend" / "panel.js"
FRONTEND = MODULE_DIR / "frontend.py"
MANAGER = MODULE_DIR / "notification_manager.py"
FORMATTING = MODULE_DIR / "notification_formatting.py"
SCHEDULER = MODULE_DIR / "scheduler_engine.py"
MANIFEST = MODULE_DIR / "manifest.json"




def test_telegram_transport_roundtrips_in_recipient_binding():
    channel = NotificationChannel(
        channel_id="tg-home",
        name="Telegram home",
        transport_type=TransportType.TELEGRAM,
        target="notify.telegram_home",
        target_kind="entity",
        actionable=True,
    )
    restored = NotificationChannel.from_dict(channel.to_dict())
    assert restored == channel
    assert restored.transport_type is TransportType.TELEGRAM


def test_semantic_wait_event_renders_localized_business_context():
    now = datetime(2026, 8, 14, 9, 30)
    event = SemanticNotificationEvent(
        event_id="job-1:wait_enter",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=now,
        job_id="job-1",
        schedule_name="Утро",
        zones=("Кухня", "Коридор"),
        planned_at=now - timedelta(minutes=5),
        deadline_at=now + timedelta(minutes=40),
        blockers=("battery_low", "dnd_active"),
        wait_elapsed_seconds=125,
        dry_run=True,
        actions=(SemanticAction("RECHECK", "Повторить"), SemanticAction("SKIP", "Пропустить")),
    )
    rendered = render_plain(event, "ru")
    assert "Уборка ожидает запуска" in rendered.title
    assert "Расписание: Утро" in rendered.message
    assert "Зоны: Кухня, Коридор" in rendered.message
    assert "Недостаточный заряд аккумулятора" in rendered.message
    assert "Не беспокоить" in rendered.message
    assert "Ожидание: 2 мин 5 с" in rendered.message
    assert "🧪 Dry-Run" in rendered.message
    assert semantic_type_for(NotificationEventType.WAIT_ENTER) == "vacuum.job.preflight_blocked"


def test_telegram_renderer_uses_html_inline_callbacks_and_stable_job_tag():
    event = SemanticNotificationEvent(
        event_id="job-telegram:prewarning",
        semantic_type="vacuum.job.prewarning",
        event_type=NotificationEventType.PREWARNING,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 14, 8, 0),
        job_id="job-telegram",
        schedule_name="Morning <clean>",
        zones=("Kitchen & hall",),
        dry_run=True,
        actions=(SemanticAction("START_NOW", "Start now"), SemanticAction("SKIP", "Skip")),
    )
    rendered = render_telegram(
        event,
        "en",
        callback_token=lambda command: f"CALLBACK:{command}",
        open_url="https://ha.example/vacuum-schedule",
        tag_prefix="vacuum_schedule:entry",
    )
    assert rendered.title == ""
    assert rendered.parse_mode == "html"
    assert "<b>" in rendered.message
    assert "Morning &lt;clean&gt;" in rendered.message
    assert "Kitchen &amp; hall" not in rendered.message
    assert "08:00" in rendered.message
    assert rendered.message_tag == "vacuum_schedule:entry:job-telegram"
    flat = [button for row in rendered.inline_keyboard for button in row]
    assert ("Start now", "CALLBACK:START_NOW") in flat
    assert ("Skip", "CALLBACK:SKIP") in flat
    assert ("Open in Home Assistant", "https://ha.example/vacuum-schedule") in flat
    assert "CALLBACK:START_NOW" not in rendered.message


def test_telegram_callback_token_scheme_fits_telegram_limit_for_normal_job_ids():
    # Vacuum Schedule job IDs are UUID-like/hex-sized. The manager intentionally
    # uses an 8-char entry prefix plus one-letter commands.
    token = f"VS6|{'e' * 8}|{'j' * 32}|N"
    assert len(token.encode("utf-8")) <= 64
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'return f"VS6|{self.entry_id[:8]}|{event.job_id}|{self._recipient_token(recipient_id)}|{code}"' in manager
    assert 'return f"VS6|{self.entry_id[:8]}|{event.job_id}|{code}"' in manager
    assert '"START_NOW": "N"' in manager
    assert '"RECHECK": "R"' in manager


def test_manager_autodetects_ha_owned_telegram_entities_and_updates_messages():
    manager = MANAGER.read_text(encoding="utf-8")
    for token in (
        'value == "telegram_bot"',
        "entity_registry as er",
        "_resolved_channel_transport",
        '"supports_updates": transport is TransportType.TELEGRAM',
        'mode in {"polling", "webhooks"}',
        '"telegram_mode": telegram_mode',
        '"telegram_bot", "send_message"',
        '"telegram_bot", "edit_message"',
        "return_response=True",
        "provider_message_id",
        '", ".join(f"{title}:{data}" for title, data in row)',
        "_async_sync_mutable_state",
        "policy suppresses a state event",
    ):
        assert token in manager


def test_scheduler_handles_telegram_callbacks_and_keeps_legacy_mobile_tokens_compatible():
    scheduler = SCHEDULER.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    assert '"telegram_callback"' in scheduler
    assert "parse_telegram_callback" in scheduler
    assert "async_finalize_telegram_callback" in scheduler
    assert 'legacy_prefix = f"VS060|{self.entry_id}|"' in manager
    assert "parse_mobile_action" in scheduler
    assert "telegram_notification" in scheduler


def test_recipient_editor_attaches_discovered_ha_channels_instead_of_creating_transport_config():
    panel = PANEL.read_text(encoding="utf-8")
    assert "_notificationChannelSearchHtml" in panel
    assert 'panel.ha_notify_channel' in panel
    assert 'this._tr("panel.attach")' in panel
    assert "bell-plus-outline" in panel
    assert 'panel.the_channel_is_created_and_configured_in_home_assistant_vacuum_schedule' in panel
    assert 'data-notification-channel-field="name"' not in panel
    assert "supports_updates" in panel
    assert 'panel.telegram_broadcast_is_send_only_so_action_buttons_are_unavailable_use_po' in panel
    assert 'const current=channel.target' in panel


def test_same_ha_notify_channel_can_be_bound_to_multiple_recipients():
    raw = {
        "recipients": [
            {
                "recipient_id": "one",
                "name": "One",
                "channels": [
                    {
                        "channel_id": "one-tg",
                        "name": "Telegram",
                        "transport_type": "telegram",
                        "target": "notify.telegram_family",
                        "target_kind": "entity",
                    }
                ],
            },
            {
                "recipient_id": "two",
                "name": "Two",
                "channels": [
                    {
                        "channel_id": "two-tg",
                        "name": "Telegram",
                        "transport_type": "telegram",
                        "target": "notify.telegram_family",
                        "target_kind": "entity",
                    }
                ],
            },
        ]
    }
    normalized = normalize_notification_settings(raw)
    assert normalized["recipients"][0]["channels"][0]["target"] == "notify.telegram_family"
    assert normalized["recipients"][1]["channels"][0]["target"] == "notify.telegram_family"


def test_semantic_layer_is_home_assistant_independent_and_physical_execution_stays_disabled():
    formatting = FORMATTING.read_text(encoding="utf-8")
    scheduler = SCHEDULER.read_text(encoding="utf-8")
    assert "homeassistant" not in formatting
    assert "SemanticNotificationEvent" in formatting
    for forbidden in ("vacuum.start", "vacuum.send_command", "executor.async_start", "executor.async_stop"):
        assert forbidden not in scheduler
