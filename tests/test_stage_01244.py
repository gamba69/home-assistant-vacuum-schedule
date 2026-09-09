"""Regression coverage for 0.12.44 Ukrainian localization and language inheritance."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from localization import normalize_language, translate  # noqa: E402
from notification_formatting import SemanticAction, SemanticNotificationEvent, render_plain  # noqa: E402
from notification_models import NotificationClass, NotificationEventType, NotificationRecipient  # noqa: E402
from datetime import datetime  # noqa: E402


def _leaf_paths(value, prefix=""):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else key
            result |= _leaf_paths(item, child)
    else:
        result.add(prefix)
    return result


def test_frontend_locales_have_identical_keys_and_ukrainian_is_real_locale():
    catalogs = {
        lang: json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "ru", "uk")
    }
    assert set(catalogs["en"]) == set(catalogs["ru"]) == set(catalogs["uk"])
    assert catalogs["uk"]["panel.interface_language"] == "Мова інтерфейсу"
    assert catalogs["uk"]["common.language.uk"] == "Українська"
    assert catalogs["uk"]["notification.field.schedule"] == "Розклад"


def test_native_ha_translation_tree_has_exact_key_parity():
    source = json.loads((MODULE / "translations" / "en.json").read_text(encoding="utf-8"))
    locales = {
        lang: json.loads((MODULE / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "ru", "uk")
    }
    expected = _leaf_paths(source)
    for lang, catalog in locales.items():
        assert _leaf_paths(catalog) == expected, lang
    assert locales["uk"]["title"] == "Планувальник прибирання"
    assert locales["uk"]["selector"]["weekdays"]["options"]["sun"] == "Неділя"


def test_language_normalization_supports_home_assistant_uk_and_legacy_ua_spellings():
    assert normalize_language("uk") == "uk"
    assert normalize_language("uk-UA") == "uk"
    assert normalize_language("ua") == "uk"
    assert translate("notification.field.schedule", "uk-UA") == "Розклад"


def test_recipient_language_default_migrates_old_auto_and_allows_ukrainian():
    legacy = NotificationRecipient.from_dict({"recipient_id": "legacy", "name": "Legacy", "language": "auto"})
    default = NotificationRecipient.from_dict({"recipient_id": "default", "name": "Default"})
    ukrainian = NotificationRecipient.from_dict({"recipient_id": "uk", "name": "UA", "language": "uk"})
    assert legacy.language == "default"
    assert default.language == "default"
    assert ukrainian.language == "uk"
    assert ukrainian.to_dict()["language"] == "uk"


def test_panel_exposes_global_and_per_recipient_language_controls():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    assert 'option value="auto"' in panel
    assert 'option value="uk"' in panel
    assert 'option value="default"' in panel
    assert 'type:"vacuum_schedule/settings/update_interface_language"' in panel
    assert 'f"{DOMAIN}/settings/update_interface_language"' in frontend
    assert 'SUPPORTED_INTERFACE_LANGUAGES' in frontend


def test_notification_test_paths_do_not_force_panel_language_over_recipient_language():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert 'channel_id:channel.channel_id,language:this._language' not in panel
    assert 'notification_class:btn.dataset.event==="finished"?"error":"attention",language:this._language' not in panel


def test_ukrainian_notification_renderer_uses_ukrainian_catalog_and_actions():
    event = SemanticNotificationEvent(
        event_id="uk:test",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 9, 6, 12, 0),
        schedule_name="Кухня",
        blockers=("battery_insufficient",),
        actions=(SemanticAction("RECHECK", ""),),
    )
    rendered = render_plain(event, "uk")
    assert "Недостатній заряд акумулятора" in rendered.message
    assert "battery_insufficient" not in rendered.message
    assert translate("notification.action.RECHECK", "uk") != "notification.action.RECHECK"
