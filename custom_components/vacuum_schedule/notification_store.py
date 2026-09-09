"""Persistent notification event archive and delivery outbox for Vacuum Schedule."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .notification_formatting import SemanticNotificationEvent, SemanticZoneSnapshot
from .notification_models import NotificationClass, NotificationEventType

_STORAGE_VERSION = 1
_HISTORY_LIMIT = 2000
_EVENT_HISTORY_LIMIT = 1000


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class NotificationDelivery:
    delivery_id: str
    event_id: str
    job_id: str | None
    event_type: str
    notification_class: str
    recipient_id: str
    recipient_name: str
    channel_id: str
    channel_name: str
    transport_type: str
    target: str
    target_kind: str = "service"
    created_at: str = ""
    claimed_at: str | None = None
    status: str = "pending"
    sent_at: str | None = None
    attempts: int = 0
    error: str | None = None
    suppression_reason: str | None = None
    title: str = ""
    message: str = ""
    semantic_type: str = ""
    execution_mode: str = ""
    delivery_operation: str = "sent"
    provider_chat_id: str | None = None
    provider_message_id: int | None = None
    provider_entity_id: str | None = None
    provider_message_tag: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NotificationDelivery":
        required = {
            "delivery_id", "event_id", "job_id", "event_type", "notification_class",
            "recipient_id", "recipient_name", "channel_id", "channel_name",
            "transport_type", "target", "created_at",
        }
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            if name in raw:
                values[name] = raw[name]
            elif name in required:
                values[name] = None if name == "job_id" else ""
        return cls(**values)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(slots=True)
class NotificationEventRecord:
    """Immutable logical notification event, independent of delivery routes."""

    event_id: str
    job_id: str | None
    event_type: str
    notification_class: str
    semantic_type: str
    severity: str
    created_at: str
    schedule_name: str = ""
    zones: list[str] = field(default_factory=list)
    zone_snapshots: list[dict[str, Any]] = field(default_factory=list)
    planned_at: str | None = None
    deadline_at: str | None = None
    blockers: list[str] = field(default_factory=list)
    advisory_decision: str | None = None
    advisory_blockers: list[str] = field(default_factory=list)
    current_decision: str | None = None
    current_blockers: list[str] = field(default_factory=list)
    cleaning_params: dict[str, Any] = field(default_factory=dict)
    wait_elapsed_seconds: int | None = None
    result: str | None = None
    reason_code: str | None = None
    dry_run: bool = False
    reminder_index: int | None = None
    diagnostic_marker: str | None = None
    occupancy_override_zones: list[str] = field(default_factory=list)
    # Pre-0.8.12 records can be reconstructed only from rendered deliveries.
    legacy_title: str = ""
    legacy_message: str = ""
    forecast_kind: str | None = None
    compact_v2: bool = False
    duration_seconds: int | None = None
    start_source: str | None = None
    coalesced_wait_event_id: str | None = None

    @classmethod
    def from_event(cls, event: SemanticNotificationEvent) -> "NotificationEventRecord":
        return cls(
            forecast_kind=event.forecast_kind,
            compact_v2=event.compact_v2,
            duration_seconds=event.duration_seconds,
            start_source=event.start_source,
            coalesced_wait_event_id=event.coalesced_wait_event_id,
            event_id=event.event_id,
            job_id=event.job_id,
            event_type=event.event_type.value,
            notification_class=event.notification_class.value,
            semantic_type=event.semantic_type,
            severity=event.severity,
            created_at=event.created_at.isoformat(),
            schedule_name=event.schedule_name,
            zones=list(event.zones),
            zone_snapshots=[
                {
                    "zone_id": zone.zone_id,
                    "name": zone.name,
                    "state": zone.state,
                    "result": zone.result,
                    "reason_code": zone.reason_code,
                    "blockers": list(zone.blockers),
                    "actual_start": _iso(zone.actual_start),
                }
                for zone in event.zone_snapshots
            ],
            planned_at=_iso(event.planned_at),
            deadline_at=_iso(event.deadline_at),
            blockers=list(event.blockers),
            advisory_decision=event.advisory_decision,
            advisory_blockers=list(event.advisory_blockers),
            current_decision=event.current_decision,
            current_blockers=list(event.current_blockers),
            cleaning_params=dict(event.cleaning_params),
            wait_elapsed_seconds=event.wait_elapsed_seconds,
            result=event.result,
            reason_code=event.reason_code,
            dry_run=bool(event.dry_run),
            reminder_index=event.reminder_index,
            diagnostic_marker=event.diagnostic_marker,
            occupancy_override_zones=list(event.occupancy_override_zones),
        )

    @classmethod
    def from_delivery(cls, delivery: NotificationDelivery) -> "NotificationEventRecord":
        """Best-effort migration of a retained pre-0.8.12 delivery."""
        return cls(
            event_id=delivery.event_id,
            job_id=delivery.job_id,
            event_type=delivery.event_type,
            notification_class=delivery.notification_class,
            semantic_type=delivery.semantic_type or delivery.event_type,
            severity=("error" if delivery.notification_class == "error" else "warning" if delivery.notification_class == "attention" else "info"),
            created_at=delivery.created_at,
            dry_run=str(delivery.execution_mode).upper() == "DRY_RUN",
            legacy_title=delivery.title,
            legacy_message=delivery.message,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NotificationEventRecord":
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            if name in raw:
                values[name] = raw[name]
        values.setdefault("event_id", "")
        values.setdefault("job_id", None)
        values.setdefault("event_type", NotificationEventType.TEST.value)
        values.setdefault("notification_class", NotificationClass.INFO.value)
        values.setdefault("semantic_type", "")
        values.setdefault("severity", "info")
        values.setdefault("created_at", "")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def to_event(self) -> SemanticNotificationEvent:
        try:
            event_type = NotificationEventType(self.event_type)
        except ValueError:
            event_type = NotificationEventType.TEST
        try:
            notification_class = NotificationClass(self.notification_class)
        except ValueError:
            notification_class = NotificationClass.INFO
        created_at = _dt(self.created_at) or datetime.now()
        zones = tuple(
            SemanticZoneSnapshot(
                zone_id=str(item.get("zone_id", "")),
                name=str(item.get("name", "")),
                state=str(item.get("state", "")),
                result=(str(item["result"]) if item.get("result") is not None else None),
                reason_code=(str(item["reason_code"]) if item.get("reason_code") is not None else None),
                blockers=tuple(str(value) for value in item.get("blockers", ())),
                actual_start=_dt(item.get("actual_start")),
            )
            for item in self.zone_snapshots
            if isinstance(item, Mapping)
        )
        return SemanticNotificationEvent(
            forecast_kind=self.forecast_kind,
            compact_v2=self.compact_v2,
            duration_seconds=self.duration_seconds,
            start_source=self.start_source,
            coalesced_wait_event_id=self.coalesced_wait_event_id,
            event_id=self.event_id,
            semantic_type=self.semantic_type,
            event_type=event_type,
            notification_class=notification_class,
            severity=self.severity,
            created_at=created_at,
            job_id=self.job_id,
            schedule_name=self.schedule_name,
            zones=tuple(str(item) for item in self.zones),
            zone_snapshots=zones,
            planned_at=_dt(self.planned_at),
            deadline_at=_dt(self.deadline_at),
            blockers=tuple(str(item) for item in self.blockers),
            advisory_decision=self.advisory_decision,
            advisory_blockers=tuple(str(item) for item in self.advisory_blockers),
            current_decision=self.current_decision,
            current_blockers=tuple(str(item) for item in self.current_blockers),
            cleaning_params=dict(self.cleaning_params),
            wait_elapsed_seconds=self.wait_elapsed_seconds,
            result=self.result,
            reason_code=self.reason_code,
            dry_run=bool(self.dry_run),
            actions=(),
            reminder_index=self.reminder_index,
            diagnostic_marker=self.diagnostic_marker,
            occupancy_override_zones=tuple(str(item) for item in self.occupancy_override_zones),
        )


class NotificationStore:
    """Persist logical messages, delivery dedup state and a bounded delivery log."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, _STORAGE_VERSION, f"vacuum_schedule.{entry_id}.notifications")
        self.deliveries: dict[str, NotificationDelivery] = {}
        self.history: list[str] = []
        self.events: dict[str, NotificationEventRecord] = {}
        self.event_history: list[str] = []
        self.start_forecasts: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        raw = await self._store.async_load() or {}
        self.start_forecasts = {
            str(key): dict(value) for key, value in dict(raw.get("start_forecasts", {})).items()
            if isinstance(value, Mapping)
        }
        self.deliveries = {}
        for item in raw.get("deliveries", []):
            if not isinstance(item, Mapping):
                continue
            try:
                delivery = NotificationDelivery.from_dict(item)
            except (TypeError, ValueError):
                continue
            if delivery.delivery_id:
                self.deliveries[delivery.delivery_id] = delivery
        self.history = [str(item) for item in raw.get("history", []) if str(item)][-_HISTORY_LIMIT:]

        self.events = {}
        for item in raw.get("events", []):
            if not isinstance(item, Mapping):
                continue
            try:
                record = NotificationEventRecord.from_dict(item)
            except (TypeError, ValueError):
                continue
            if record.event_id:
                self.events[record.event_id] = record
        self.event_history = [str(item) for item in raw.get("event_history", []) if str(item)][-_EVENT_HISTORY_LIMIT:]
        self.event_history = [key for key in self.event_history if key in self.events]

        # Backward compatibility: old stores had only delivery records. Build one
        # logical message per semantic event without duplicating recipient routes.
        if not self.events:
            for delivery_id in self.history:
                delivery = self.deliveries.get(delivery_id)
                if delivery is None or not delivery.event_id or delivery.event_id in self.events:
                    continue
                record = NotificationEventRecord.from_delivery(delivery)
                self.events[record.event_id] = record
                self.event_history.append(record.event_id)
            self.event_history = self.event_history[-_EVENT_HISTORY_LIMIT:]

    async def async_save(self) -> None:
        keys = [key for key in self.history[-_HISTORY_LIMIT:] if key in self.deliveries]
        values = [self.deliveries[key].to_dict() for key in keys]
        event_keys = [key for key in self.event_history[-_EVENT_HISTORY_LIMIT:] if key in self.events]
        event_values = [self.events[key].to_dict() for key in event_keys]
        await self._store.async_save({
            "start_forecasts": dict(list(self.start_forecasts.items())[-_EVENT_HISTORY_LIMIT:]),
            "deliveries": values,
            "history": keys,
            "events": event_values,
            "event_history": event_keys,
        })

    def get(self, delivery_id: str) -> NotificationDelivery | None:
        return self.deliveries.get(delivery_id)

    def get_event(self, event_id: str) -> NotificationEventRecord | None:
        return self.events.get(str(event_id))

    def put_event(self, event: SemanticNotificationEvent) -> NotificationEventRecord:
        existing = self.events.get(event.event_id)
        if existing is not None:
            return existing
        record = NotificationEventRecord.from_event(event)
        self.events[record.event_id] = record
        self.event_history.append(record.event_id)
        self.event_history = self.event_history[-_EVENT_HISTORY_LIMIT:]
        valid = set(self.event_history)
        self.events = {key: value for key, value in self.events.items() if key in valid}
        return record

    def endpoint_claim(
        self, event_id: str, target_kind: str, target: str
    ) -> NotificationDelivery | None:
        for key in reversed(self.history):
            item = self.deliveries.get(key)
            if item is None:
                continue
            if (
                item.event_id == event_id
                and item.target_kind == target_kind
                and item.target == target
                and (
                    item.status in {"pending", "sent"}
                    or (item.status == "failed" and item.attempts > 0)
                )
            ):
                return item
        return None

    def put(self, item: NotificationDelivery) -> None:
        self.deliveries[item.delivery_id] = item
        if item.delivery_id not in self.history:
            self.history.append(item.delivery_id)
        self.history = self.history[-_HISTORY_LIMIT:]
        valid = set(self.history)
        self.deliveries = {key: value for key, value in self.deliveries.items() if key in valid}

    def latest_for_job_channel(
        self, job_id: str, recipient_id: str, channel_id: str, *, transport_type: str | None = None
    ) -> NotificationDelivery | None:
        for key in reversed(self.history):
            item = self.deliveries.get(key)
            if item is None or item.status != "sent":
                continue
            if item.job_id != job_id or item.recipient_id != recipient_id or item.channel_id != channel_id:
                continue
            if transport_type is not None and item.transport_type != transport_type:
                continue
            return item
        return None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key in reversed(self.history[-max(1, limit):]):
            item = self.deliveries.get(key)
            if item is not None:
                result.append(item.to_dict())
        return result

    def recent_events(self, limit: int = 50) -> list[NotificationEventRecord]:
        result: list[NotificationEventRecord] = []
        for key in reversed(self.event_history[-max(1, limit):]):
            item = self.events.get(key)
            if item is not None:
                result.append(item)
        return result

    def deliveries_for_event(self, event_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        key = str(event_id)
        for delivery_id in self.history:
            item = self.deliveries.get(delivery_id)
            if item is not None and item.event_id == key:
                result.append(item.to_dict())
        return result

    def event_id_for_delivery(self, delivery_id: str) -> str | None:
        item = self.get(delivery_id)
        return item.event_id if item is not None else None

    def for_job(self, job_id: str) -> list[dict[str, Any]]:
        key = str(job_id)
        result: list[dict[str, Any]] = []
        for delivery_id in self.history:
            item = self.deliveries.get(delivery_id)
            if item is not None and item.job_id == key:
                result.append(item.to_dict())
        return result

    def clear(self) -> None:
        self.start_forecasts.clear()
        self.deliveries.clear()
        self.history.clear()
        self.events.clear()
        self.event_history.clear()

    def clear_simulation_jobs(self, job_ids: set[str]) -> None:
        for job_id in job_ids:
            self.start_forecasts.pop(job_id, None)
        remove = {key for key, item in self.deliveries.items() if item.job_id in job_ids}
        if remove:
            self.history = [key for key in self.history if key not in remove]
            for key in remove:
                self.deliveries.pop(key, None)
        remove_events = {key for key, item in self.events.items() if item.job_id in job_ids}
        if remove_events:
            self.event_history = [key for key in self.event_history if key not in remove_events]
            for key in remove_events:
                self.events.pop(key, None)
