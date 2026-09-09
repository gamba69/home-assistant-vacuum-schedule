"""Regression contracts for Vacuum Schedule 0.8.12 notification message archive."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANAGER = MODULE / "notification_manager.py"
STORE = MODULE / "notification_store.py"
ENGINE = MODULE / "scheduler_engine.py"
FRONTEND = MODULE / "frontend.py"
PANEL = MODULE / "frontend" / "panel.js"




def test_logical_notification_event_is_persisted_independently_from_deliveries():
    store = STORE.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    assert "class NotificationEventRecord" in store
    assert "self.events: dict[str, NotificationEventRecord]" in store
    assert "self.event_history: list[str]" in store
    assert "def put_event(self, event: SemanticNotificationEvent)" in store
    assert '"events": event_values' in store
    assert '"event_history": event_keys' in store
    assert 'put_event = getattr(self.store, "put_event", None)' in manager


def test_new_deep_links_open_message_not_verbose_job():
    manager = MANAGER.read_text(encoding="utf-8")
    section = manager[manager.index("def _event_for_delivery"):manager.index("def _render_for_channel")]
    assert 'open_path = self._panel_path(view="notifications", message_id=event.event_id)' in section
    assert 'params["message"] = message_id' in manager
    assert 'view="status"' not in section
    assert 'job_id=event.job_id' not in section


def test_automatic_job_push_actions_are_not_frozen_into_delivery():
    manager = MANAGER.read_text(encoding="utf-8")
    section = manager[manager.index("def _event_for_delivery"):manager.index("def _render_for_channel")]
    assert "if event.job_id:" in section
    assert "actions = ()" in section
    assert "if self._is_test_event(event):" in section


def test_legacy_delivery_deep_link_resolves_to_message():
    store = STORE.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert "def event_id_for_delivery" in store
    assert "selected_event_id = self.store.event_id_for_delivery(delivery_id)" in manager
    assert 'params.get("delivery")' in panel
    assert "selected_message_id" in panel
    assert 'if (messageId || deliveryId)' in panel
    assert 'this._view = "notifications"' in panel


def test_notification_detail_websocket_returns_message_and_live_job_context():
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/notifications/detail"' in frontend
    assert "scheduler.notifications.message_payload(" in frontend
    assert "scheduler.notification_job_context_payload(job_id)" in frontend
    assert "websocket_api.async_register_command(hass, websocket_notifications_detail)" in frontend


def test_dynamic_job_actions_are_backend_derived():
    engine = ENGINE.read_text(encoding="utf-8")
    section = engine[engine.index("def notification_job_context_payload"):engine.index("def job_details_payload")]
    for token in (
        '"action": "start_now"',
        '"action": "start_now_ignore_busy"',
        '"action": "recheck"',
        '"action": "skip"',
        '"action": "cancel"',
        '"action": "pause"',
        '"action": "resume"',
        '"zone_busy" in run.blockers',
        'ExecutionAttemptState.PAUSED',
    ):
        assert token in section


def test_message_ui_is_compact_and_full_job_is_secondary():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        "_notificationMessageDetailHtml()",
        "_notificationMessageFactsHtml(message)",
        "_notificationCurrentJobHtml(detail.job)",
        'job.available_actions||[]',
        'class="text-link notification-open-job"',
        'this._tr("panel.job_details_link")',
        'class="notification-message-technical"',
    ):
        assert token in panel
    detail = panel[panel.index("_notificationMessageDetailHtml()"):panel.index("_notificationMessagesHtml()")]
    assert "JSON.stringify" not in detail


def test_message_action_click_reloads_live_detail_and_reuses_authoritative_job_action():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'type:"vacuum_schedule/jobs/action"' in panel
    assert 'if(this._view==="notifications"&&this._notificationMessageSelectedId)await this._loadNotifications(false)' in panel
    assert 'action==="start_now_ignore_busy"' in panel
    assert 'await this._confirmAction(' in panel


def test_message_archive_is_one_row_per_logical_event():
    panel = PANEL.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    assert "this._notificationData?.messages||[]" in panel
    assert 'data-event-id="${this._escape(id)}"' in panel
    assert "self.store.recent_events(50)" in manager
    assert '"messages": messages' in manager


def test_0812_localization_complete():
    required = {
        "panel.current_state",
        "panel.waiting_zones",
        "panel.job_details_link",
        "panel.notification_archive",
        "panel.notification_archive_help",
        "panel.loading_notification_message",
        "panel.back_to_notifications",
        "panel.deliveries_summary",
        "error.notification_message_not_found",
    }
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert required <= set(data)


