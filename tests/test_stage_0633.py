"""Regression tests for custom presentation catalogs have exact key and placeholder parity, ha native translation catalogs have recursive key and placeholder, and panel uses frontend user language and translation keys only.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from localization import normalize_language, translate  # noqa: E402
from notification_formatting import (  # noqa: E402
    SemanticAction,
    SemanticNotificationEvent,
    localized_blocker,
    localized_reason,
    render_mobile_app,
    render_plain,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402

PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANIFEST = MODULE / "manifest.json"
PRESENTATION_EN = MODULE / "frontend" / "localization" / "en.json"
PRESENTATION_RU = MODULE / "frontend" / "localization" / "ru.json"
PRESENTATION_UK = MODULE / "frontend" / "localization" / "uk.json"
HA_EN = MODULE / "translations" / "en.json"
HA_RU = MODULE / "translations" / "ru.json"
HA_UK = MODULE / "translations" / "uk.json"
FORMATTING = MODULE / "notification_formatting.py"
MANAGER = MODULE / "notification_manager.py"

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _flat(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flat(child, name))
    else:
        result[prefix] = str(value)
    return result


def _placeholders(value: str) -> set[str]:
    return set(_PLACEHOLDER.findall(value))




def test_custom_presentation_catalogs_have_exact_key_and_placeholder_parity():
    en = json.loads(PRESENTATION_EN.read_text(encoding="utf-8"))
    ru = json.loads(PRESENTATION_RU.read_text(encoding="utf-8"))
    uk = json.loads(PRESENTATION_UK.read_text(encoding="utf-8"))
    assert en.keys() == ru.keys() == uk.keys()
    assert len(en) >= 700
    for language, catalog in (("ru", ru), ("uk", uk)):
        mismatches = {
            key: (_placeholders(en[key]), _placeholders(catalog[key]))
            for key in en
            if _placeholders(en[key]) != _placeholders(catalog[key])
        }
        assert mismatches == {}, language


def test_ha_native_translation_catalogs_have_recursive_key_and_placeholder_parity():
    en = _flat(json.loads(HA_EN.read_text(encoding="utf-8")))
    ru = _flat(json.loads(HA_RU.read_text(encoding="utf-8")))
    uk = _flat(json.loads(HA_UK.read_text(encoding="utf-8")))
    assert en.keys() == ru.keys() == uk.keys()
    for language, catalog in (("ru", ru), ("uk", uk)):
        mismatches = {
            key: (_placeholders(en[key]), _placeholders(catalog[key]))
            for key in en
            if _placeholders(en[key]) != _placeholders(catalog[key])
        }
        assert mismatches == {}, language


def test_panel_uses_frontend_user_language_and_translation_keys_only():
    panel = PANEL.read_text(encoding="utf-8")
    assert "this._t(" not in panel
    assert not _CYRILLIC.search(panel)
    assert 'this._hass?.language || navigator.language || "en"' in panel
    assert 'raw.startsWith("uk") || raw.startsWith("ua")' in panel
    assert "_ensureTranslations()" in panel
    assert 'const fallback = await this._loadTranslationCatalog("en")' in panel

    en = json.loads(PRESENTATION_EN.read_text(encoding="utf-8"))
    ru = json.loads(PRESENTATION_RU.read_text(encoding="utf-8"))
    uk = json.loads((MODULE / "frontend" / "localization" / "uk.json").read_text(encoding="utf-8"))
    literal_keys = set(re.findall(r'_tr\("([A-Za-z0-9_.-]+)"', panel))
    missing = sorted(key for key in literal_keys if key not in en or key not in ru or key not in uk)
    assert missing == []


def test_known_dynamic_semantic_families_are_complete_in_both_catalogs():
    en = json.loads(PRESENTATION_EN.read_text(encoding="utf-8"))
    ru = json.loads(PRESENTATION_RU.read_text(encoding="utf-8"))
    uk = json.loads((MODULE / "frontend" / "localization" / "uk.json").read_text(encoding="utf-8"))
    keys = [
        *(f"common.job_state.{x}" for x in ("PLANNED", "WAIT", "STARTING", "RUNNING", "FINISHED")),
        *(f"common.job_result.{x}" for x in ("SUCCESS", "PARTIAL_SUCCESS", "FAILED")),
        *(f"common.preflight_decision.{x}" for x in ("PASS", "WAIT", "FAIL")),
        *(f"common.override_mode.{x}" for x in ("live", "freeze", "force", "unavailable")),
        *(f"notification.action.{x}" for x in ("START_NOW", "SKIP", "RECHECK", "CANCEL", "TEST_OPEN_RECORD", "TEST_DESTRUCTIVE_DEMO")),
    ]
    for key in keys:
        assert key in en
        assert key in ru
        assert en[key]
        assert ru[key]
        assert key in uk
        assert uk[key]


def test_python_presentation_boundary_has_no_embedded_russian_user_text():
    for path in (FORMATTING, MANAGER):
        text = path.read_text(encoding="utf-8")
        assert not _CYRILLIC.search(text), path.name
        assert "translate(" in text
    # The only allowed Cyrillic Python token in the package is a read-only
    # discovery synonym for a vendor/entity map name, not presentation text.
    offenders = []
    for path in MODULE.glob("*.py"):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CYRILLIC.search(line) and not (path.name == "capabilities.py" and '"карта"' in line):
                offenders.append(f"{path.name}:{index}:{line.strip()}")
    assert offenders == []


def test_language_normalization_and_unknown_locale_fall_back_to_english():
    assert normalize_language("ru") == "ru"
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("RU_ua") == "ru"
    assert normalize_language("en-GB") == "en"
    assert normalize_language("uk-UA") == "uk"
    assert normalize_language(None) == "en"
    assert translate("notification.field.schedule", "uk-UA") == "Розклад"


def test_notification_renderer_localizes_template_and_actions_without_touching_user_names():
    event = SemanticNotificationEvent(
        event_id="localization:test",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 8, 15, 12, 0),
        job_id="job-localization",
        schedule_name="My Kitchen / Кухня 42",
        zones=("Zone A / Зона А",),
        blockers=("battery_insufficient",),
        actions=(SemanticAction("RECHECK", ""), SemanticAction("SKIP", "")),
        dry_run=True,
    )
    ru = render_mobile_app(event, "ru", action_token=lambda command: command)
    en = render_mobile_app(event, "en", action_token=lambda command: command)

    assert "My Kitchen / Кухня 42" in ru.message
    assert "Расписание:" not in ru.message
    assert "Zone A / Зона А" not in ru.message
    assert "Низкий заряд" in ru.message
    assert ru.data["actions"][0]["title"] == translate("notification.action.RECHECK", "ru")
    assert ru.data["actions"][1]["title"] == translate("notification.action.SKIP", "ru")

    assert "My Kitchen / Кухня 42" in en.message
    assert "Schedule:" not in en.message
    assert "Zone A / Зона А" not in en.message
    assert "Low battery" in en.message
    assert en.data["actions"][0]["title"] == translate("notification.action.RECHECK", "en")
    assert en.data["actions"][1]["title"] == translate("notification.action.SKIP", "en")


def test_known_reason_blocker_and_decision_codes_never_leak_as_raw_user_text():
    assert localized_blocker("battery_insufficient", "ru") != "battery_insufficient"
    assert localized_blocker("battery_insufficient", "en") != "battery_insufficient"
    assert localized_reason("deadline_expired", "ru") != "deadline_expired"
    assert localized_reason("deadline_expired", "en") != "deadline_expired"

    event = SemanticNotificationEvent(
        event_id="prewarning:localization",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.PREWARNING,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 8, 15, 12, 0),
        schedule_name="Morning",
        advisory_decision="WAIT",
        advisory_blockers=("zone_busy",),
        current_decision="PASS",
        current_blockers=(),
    )
    rendered = render_plain(event, "ru")
    assert "WAIT" not in rendered.message
    assert "PASS" not in rendered.message
    assert "zone_busy" not in rendered.message
    assert "Нужно подождать" in rendered.message
    assert "Готово к запуску" in rendered.message


def test_testing_override_and_preflight_ui_no_longer_expose_raw_english_enum_labels():
    panel = PANEL.read_text(encoding="utf-8")
    for raw in (">PASS</span>", ">LIVE</option>", ">FREEZE</option>", ">FORCE</option>", ">UNAVAILABLE</option>", "<th>Live</th>"):
        assert raw not in panel
    assert 'const key = `common.preflight_decision.${value}`' in panel
    for key in (
        "common.override_mode.live",
        "common.override_mode.freeze",
        "common.override_mode.force",
        "common.override_mode.unavailable",
    ):
        assert key in panel


def test_frontend_error_renderer_prefers_localized_error_code_over_backend_message():
    panel = PANEL.read_text(encoding="utf-8")
    assert "const key = `error.${code}`" in panel
    assert "this._translations?.[key] ?? this._fallbackTranslations?.[key]" in panel
    assert 'return err?.message || err?.error?.message || this._tr("error.unknown")' in panel


def test_websocket_presentation_errors_are_machine_codes_and_panel_localizes_them():
    backend = FRONTEND.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert backend.count("connection.send_error(") == 1  # centralized helper only
    assert "_send_localizable_error(connection, msg[\"id\"]" in backend
    assert "const key = `error.${code}`" in panel


def test_execution_banners_use_stable_localization_keys_not_release_number_keys():
    panel = PANEL.read_text(encoding="utf-8")
    en = json.loads(PRESENTATION_EN.read_text(encoding="utf-8"))
    ru = json.loads(PRESENTATION_RU.read_text(encoding="utf-8"))
    assert 'panel.dry_run_banner_text' in panel
    assert 'panel.dry_run_tab' in panel
    assert 'panel.dry_run_banner_text' in en and 'panel.dry_run_banner_text' in ru
    assert 'panel.dry_run_tab' in en and 'panel.dry_run_tab' in ru

