"""Regression contracts for Vacuum Schedule 0.8.15 notification semantics."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    SemanticZoneSnapshot,
    render_generic_notify,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402


def _event(event_type, *, created, planned=None, wait=None, result=None, reason=None, starts=()):
    return SemanticNotificationEvent(
        event_id=f"0814:{event_type.value}:{created.isoformat()}",
        semantic_type="test",
        event_type=event_type,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=created,
        job_id="job-0814",
        schedule_name="Утро",
        planned_at=planned,
        wait_elapsed_seconds=wait,
        result=result,
        reason_code=reason,
        zone_snapshots=tuple(
            SemanticZoneSnapshot(zone_id=str(i), name=f"Z{i}", state="RUNNING", actual_start=value)
            for i, value in enumerate(starts)
        ),
    )




def test_wait_titles_show_only_meaningful_duration():
    now = datetime(2026, 8, 19, 15, 0)
    assert render_generic_notify(_event(NotificationEventType.WAIT_ENTER, created=now, wait=0), "ru").title == "⏳ Ожидание"
    assert render_generic_notify(_event(NotificationEventType.WAIT_ENTER, created=now, wait=3), "ru").title == "⏳ Ожидание"
    assert render_generic_notify(_event(NotificationEventType.WAIT_ENTER, created=now, wait=20), "ru").title == "⏳ Ожидание 20 сек"
    assert render_generic_notify(_event(NotificationEventType.WAIT_REMINDER, created=now, wait=15 * 60), "ru").title == "⏳ Ожидание 15 мин"


def test_prewarning_and_wait_use_planned_occurrence_time():
    created = datetime(2026, 8, 19, 15, 27)
    planned = datetime(2026, 8, 19, 15, 15)
    for event_type in (NotificationEventType.PREWARNING, NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER):
        rendered = render_generic_notify(_event(event_type, created=created, planned=planned, wait=20), "ru")
        assert rendered.message.splitlines()[-1].startswith("15:15")


def test_started_uses_earliest_actual_zone_start():
    created = datetime(2026, 8, 19, 15, 28)
    planned = datetime(2026, 8, 19, 15, 15)
    starts = (datetime(2026, 8, 19, 15, 26), datetime(2026, 8, 19, 15, 27))
    rendered = render_generic_notify(_event(NotificationEventType.STARTED, created=created, planned=planned, starts=starts), "ru")
    assert rendered.message == "Утро\n15:26"


def test_finished_uses_terminal_event_time_and_success_has_no_reason_noise():
    created = datetime(2026, 8, 19, 16, 3)
    planned = datetime(2026, 8, 19, 15, 15)
    rendered = render_generic_notify(
        _event(NotificationEventType.FINISHED, created=created, planned=planned, result="SUCCESS", reason="execution_success"),
        "ru",
    )
    assert rendered.title == "✅ Уборка завершена"
    assert rendered.message == "Утро\n16:03"
    assert "execution" not in rendered.message.lower()


def test_cancel_skip_and_partial_do_not_repeat_self_evident_reason():
    now = datetime(2026, 8, 19, 16, 5)
    cases = [
        ("PARTIAL_SUCCESS", "partial_success", "⚠️ Частично выполнено"),
        ("FAILED", "user_cancelled", "⏹️ Уборка отменена"),
        ("FAILED", "user_skipped", "⏭️ Уборка пропущена"),
    ]
    for result, reason, title in cases:
        rendered = render_generic_notify(_event(NotificationEventType.FINISHED, created=now, result=result, reason=reason), "ru")
        assert rendered.title == title
        assert rendered.message == "Утро\n16:05"


def test_execution_success_is_human_localized_in_both_catalogs():
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    assert ru["common.reason.execution_success"] == "Успешно выполнено"
    assert en["common.reason.execution_success"] == "Completed successfully"


def test_archive_table_uses_same_compact_title_and_body_without_zone_summary():
    manager = (MODULE / "notification_manager.py").read_text(encoding="utf-8")
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert "rendered = render_compact(event, language)" in manager
    assert 'const pushBody=String(x.message||"")' in panel
    assert 'notification-message-push-body' in panel
    assert 'const context=[x.schedule_name,(x.zones||[]).join(", ")].filter(Boolean).join(" · ")' not in panel


def test_wait_enter_manager_uses_configured_delay_but_immediate_wait_is_zero():
    source = (MODULE / "notification_manager.py").read_text(encoding="utf-8")
    assert "if policy.wait_enter_mode is WaitEnterMode.DELAYED" in source
    assert "max(0, int(policy.wait_delay_seconds))" in source
    assert "else 0" in source


