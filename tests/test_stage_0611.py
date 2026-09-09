"""Regression tests for notification presence uses searchable compact picker.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"
FRONTEND = ROOT / "custom_components" / "vacuum_schedule" / "frontend.py"
MANIFEST = ROOT / "custom_components" / "vacuum_schedule" / "manifest.json"




def test_notification_presence_uses_searchable_compact_picker():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_notificationPresenceSearchHtml(selected)' in panel
    assert 'data-notification-recipient-field="presence_entity_id"' in panel
    assert 'this._tr("panel.search_person_or_device_tracker")' in panel
    assert '<select data-notification-recipient-field="presence_entity_id">' not in panel
    assert '.search-select-menu' in panel
    assert 'max-height:min(240px,42vh)' in panel
