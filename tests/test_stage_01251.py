"""Regression coverage for expanded notification recipient table in 0.12.51."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_recipient_table_exposes_language_and_delivery_details():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_notificationRecipientLanguageLabel' in panel
    assert 'this._tr("panel.notification_language")' in panel
    assert 'this._tr("panel.delivery_rules")' in panel
    assert '_notificationPresencePolicyLabel(c.presence_policy)' in panel
    assert '_notificationChannelFilterLabel(c.event_filter)' in panel
    assert 'notification-recipient-channel-line' in panel
    assert 'notification-recipient-rule-line' in panel
    assert 'enabledChannels=channels.filter(c=>c.enabled!==false).length' in panel


def test_delivery_rules_translation_exists_in_all_panel_locales():
    values = {}
    for language in ("en", "ru", "uk"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{language}.json").read_text(encoding="utf-8"))
        assert "panel.delivery_rules" in data
        assert data["panel.delivery_rules"].strip()
        values[language] = data["panel.delivery_rules"]
    assert values["ru"] == "Правила доставки"
    assert values["uk"] == "Правила доставлення"
    assert values["en"] == "Delivery rules"


def test_recipient_table_has_room_for_expanded_columns():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.notification-recipient-table { min-width:1180px; }' in panel
    assert '.notification-recipient-table td:nth-child(5){min-width:320px}' in panel
