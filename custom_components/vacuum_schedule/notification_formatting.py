"""Semantic notification events and transport renderers for Vacuum Schedule 0.8.0.

Scheduler code emits one transport-neutral semantic event. Routing decides who
should receive it, then a renderer turns that event into the representation
supported by one Home Assistant notification transport.

This module intentionally has no Home Assistant imports so formatting can be
unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape as html_escape
from typing import Any, Callable, Mapping

try:
    from .localization import normalize_language, translate
    from .notification_models import NotificationClass, NotificationEventType
except ImportError:  # pragma: no cover - isolated source-file testing
    from localization import normalize_language, translate
    from notification_models import NotificationClass, NotificationEventType


@dataclass(frozen=True, slots=True)
class SemanticAction:
    """One business action or safe navigation action exposed by a capable transport."""

    command: str
    title: str
    uri: str | None = None
    destructive: bool = False


@dataclass(frozen=True, slots=True)
class SemanticZoneSnapshot:
    """Immutable zone lifecycle snapshot embedded in one Job-level notification."""

    zone_id: str
    name: str
    state: str
    result: str | None = None
    reason_code: str | None = None
    blockers: tuple[str, ...] = ()
    actual_start: datetime | None = None


@dataclass(frozen=True, slots=True)
class SemanticNotificationEvent:
    """Transport-neutral scheduler event used by notification policy/routing."""

    event_id: str
    semantic_type: str
    event_type: NotificationEventType
    notification_class: NotificationClass
    severity: str
    created_at: datetime
    job_id: str | None = None
    schedule_name: str = ""
    zones: tuple[str, ...] = ()
    zone_snapshots: tuple[SemanticZoneSnapshot, ...] = ()
    planned_at: datetime | None = None
    deadline_at: datetime | None = None
    blockers: tuple[str, ...] = ()
    advisory_decision: str | None = None
    advisory_blockers: tuple[str, ...] = ()
    current_decision: str | None = None
    current_blockers: tuple[str, ...] = ()
    cleaning_params: Mapping[str, Any] = field(default_factory=dict)
    wait_elapsed_seconds: int | None = None
    result: str | None = None
    reason_code: str | None = None
    dry_run: bool = False
    actions: tuple[SemanticAction, ...] = ()
    recipient_ids: tuple[str, ...] = ()
    reminder_index: int | None = None
    diagnostic_marker: str | None = None
    occupancy_override_zones: tuple[str, ...] = ()
    open_path: str = "/vacuum-schedule"
    forecast_kind: str | None = None
    compact_v2: bool = False
    duration_seconds: int | None = None
    start_source: str | None = None
    coalesced_wait_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedNotification:
    """Transport payload produced from one semantic event.

    ``data`` is intentionally transport-specific and is produced only by the
    renderer that owns that transport.  The dispatcher does not invent provider
    fields from scheduler state.  ``message_tag`` is optional transport metadata
    used only where an adapter explicitly owns provider-side message mutation
    (currently Telegram); Mobile App deliberately leaves it unset.
    """

    title: str
    message: str
    actions: tuple[dict[str, Any], ...] = ()
    inline_keyboard: tuple[tuple[tuple[str, str], ...], ...] = ()
    parse_mode: str | None = None
    message_tag: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    targets: tuple[str, ...] = ()


def _language(language: str) -> str:
    return normalize_language(language)


def localized_reason(code: str | None, language: str) -> str:
    if not code:
        return "—"
    value = str(code)
    key = f"common.reason.{value}"
    translated = translate(key, language)
    if translated != key:
        return translated
    # Authoritative pre-flight failures retain their concrete blocker code as
    # the terminal reason. Reuse the same localized blocker vocabulary.
    return localized_blocker(value, language)


def _presentation_blocker_code(code: str) -> str:
    """Collapse protocol-specific reasons into one user-facing concept."""
    value = str(code)
    return "vacuum_busy" if value == "execution_lease_busy" else value


def localized_blocker(code: str, language: str) -> str:
    value = str(code)
    if value.startswith("synthetic_"):
        raw = _presentation_blocker_code(value[len("synthetic_") :])
        blocker_key = f"common.blocker.{raw}"
        blocker = translate(blocker_key, language)
        if blocker == blocker_key:
            blocker = raw.replace("_", " ")
        return translate("common.blocker.synthetic", language, blocker=blocker)
    value = _presentation_blocker_code(value)
    key = f"common.blocker.{value}"
    translated = translate(key, language)
    return value.replace("_", " ") if translated == key else translated


def localized_blockers(codes: tuple[str, ...] | list[str], language: str) -> tuple[str, ...]:
    """Localize and deduplicate blocker concepts for presentation only."""
    labels: list[str] = []
    seen: set[str] = set()
    for code in codes:
        value = str(code)
        synthetic = value.startswith("synthetic_")
        raw = value[len("synthetic_") :] if synthetic else value
        canonical = _presentation_blocker_code(raw)
        key = f"synthetic:{canonical}" if synthetic else canonical
        if not canonical or key in seen:
            continue
        seen.add(key)
        labels.append(localized_blocker(value, language))
    return tuple(labels)

def semantic_type_for(
    event_type: NotificationEventType,
    *,
    result: str | None = None,
    reason_code: str | None = None,
    blockers: tuple[str, ...] = (),
) -> str:
    """Map scheduler lifecycle data to stable semantic event names."""

    if event_type is NotificationEventType.START_FORECAST:
        return "vacuum.job.start_forecast"
    if event_type is NotificationEventType.PREWARNING:
        return "vacuum.job.preflight_blocked" if blockers else "vacuum.job.prewarning"
    if event_type is NotificationEventType.WAIT_ENTER:
        return "vacuum.job.preflight_blocked"
    if event_type is NotificationEventType.WAIT_REMINDER:
        return "vacuum.job.retry_scheduled"
    if event_type is NotificationEventType.STARTED:
        return "vacuum.job.started"
    if event_type is NotificationEventType.FINISHED:
        if reason_code == "user_cancelled":
            return "vacuum.job.cancelled"
        if reason_code == "user_skipped":
            return "vacuum.job.skipped"
        if result in {"SUCCESS", "PARTIAL_SUCCESS"}:
            return "vacuum.job.completed"
        if result == "SUPPRESSED":
            return "vacuum.job.suppressed"
        return "vacuum.job.failed"
    return "vacuum.system.test"


def _format_dt(value: datetime | None, language: str) -> str:
    if value is None:
        return "—"
    if _language(language) == "ru":
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%Y-%m-%d %H:%M")


def _duration(seconds: int | None, language: str) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {translate('common.unit.hour', language)}")
    if minutes:
        parts.append(f"{minutes} {translate('common.unit.minute', language)}")
    if not parts or sec:
        parts.append(f"{sec} {translate('common.unit.second', language)}")
    return " ".join(parts)


def _title(event: SemanticNotificationEvent, language: str) -> str:
    if event.event_type is NotificationEventType.START_FORECAST:
        return translate("notification.title.start_forecast", language)
    if event.event_type is NotificationEventType.PREWARNING:
        has_blockers = bool(event.blockers or event.advisory_blockers or event.current_blockers)
        return translate("notification.title.prewarning.blocked" if has_blockers else "notification.title.prewarning.clear", language)
    if event.event_type is NotificationEventType.WAIT_ENTER:
        return translate("notification.title.wait_enter", language)
    if event.event_type is NotificationEventType.WAIT_REMINDER:
        return translate("notification.title.wait_reminder", language)
    if event.event_type is NotificationEventType.STARTED:
        return translate("notification.title.started", language)
    if event.event_type is NotificationEventType.FINISHED:
        if event.reason_code == "user_cancelled":
            return translate("notification.title.finished.cancelled", language)
        if event.reason_code == "user_skipped":
            return translate("notification.title.finished.skipped", language)
        if event.result == "SUCCESS":
            return translate("notification.title.finished.success", language)
        if event.result == "PARTIAL_SUCCESS":
            return translate("notification.title.finished.partial", language)
        if event.result == "SUPPRESSED":
            return translate("notification.title.finished.suppressed", language)
        return translate("notification.title.finished.failed", language)
    return translate("notification.title.test", language)


_COMPACT_REASON_CATEGORY: dict[str, str] = {
    "battery_insufficient": "battery",
    "forecast_battery_insufficient": "battery",
    "forecast_time_insufficient": "time_window",
    "battery_low": "battery",
    "battery_unavailable": "battery_state",
    "clean_water_insufficient": "water",
    "forecast_clean_water_insufficient": "water",
    "forecast_dirty_water_insufficient": "dirty_water",
    "dock_clean_water_unavailable": "water_state",
    "dirty_water_full": "dirty_water",
    "dock_dirty_water_unavailable": "dirty_water_state",
    "detergent_unavailable": "detergent",
    "dock_detergent_unavailable": "detergent_state",
    "dnd": "dnd",
    "dnd_active": "dnd",
    "dnd_safety_margin": "dnd",
    "dnd_window": "dnd",
    "dnd_unavailable": "dnd_state",
    "dock_unavailable": "dock",
    "global_disabled": "disabled",
    "global_disabled_until": "disabled",
    "schedule_paused": "disabled",
    "insufficient_time_window": "time_window",
    "invalid_configuration": "configuration",
    "required_capability_missing": "configuration",
    "invalid_target": "target",
    "robot_target_missing": "target",
    "target_map_not_active": "map",
    "mop_not_attached": "mop",
    "mop_attached_unavailable": "mop_state",
    "mop_unavailable": "mop_state",
    "room_access_blocked": "access",
    "zone_access_blocked": "access",
    "room_busy": "occupied",
    "zone_busy": "occupied",
    "room_state_unknown": "zone_state",
    "room_unavailable": "zone_state",
    "zone_state_unknown": "zone_state",
    "shared_target_waiting": "linked_zones",
    "vacuum_busy": "robot_busy",
    "vacuum_error": "robot_error",
    "vacuum_unavailable": "robot_unavailable",
    "deadline_expired": "deadline",
    "resource_blocked_until_deadline": "resource_deadline",
    "displaced_by_next_occurrence": "displaced",
    "execution_failed": "execution",
    "execution_lost": "execution_state",
    "execution_preempted_external": "external_execution",
    "execution_preparation_failed": "preparation",
    "execution_volume_mute_failed": "volume",
    "execution_volume_restore_failed": "volume",
    "execution_volume_not_confirmed": "volume",
    "execution_start_rejected": "start_rejected",
    "execution_start_timeout": "start_timeout",
    "manual_run_failed": "execution",
    "missed_while_offline": "offline",
    "no_zone_succeeded": "nothing_cleaned",
    "partial_success": "partial",
    "preflight_failed": "preflight",
    "schedule_changed_after_warning": "schedule_changed",
    "schedule_disabled_by_user": "schedule_disabled",
    "schedule_removed_after_warning": "schedule_removed",
    "simulated_execution_failed": "simulation",
    "user_cancelled": "cancelled",
    "user_skipped": "skipped",
    "zone_disabled": "zone_disabled",
    "zone_disabled_until": "zone_disabled",
}

_COMPACT_REASON_PRIORITY: tuple[str, ...] = (
    "vacuum_error", "vacuum_unavailable", "dock_unavailable",
    "invalid_configuration", "required_capability_missing", "invalid_target",
    "global_disabled", "global_disabled_until", "schedule_paused",
    "dnd", "dnd_active", "dnd_safety_margin", "dnd_window",
    "battery_insufficient", "battery_low", "forecast_battery_insufficient",
    "clean_water_insufficient", "dirty_water_full", "forecast_clean_water_insufficient", "forecast_dirty_water_insufficient", "detergent_unavailable",
    "mop_not_attached", "zone_access_blocked", "room_access_blocked",
    "zone_busy", "room_busy", "vacuum_busy",
    "zone_state_unknown", "room_state_unknown", "room_unavailable",
    "insufficient_time_window", "forecast_time_insufficient", "shared_target_waiting",
)

_COMPACT_REASON_PRIORITY_INDEX = {code: index for index, code in enumerate(_COMPACT_REASON_PRIORITY)}


def _compact_wait_title(event: SemanticNotificationEvent, language: str) -> str:
    """Return a concise WAIT title, adding meaningful elapsed time only."""
    seconds = max(0, int(event.wait_elapsed_seconds or 0))
    # Immediate WAIT notifications can be emitted a few seconds after the state
    # transition because of scheduler/event-loop timing. Do not turn that
    # technical jitter into user-facing "Waiting 1 s" noise.
    if seconds < 5:
        return translate("notification.compact.title.wait", language)
    if seconds < 60:
        return translate("notification.compact.title.wait_seconds", language, value=seconds)
    minutes = max(1, int(round(seconds / 60)))
    return translate("notification.compact.title.wait_minutes", language, value=minutes)


def _compact_title(event: SemanticNotificationEvent, language: str) -> str:
    """Return a concise lock-screen title."""
    if event.event_type is NotificationEventType.PREWARNING:
        return translate("notification.compact.title.prewarning", language)
    if event.event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}:
        return _compact_wait_title(event, language)
    if event.event_type is NotificationEventType.STARTED:
        return translate("notification.compact.title.started", language)
    if event.event_type is NotificationEventType.FINISHED:
        if event.reason_code == "user_cancelled":
            return translate("notification.compact.title.cancelled", language)
        if event.reason_code == "user_skipped":
            return translate("notification.compact.title.skipped", language)
        if event.result == "SUCCESS":
            return translate("notification.compact.title.success", language)
        if event.result == "PARTIAL_SUCCESS":
            return translate("notification.compact.title.partial", language)
        if event.result == "SUPPRESSED":
            return translate("notification.compact.title.suppressed", language)
        return translate("notification.compact.title.failed", language)
    return translate("notification.compact.title.test", language)


def _compact_reason_codes(event: SemanticNotificationEvent) -> tuple[str, ...]:
    """Return only reasons that add information beyond the compact title."""
    values: list[str] = []
    if event.event_type is NotificationEventType.FINISHED:
        # Successful, partial, cancelled and skipped terminal titles already
        # communicate the outcome. Do not duplicate it in the body and never
        # leak the machine reason ``execution_success`` to the user.
        if event.result == "SUCCESS" or event.reason_code in {
            "execution_success", "simulated_success", "partial_success",
            "user_cancelled", "user_skipped",
        }:
            return ()
        if event.reason_code:
            values.append(str(event.reason_code))
    elif event.event_type is NotificationEventType.PREWARNING:
        source = event.current_blockers if event.compact_v2 and event.current_decision else (event.current_blockers or event.advisory_blockers or event.blockers)
        values.extend(str(item) for item in source)
    else:
        values.extend(str(item) for item in event.blockers)
        if not values and event.reason_code:
            values.append(str(event.reason_code))

    if not values and event.zone_snapshots and not (event.compact_v2 and event.event_type is NotificationEventType.PREWARNING and event.current_decision):
        values.extend(
            str(blocker)
            for zone in event.zone_snapshots
            for blocker in zone.blockers
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


def _short_text(value: str, limit: int = 30) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _compact_reason(event: SemanticNotificationEvent, language: str) -> str:
    codes = list(dict.fromkeys(
        _presentation_blocker_code(code) for code in _compact_reason_codes(event)
    ))
    if not codes:
        return ""
    original_order = {code: index for index, code in enumerate(codes)}
    codes.sort(key=lambda code: (_COMPACT_REASON_PRIORITY_INDEX.get(code, 10_000), original_order[code]))
    code = codes[0]
    category = _COMPACT_REASON_CATEGORY.get(code)
    short_key = f"notification.short.reason.{code if code.startswith('forecast_') else category}"
    short_value = translate(short_key, language)
    if (event.compact_v2 or event.event_type is NotificationEventType.START_FORECAST) and short_value != short_key:
        text = short_value
    elif category:
        key = f"notification.compact.reason.{category}"
        value = translate(key, language)
        if value != key:
            text = value
        else:
            text = localized_reason(code, language)
    else:
        text = localized_reason(code, language)
    text = _short_text(text)
    if len(codes) > 1:
        text = f"{text} +{len(codes) - 1}"
    return text


def _compact_time(event: SemanticNotificationEvent) -> str:
    """Return the clock time relevant to this lifecycle event.

    Prewarning and WAIT are about the planned occurrence, while STARTED and
    FINISHED report what actually happened. For STARTED prefer the earliest
    recorded zone start; ``created_at`` is the authoritative event timestamp
    fallback and is also used for terminal events.
    """
    if event.event_type in {
        NotificationEventType.PREWARNING,
        NotificationEventType.WAIT_ENTER,
        NotificationEventType.WAIT_REMINDER,
    }:
        value = event.planned_at or event.created_at
    elif event.event_type is NotificationEventType.STARTED:
        starts = [zone.actual_start for zone in event.zone_snapshots if zone.actual_start is not None]
        value = min(starts) if starts else event.created_at
    else:
        value = event.created_at
    return value.strftime("%H:%M") if value is not None else ""


def render_compact(event: SemanticNotificationEvent, language: str) -> RenderedNotification:
    """Render the intentionally tiny external notification surface.

    External transports carry only the event title, bare schedule name, start
    time and one concise reason.  Full semantic detail remains in the immutable
    notification archive opened by the deep link.
    """
    if event.compact_v2 or event.event_type is NotificationEventType.START_FORECAST:
        return _render_short(event, language)
    title = _compact_title(event, language)
    lines: list[str] = []
    if event.schedule_name:
        lines.append(str(event.schedule_name))
    time_text = _compact_time(event)
    reason = _compact_reason(event, language)
    status_parts = [part for part in (time_text, reason) if part]
    if status_parts:
        lines.append(" · ".join(status_parts))
    return RenderedNotification(title=title, message="\n".join(lines))


def _render_short(event: SemanticNotificationEvent, language: str) -> RenderedNotification:
    """One schedule heading and one actionable line for phone previews."""
    title = _short_text(event.schedule_name, 40)
    if event.planned_at:
        title += f" · {event.planned_at:%H:%M}"
    if event.dry_run:
        title = f"Dry-Run · {title}"
    reason = _compact_reason(event, language)
    kind = event.forecast_kind
    if event.event_type is NotificationEventType.START_FORECAST:
        if kind == "closing":
            seconds = ((event.deadline_at - event.created_at).total_seconds() if event.deadline_at else 0)
            minutes = max(1, int((seconds + 59) // 60))
            body = translate("notification.short.closing", language, minutes=minutes)
        elif kind in {"ready", "started", "unknown", "risk"}:
            body = translate(f"notification.short.{kind}", language)
        elif kind == "improved":
            body = translate("notification.short.remaining", language, reason=reason)
        else:
            body = reason or translate("notification.short.unknown", language)
    elif event.event_type is NotificationEventType.PREWARNING:
        body = reason or translate("notification.short.ready" if event.current_decision == "PASS" else "notification.short.unknown", language)
    elif event.event_type is NotificationEventType.WAIT_ENTER:
        body = reason or translate("notification.short.wait", language)
    elif event.event_type is NotificationEventType.WAIT_REMINDER:
        body = translate("notification.short.still", language, reason=reason) if reason else translate("notification.short.wait", language)
    elif event.event_type is NotificationEventType.STARTED:
        count = sum(zone.actual_start is not None for zone in event.zone_snapshots)
        if count and count < len(event.zone_snapshots):
            body = translate("notification.short.partial_start", language, count=count, total=len(event.zone_snapshots))
        else:
            key = event.start_source if event.start_source in {"manual", "early", "waited"} else "started"
            body = translate(f"notification.short.{key}", language)
    elif event.event_type is NotificationEventType.FINISHED:
        if event.reason_code in {"user_cancelled", "user_skipped"}:
            key = "cancelled" if event.reason_code == "user_cancelled" else "skipped"
            body = translate(f"notification.short.{key}", language)
        elif event.result == "SUCCESS":
            body = translate("notification.short.done", language)
        elif event.result == "PARTIAL_SUCCESS":
            count = sum(zone.result == "SUCCESS" for zone in event.zone_snapshots)
            body = translate("notification.short.partial", language, count=count, total=len(event.zone_snapshots))
        else:
            body = translate("notification.short.not_done", language, reason=reason or translate("notification.short.error", language))
        if event.duration_seconds is not None and event.result == "SUCCESS":
            body += " · " + translate("notification.short.minutes", language, minutes=max(1, round(event.duration_seconds / 60)))
    else:
        body = translate("notification.compact.title.test", language)
    if event.event_type is not NotificationEventType.START_FORECAST and kind == "closing" and event.deadline_at:
        minutes = max(1, int(((event.deadline_at - event.created_at).total_seconds() + 59) // 60))
        body += " · " + translate("notification.short.closing", language, minutes=minutes)
    return RenderedNotification(title=title, message=body)


def _cleaning_params_text(params: Mapping[str, Any], language: str) -> str:
    parts: list[str] = []
    for key in (
        "cleaning_mode", "fan_mode", "cleaning_route", "mop_mode", "water_mode",
        "passes", "minimum_battery_percent", "minimum_start_window_minutes",
    ):
        value = params.get(key)
        if value in (None, ""):
            continue
        label_key = f"notification.cleaning_param.{key}"
        label = translate(label_key, language)
        if label == label_key:
            label = key.replace("_", " ")
        normalized = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        preset_key = f"common.preset.{key}.{normalized}"
        display = translate(preset_key, language)
        if display == preset_key:
            display = str(value)
        if key == "minimum_battery_percent":
            display = f"{display}%"
        elif key == "minimum_start_window_minutes":
            display = f"{display} {translate('common.unit.minute', language)}"
        parts.append(f"{label}: {display}")
    return "; ".join(parts)


def _decision_text(value: str | None, language: str) -> str:
    if not value:
        return "—"
    raw = str(value).upper()
    key = f"common.preflight_decision.{raw}"
    translated = translate(key, language)
    return str(value) if translated == key else translated


def _zone_name(zone: SemanticZoneSnapshot) -> str:
    return zone.name or zone.zone_id


def _zone_wait_text(zone: SemanticZoneSnapshot, language: str) -> str:
    name = _zone_name(zone)
    if not zone.blockers:
        return name
    reasons = ", ".join(localized_blockers(list(zone.blockers), language))
    return f"{name} — {reasons}"


def _zone_result_text(zone: SemanticZoneSnapshot, language: str) -> str:
    name = _zone_name(zone)
    result = str(zone.result or "FAILED")
    key = f"notification.value.zone_result.{result}"
    label = translate(key, language)
    if label == key:
        label = result.lower().replace("_", " ")
    if zone.reason_code and result != "SUCCESS":
        return f"{name} — {label}: {localized_reason(zone.reason_code, language)}"
    return f"{name} — {label}"


def _append_zone_lifecycle_rows(
    rows: list[tuple[str, str]], event: SemanticNotificationEvent, language: str
) -> None:
    zones = event.zone_snapshots
    if not zones:
        return

    if event.event_type is NotificationEventType.STARTED:
        started = [z for z in zones if z.actual_start is not None or z.state == "RUNNING"]
        starting = [z for z in zones if z.state == "STARTING" and z.actual_start is None]
        waiting = [z for z in zones if z.state == "WAIT"]
        pending = [z for z in zones if z.state == "PLANNED"]
        not_run = [z for z in zones if z.state == "FINISHED" and z.result != "SUCCESS"]
        if started:
            rows.append((translate("notification.field.started_zones", language), ", ".join(_zone_name(z) for z in started)))
        if starting:
            rows.append((translate("notification.field.starting_zones", language), ", ".join(_zone_name(z) for z in starting)))
        if waiting:
            rows.append((translate("notification.field.waiting_zones", language), "; ".join(_zone_wait_text(z, language) for z in waiting)))
        if pending:
            rows.append((translate("notification.field.pending_zones", language), ", ".join(_zone_name(z) for z in pending)))
        if not_run:
            rows.append((translate("notification.field.not_run_zones", language), "; ".join(_zone_result_text(z, language) for z in not_run)))
        if event.occupancy_override_zones:
            rows.append((
                translate("notification.field.occupancy_override", language),
                ", ".join(event.occupancy_override_zones),
            ))
        return

    if event.event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}:
        waiting = [z for z in zones if z.state == "WAIT"]
        running = [z for z in zones if z.state == "RUNNING"]
        starting = [z for z in zones if z.state == "STARTING"]
        completed = [z for z in zones if z.state == "FINISHED" and z.result == "SUCCESS"]
        if waiting:
            rows.append((translate("notification.field.waiting_zones", language), "; ".join(_zone_wait_text(z, language) for z in waiting)))
        if running:
            rows.append((translate("notification.field.running_zones", language), ", ".join(_zone_name(z) for z in running)))
        if starting:
            rows.append((translate("notification.field.starting_zones", language), ", ".join(_zone_name(z) for z in starting)))
        if completed:
            rows.append((translate("notification.field.completed_zones", language), ", ".join(_zone_name(z) for z in completed)))
        return

    if event.event_type is NotificationEventType.FINISHED:
        rows.append((translate("notification.field.zone_results", language), "; ".join(_zone_result_text(z, language) for z in zones)))


def _body_rows(event: SemanticNotificationEvent, language: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if event.event_type is NotificationEventType.START_FORECAST:
        rows.append((translate("notification.title.start_forecast", language), _render_short(event, language).message))
    if event.schedule_name:
        rows.append((translate("notification.field.schedule", language), event.schedule_name))
    if event.zones and not (
        event.zone_snapshots
        and event.event_type in {
            NotificationEventType.WAIT_ENTER,
            NotificationEventType.WAIT_REMINDER,
            NotificationEventType.STARTED,
            NotificationEventType.FINISHED,
        }
    ):
        rows.append((translate("notification.field.zones", language), ", ".join(event.zones)))
    _append_zone_lifecycle_rows(rows, event, language)
    if event.planned_at is not None:
        rows.append((translate("notification.field.planned", language), _format_dt(event.planned_at, language)))
    if event.deadline_at is not None:
        rows.append((translate("notification.field.latest_start", language), _format_dt(event.deadline_at, language)))
    if event.event_type is NotificationEventType.PREWARNING:
        params = _cleaning_params_text(event.cleaning_params, language)
        if params:
            rows.append((translate("notification.field.cleaning_settings", language), params))
        rows.append((translate("notification.field.advisory_check", language), _decision_text(event.advisory_decision, language)))
        advisory = "; ".join(localized_blockers(list(event.advisory_blockers), language)) if event.advisory_blockers else translate("notification.value.none", language)
        rows.append((translate("notification.field.advisory_blockers", language), advisory))
        rows.append((translate("notification.field.current_readiness", language), _decision_text(event.current_decision, language)))
        current = "; ".join(localized_blockers(list(event.current_blockers), language)) if event.current_blockers else translate("notification.value.none", language)
        rows.append((translate("notification.field.current_blockers", language), current))
    elif event.blockers and not (
        event.zone_snapshots
        and event.event_type in {
            NotificationEventType.WAIT_ENTER,
            NotificationEventType.WAIT_REMINDER,
            NotificationEventType.STARTED,
            NotificationEventType.FINISHED,
        }
    ):
        rows.append((translate("notification.field.blockers", language), "; ".join(localized_blockers(list(event.blockers), language))))
    if event.wait_elapsed_seconds is not None and event.event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}:
        rows.append((translate("notification.field.waiting", language), _duration(event.wait_elapsed_seconds, language)))
    if event.reminder_index is not None:
        rows.append((translate("notification.field.reminder", language), f"#{event.reminder_index}"))
    if event.result:
        result_key = f"notification.value.result.{event.result}"
        result = translate(result_key, language)
        rows.append((translate("notification.field.result", language), event.result if result == result_key else result))
    if event.reason_code and (event.event_type is NotificationEventType.FINISHED or event.result == "FAILED"):
        rows.append((translate("notification.field.reason", language), localized_reason(event.reason_code, language)))
    if event.diagnostic_marker:
        rows.append((translate("notification.field.test", language), event.diagnostic_marker))
    if event.dry_run:
        rows.append((translate("notification.field.mode", language), "🧪 Dry-Run"))
    return rows


def _action_title(action: SemanticAction, language: str) -> str:
    if action.title:
        return action.title
    key = f"notification.action.{action.command}"
    translated = translate(key, language)
    return action.command if translated == key else translated

def render_plain(
    event: SemanticNotificationEvent,
    language: str,
    *,
    action_token: Callable[[str], str] | None = None,
) -> RenderedNotification:
    """Render the common plain-text fallback used by generic transports."""

    title = _title(event, language)
    rows = _body_rows(event, language)
    message = "\n".join(f"{label}: {value}" for label, value in rows)
    actions: tuple[dict[str, Any], ...] = ()
    if action_token is not None:
        actions = tuple(
            {"action": action_token(action.command), "title": _action_title(action, language)}
            for action in event.actions
        )
    return RenderedNotification(title=title, message=message, actions=actions)


def render_generic_notify(
    event: SemanticNotificationEvent, language: str
) -> RenderedNotification:
    """Render the provider-neutral HA notify fallback.

    Generic notify deliberately exposes no semantic action buttons and no
    provider-specific data.  That keeps unknown notify integrations on the
    common Home Assistant message/title contract.
    """

    return render_compact(event, language)


def render_mobile_app(
    event: SemanticNotificationEvent,
    language: str,
    *,
    action_token: Callable[[str], str],
    open_path: str = "/vacuum-schedule",
    ios_open_url: str | None = None,
    group_prefix: str = "vacuum_schedule",
    include_actions: bool = True,
) -> RenderedNotification:
    """Render one Home Assistant Companion App push.

    Mobile App notifications deliberately do not use provider-side ``tag``
    replacement or ``clear_notification`` lifecycle management.  Every semantic
    event that passes Vacuum Schedule policy/routing becomes a new user-visible
    push.  Duplicate prevention belongs to Vacuum Schedule's delivery store,
    while action callbacks are always revalidated against the current Job state.

    ``group`` is retained only as presentation metadata so the phone may group
    Vacuum Schedule notifications; unlike ``tag`` it does not identify a
    notification for replacement.
    """

    compact = render_compact(event, language)
    actions: list[dict[str, Any]] = []
    if include_actions:
        for action in event.actions:
            if action.uri:
                item: dict[str, Any] = {
                    "action": "URI",
                    "title": _action_title(action, language),
                    "uri": action.uri,
                }
            else:
                item = {
                    "action": action_token(action.command),
                    "title": _action_title(action, language),
                }
            if action.destructive or action.command in {"SKIP", "CANCEL"}:
                # iOS/macOS use this hint; Android simply ignores unsupported
                # action metadata while preserving the same event token.
                item["destructive"] = True
            actions.append(item)

    data: dict[str, Any] = {
        "group": f"{group_prefix}:jobs" if event.job_id else f"{group_prefix}:system",
    }
    if open_path:
        # The integration layer may provide a platform-specific iOS/macOS URL,
        # while Android deliberately keeps the server-relative clickAction.
        data["url"] = ios_open_url or open_path
        data["clickAction"] = open_path
    if actions:
        data["actions"] = [dict(item) for item in actions]

    return RenderedNotification(
        title=compact.title,
        message=compact.message,
        actions=tuple(actions),
        data=data,
    )


def render_pushover(
    event: SemanticNotificationEvent,
    language: str,
    *,
    open_url: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> RenderedNotification:
    """Render Pushover title/message plus provider-specific delivery metadata.

    Pushover does not expose Home Assistant callback actions, so semantic job
    actions are deliberately not rendered as buttons.  A URL back to Vacuum
    Scheduler is used instead when Home Assistant has a usable absolute URL.
    Per-channel Pushover settings remain explicit user configuration; the
    renderer never silently escalates a scheduler event to emergency priority.
    """

    compact = render_compact(event, language)
    raw = dict(options or {})
    data: dict[str, Any] = {}
    for key in ("priority", "sound", "ttl", "retry", "expire"):
        value = raw.get(key)
        if value is not None and value != "":
            data[key] = value
    if open_url:
        data["url"] = open_url
        data["url_title"] = translate("notification.open_vacuum_schedule", language)

    raw_targets = raw.get("targets")
    if isinstance(raw_targets, str):
        targets = tuple(part.strip() for part in raw_targets.split(",") if part.strip())
    elif isinstance(raw_targets, (list, tuple)):
        targets = tuple(str(item).strip() for item in raw_targets if str(item).strip())
    else:
        targets = ()

    return RenderedNotification(
        title=compact.title,
        message=compact.message,
        data=data,
        targets=targets,
    )


def render_telegram(
    event: SemanticNotificationEvent,
    language: str,
    *,
    callback_token: Callable[[str], str],
    open_url: str | None = None,
    tag_prefix: str = "vacuum_schedule",
    include_actions: bool = True,
) -> RenderedNotification:
    """Render Home Assistant Telegram Bot HTML + inline keyboard payload."""

    compact = render_compact(event, language)
    message_lines = [f"<b>{html_escape(compact.title)}</b>"]
    message_lines.extend(html_escape(line) for line in compact.message.splitlines() if line)
    keyboard: list[tuple[tuple[str, str], ...]] = []
    if include_actions and event.actions:
        keyboard.append(tuple(
            (_action_title(action, language), action.uri if action.uri else callback_token(action.command))
            for action in event.actions
        ))
    if open_url and not any(action.uri == open_url for action in event.actions if action.uri):
        keyboard.append(((translate("notification.open_home_assistant", language), open_url),))
    tag_id = event.job_id or event.event_id.replace(":", "_")
    return RenderedNotification(
        title="",
        message="\n".join(message_lines),
        inline_keyboard=tuple(keyboard),
        parse_mode="html",
        message_tag=f"{tag_prefix}:{tag_id}",
    )
