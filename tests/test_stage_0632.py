"""Regression tests for russian schedule notification template localizes title and field names, recipient profile persists notification language, and automatic delivery uses recipient language not only server language.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    render_plain,
)
from notification_models import (  # noqa: E402
    NotificationClass,
    NotificationEventType,
    NotificationRecipient,
)

MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANAGER = MODULE / "notification_manager.py"
MODELS = MODULE / "notification_models.py"


def _prewarning() -> SemanticNotificationEvent:
    now = datetime(2026, 8, 15, 10, 0)
    return SemanticNotificationEvent(
        event_id="job:prewarning",
        semantic_type="vacuum.job.prewarning",
        event_type=NotificationEventType.PREWARNING,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=now,
        schedule_name="Утренняя уборка",
        zones=("Кухня", "Коридор"),
        planned_at=now + timedelta(minutes=15),
        deadline_at=now + timedelta(hours=1),
        advisory_decision="PASS",
        current_decision="PASS",
        cleaning_params={"cleaning_mode": "vac_and_mop", "fan_mode": "balanced", "passes": 2},
        dry_run=True,
    )




def test_russian_schedule_notification_template_localizes_title_and_field_names():
    rendered = render_plain(_prewarning(), "ru")
    assert rendered.title == "ℹ️ Скоро уборка"
    for label in (
        "Расписание:",
        "Зоны:",
        "План:",
        "Можно запустить до:",
        "Параметры уборки:",
        "Предварительная проверка:",
        "Препятствия при проверке:",
        "Состояние сейчас:",
        "Препятствия сейчас:",
        "Режим:",
    ):
        assert label in rendered.message
    for english in ("Schedule:", "Zones:", "Planned:", "Latest start:", "Cleaning settings:"):
        assert english not in rendered.message


def test_recipient_profile_persists_notification_language():
    ru = NotificationRecipient.from_dict({"recipient_id": "igor", "name": "Игорь", "language": "ru"})
    assert ru.language == "ru"
    assert ru.to_dict()["language"] == "ru"
    legacy = NotificationRecipient.from_dict({"recipient_id": "legacy", "name": "Legacy"})
    assert legacy.language == "default"
    source = MODELS.read_text(encoding="utf-8")
    assert 'language: str = "default"' in source
    assert '"language": self.language' in source


def test_automatic_delivery_uses_recipient_language_not_only_server_language():
    source = MANAGER.read_text(encoding="utf-8")
    assert "def _recipient_language(" in source
    assert "route_language = self._recipient_language(recipient, language)" in source
    assert "event, delivery_id, language=route_language" in source
    assert "language=route_language" in source
    assert "recipient_id=recipient.recipient_id" in source
    assert "language=route_language," in source
    assert "self._localize_event_actions(event, language)" in source


def test_legacy_recipients_inherit_integration_language_without_capturing_panel_locale():
    backend = FRONTEND.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert 'vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES))' in backend
    assert 'recipient["language"] = frontend_language' not in backend
    assert 'type:"vacuum_schedule/notifications/get", entry_id:entryId, language:this._language' in panel


def test_recipient_editor_exposes_notification_language_and_new_recipient_defaults_to_integration():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'this._tr("panel.notification_language")' in panel
    assert 'data-notification-recipient-field="language"' in panel
    assert '<option value="default"' in panel
    assert '<option value="ru"' in panel
    assert '<option value="uk"' in panel
    assert '<option value="en"' in panel
    assert 'language:"default"' in panel

