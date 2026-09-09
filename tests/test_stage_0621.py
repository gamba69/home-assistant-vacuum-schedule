"""Regression tests for delivery detail is mobile responsive and technical ids are, delivery detail resolves live job and shows state valid, and delivery actions reuse existing job action handler and confirmation.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_delivery_detail_is_mobile_responsive_and_technical_ids_are_collapsible():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'class="entry-card notification-message-page"',
        '.notification-message-facts{grid-template-columns:1fr}',
        'class="notification-message-technical"',
        'this._tr("panel.technical_details")',
        '_notificationTechnicalValueHtml("event_id",message.event_id)',
    ):
        assert token in panel


def test_delivery_detail_resolves_live_job_and_shows_state_valid_actions():
    panel = PANEL.read_text(encoding="utf-8")
    frontend = FRONTEND.read_text(encoding="utf-8")
    for token in (
        '_notificationCurrentJobHtml(job)',
        'job.available_actions||[]',
        'data-action="${this._escape(item.action)}"',
        'class="text-link notification-open-job"',
        'view:"status",job:jobId',
    ):
        assert token in panel
    assert 'f"{DOMAIN}/notifications/detail"' in frontend


def test_delivery_actions_reuse_existing_job_action_handler_and_confirmation():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this.shadowRoot.querySelectorAll("button.job-action")' in panel
    assert 'type:"vacuum_schedule/jobs/action"' in panel
    assert 'action==="start_now"||action==="start_now_ignore_busy"||action==="skip"||action==="cancel"' in panel
    assert 'await this._confirmAction(' in panel


def test_mobile_history_rows_expose_labels_without_desktop_header():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'class="notification-message-row"',
        'data-label="${this._tr("panel.time")}"',
        'data-label="${this._tr("panel.message")}"',
        'data-label="${this._tr("panel.execution_mode")}"',
        'class="source-table mobile-card-table notification-message-list"',
    ):
        assert token in panel

