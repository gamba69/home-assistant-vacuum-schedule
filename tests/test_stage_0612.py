"""Regression tests for recipients are compact table with separate editor, recipient editor uses draft and cancel discards changes, and recipient table actions are icon only with tooltips.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"
FRONTEND = ROOT / "custom_components" / "vacuum_schedule" / "frontend.py"
MANIFEST = ROOT / "custom_components" / "vacuum_schedule" / "manifest.json"




def test_recipients_are_compact_table_with_separate_editor():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_notificationRecipientTableHtml(settings)' in panel
    assert 'class="source-table notification-recipient-table mobile-card-table"' in panel
    assert 'notification-edit-recipient' in panel
    assert 'notification-delete-recipient' in panel
    assert '_notificationRecipientEditorHtml()' in panel
    assert 'notification-recipient-editor' in panel
    assert 'notification-recipient-save' in panel
    assert 'notification-recipient-cancel' in panel
    assert 'this._view === "notifications" && this._notificationRecipientDraft' in panel


def test_recipient_editor_uses_draft_and_cancel_discards_changes():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._notificationRecipientDraft=JSON.parse(JSON.stringify(recipient))' in panel
    assert 'this._notificationRecipientDraft=null' in panel
    assert 'this._notificationRecipientIsNew=true' in panel
    assert 'this._tr("panel.recipient_saved")' in panel


def test_recipient_table_actions_are_icon_only_with_tooltips():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'ghost icon-only compact-icon-action notification-edit-recipient' in panel
    assert 'danger icon-only compact-icon-action notification-delete-recipient' in panel
    assert 'title="${this._tr("panel.edit_recipient")}"' in panel
    assert 'title="${this._tr("panel.delete_recipient")}"' in panel
