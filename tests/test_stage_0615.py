"""Regression tests for plain test notification is actually localized to russian, frontend passes current profile language to both test paths, and manager test rendering accepts explicit language override.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE_DIR))

from notification_formatting import SemanticNotificationEvent, render_generic_notify  # noqa: E402
from notification_models import NotificationClass, NotificationEventType  # noqa: E402

PANEL = MODULE_DIR / "frontend" / "panel.js"
FRONTEND = MODULE_DIR / "frontend.py"
MANAGER = MODULE_DIR / "notification_manager.py"
MANIFEST = MODULE_DIR / "manifest.json"




def test_plain_test_notification_is_actually_localized_to_russian():
    event = SemanticNotificationEvent(
        event_id="test-event:test:1",
        semantic_type="vacuum.system.test",
        event_type=NotificationEventType.TEST,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=datetime(2026, 8, 14, 11, 0),
        schedule_name="Тест Vacuum Schedule",
        dry_run=True,
    )
    rendered = render_generic_notify(event, "ru")
    assert rendered.title == "🧪 Тест"
    assert rendered.message == "Тест Vacuum Schedule\n11:00"
    assert "Расписание:" not in rendered.message
    assert "Dry-Run" not in rendered.message
    assert "TEST" not in rendered.title


def test_frontend_passes_current_profile_language_to_both_test_paths():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'channel_id:channel.channel_id,language:this._language' not in panel
    assert 'notification_class:btn.dataset.event==="finished"?"error":"attention",language:this._language' not in panel

    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'vol.Optional("language"): vol.In(list(SUPPORTED_PRESENTATION_LANGUAGES))' in frontend
    assert 'language=msg.get("language")' in frontend


def test_manager_test_rendering_accepts_explicit_language_override():
    manager = MANAGER.read_text(encoding="utf-8")
    for token in (
        'language: str | None = None',
        'event = self._test_event(language=route_language)',
        'self._render_for_channel(channel, event, language=route_language)',
        'summary = await self.async_deliver_event(',
        'language=language',
        'schedule_name=translate("notification.value.test_schedule", language)',
    ):
        assert token in manager


def test_notify_candidates_use_real_ha_names_instead_of_prettified_entity_or_service_ids():
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'service_name.replace("_", " ")' not in manager
    assert '"name": owner_name' in manager
    assert '"name_source": "config_entry" if owner_name else "none"' in manager
    assert 'display_name, name_source = self._entity_candidate_name' in manager
    assert 'attrs.get("friendly_name")' in manager
    assert 'getattr(reg_entry, "name", "")' in manager
    assert 'getattr(device, "name_by_user", "")' in manager
    assert 'getattr(config_entry, "title", "")' in manager
    assert '"name": display_name' in manager


def test_notify_picker_visually_separates_friendly_name_transport_and_technical_target():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'label:friendly||transport' in panel
    assert 'detail:x.target' in panel
    assert 'badge:transport' in panel
    assert 'display:friendly?`${friendly} · ${transport}`:`${transport} · ${x.target}`' in panel
    assert 'class="ui-chip status-badge search-select-badge"' in panel
    assert 'const search = `${label} ${detail} ${badge} ${item.value || ""}`.toLowerCase();' in panel
    assert 'label:`${x.name||x.target}' not in panel
