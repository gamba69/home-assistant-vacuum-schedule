"""Regression contracts for Vacuum Schedule 0.8.13 compact outbound notifications."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    render_generic_notify,
    render_mobile_app,
    render_pushover,
    render_telegram,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402


def _wait_event(*, blockers=("zone_busy",)) -> SemanticNotificationEvent:
    now = datetime(2026, 8, 19, 14, 30)
    return SemanticNotificationEvent(
        event_id="job-0813:wait",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.WAIT_ENTER,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=now,
        job_id="job-0813",
        schedule_name="Утро",
        zones=("Кухня", "Гостиная"),
        planned_at=now - timedelta(minutes=15),
        deadline_at=now + timedelta(hours=2),
        blockers=tuple(blockers),
        wait_elapsed_seconds=900,
        dry_run=True,
    )




def test_all_outbound_transports_share_the_same_compact_semantics():
    event = _wait_event()
    mobile = render_mobile_app(event, "ru", action_token=lambda command: command)
    pushover = render_pushover(event, "ru")
    generic = render_generic_notify(event, "ru")
    telegram = render_telegram(event, "ru", callback_token=lambda command: command)

    assert mobile.title == pushover.title == generic.title == "⏳ Ожидание 15 мин"
    assert mobile.message == pushover.message == generic.message == "Утро\n14:15 · Занято"
    assert telegram.title == ""
    assert telegram.message == "<b>⏳ Ожидание 15 мин</b>\nУтро\n14:15 · Занято"


def test_outbound_text_has_no_zone_list_field_labels_full_dates_or_mode_noise():
    event = _wait_event()
    rendered = render_mobile_app(event, "ru", action_token=lambda command: command)
    for forbidden in (
        "Кухня", "Гостиная", "Расписание:", "Зоны:", "План:",
        "Можно запустить до", "Dry-Run", "19.08.2026", "2026-08-19",
    ):
        assert forbidden not in rendered.message
    assert rendered.message.splitlines()[0] == "Утро"
    assert rendered.message.splitlines()[1].startswith("14:15")


def test_multiple_blockers_collapse_to_one_short_reason_plus_count():
    rendered = render_generic_notify(_wait_event(blockers=("zone_busy", "battery_low", "dnd_active")), "ru")
    assert rendered.message == "Утро\n14:15 · Не беспокоить +2"
    assert "Недостаточный заряд аккумулятора" not in rendered.message
    assert "Зона уборки занята" not in rendered.message


def test_terminal_failure_uses_short_terminal_reason():
    now = datetime(2026, 8, 19, 16, 0)
    event = SemanticNotificationEvent(
        event_id="job-0813:finished",
        semantic_type="vacuum.job.failed",
        event_type=NotificationEventType.FINISHED,
        notification_class=NotificationClass.ERROR,
        severity="error",
        created_at=now,
        job_id="job-0813",
        schedule_name="Вечер",
        planned_at=now - timedelta(hours=1),
        result="FAILED",
        reason_code="deadline_expired",
    )
    rendered = render_pushover(event, "ru")
    assert rendered.title == "❌ Ошибка уборки"
    assert rendered.message == "Вечер\n16:00 · Время истекло"


def test_compact_titles_are_at_most_two_words_excluding_emoji():
    now = datetime(2026, 8, 19, 10, 0)
    variants = [
        (NotificationEventType.PREWARNING, None, None),
        (NotificationEventType.WAIT_ENTER, None, None),
        (NotificationEventType.WAIT_REMINDER, None, None),
        (NotificationEventType.STARTED, None, None),
        (NotificationEventType.FINISHED, "SUCCESS", None),
        (NotificationEventType.FINISHED, "PARTIAL_SUCCESS", None),
        (NotificationEventType.FINISHED, "FAILED", "deadline_expired"),
        (NotificationEventType.FINISHED, "FAILED", "user_cancelled"),
        (NotificationEventType.FINISHED, "FAILED", "user_skipped"),
        (NotificationEventType.TEST, None, None),
    ]
    for language in ("ru", "en"):
        for event_type, result, reason in variants:
            event = SemanticNotificationEvent(
                event_id=f"{language}:{event_type.value}:{result}:{reason}",
                semantic_type="test",
                event_type=event_type,
                notification_class=NotificationClass.INFO,
                severity="info",
                created_at=now,
                schedule_name="S",
                result=result,
                reason_code=reason,
            )
            title = render_generic_notify(event, language).title
            if event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}:
                continue
            import re
            words = re.findall(r"[A-Za-zА-Яа-яЁё]+", title)
            assert len(words) <= 2, (language, title)


def test_full_semantic_archive_contract_is_not_shrunk_by_external_formatter():
    source = (MODULE / "notification_store.py").read_text(encoding="utf-8")
    for field in (
        "zones=list(event.zones)",
        "zone_snapshots=[",
        "blockers=list(event.blockers)",
        "advisory_blockers=list(event.advisory_blockers)",
        "current_blockers=list(event.current_blockers)",
        "cleaning_params=dict(event.cleaning_params)",
        "wait_elapsed_seconds=event.wait_elapsed_seconds",
        "reason_code=event.reason_code",
    ):
        assert field in source


def test_compact_localization_is_complete_for_both_supported_languages():
    required_titles = {
        "notification.compact.title.prewarning",
        "notification.compact.title.wait_enter",
        "notification.compact.title.wait_reminder",
        "notification.compact.title.started",
        "notification.compact.title.success",
        "notification.compact.title.partial",
        "notification.compact.title.failed",
        "notification.compact.title.cancelled",
        "notification.compact.title.skipped",
        "notification.compact.title.test",
    }
    required_reasons = {f"notification.compact.reason.{name}" for name in {
        "battery", "water", "dnd", "dock", "disabled", "time_window",
        "configuration", "access", "occupied", "robot_busy", "robot_error",
        "robot_unavailable", "deadline", "execution", "preflight",
    }}
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert required_titles | required_reasons <= set(data)


