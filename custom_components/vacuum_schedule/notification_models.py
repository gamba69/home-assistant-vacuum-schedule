"""Notification policy and recipient models for Vacuum Schedule 0.8.0.

The module intentionally has no Home Assistant imports. Event eligibility,
recipient routing and persistence formats can therefore be unit-tested without
a Home Assistant runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence
from uuid import uuid4


class NotificationPreset(StrEnum):
    MINIMAL = "minimal"
    BALANCED = "balanced"
    DETAILED = "detailed"
    CUSTOM = "custom"


class PrewarningMode(StrEnum):
    OFF = "off"
    ALWAYS = "always"
    BLOCKERS_ONLY = "blockers_only"


class WaitEnterMode(StrEnum):
    OFF = "off"
    IMMEDIATE = "immediate"
    DELAYED = "delayed"


class StartForecastMode(StrEnum):
    OFF = "off"
    IMPORTANT = "important"
    ALL = "all"


class StartMode(StrEnum):
    OFF = "off"
    ALWAYS = "always"
    DEVIATION_ONLY = "deviation_only"


class FinishMode(StrEnum):
    OFF = "off"
    ALWAYS = "always"
    INCOMPLETE_ONLY = "incomplete_only"
    FAILED_ONLY = "failed_only"
    ATTENTION_ONLY = "attention_only"


class PresencePolicy(StrEnum):
    ALWAYS = "always"
    HOME_ONLY = "home_only"
    AWAY_ONLY = "away_only"
    DISABLED = "disabled"


class ChannelEventFilter(StrEnum):
    ALL = "all"
    ATTENTION_ONLY = "attention_only"
    ERROR_ONLY = "error_only"
    CUSTOM = "custom"


class TransportType(StrEnum):
    GENERIC_NOTIFY = "generic_notify"
    MOBILE_APP = "mobile_app"
    PUSHOVER = "pushover"
    TELEGRAM = "telegram"


class NotificationEventType(StrEnum):
    START_FORECAST = "start_forecast"
    PREWARNING = "prewarning"
    WAIT_ENTER = "wait_enter"
    WAIT_REMINDER = "wait_reminder"
    STARTED = "started"
    FINISHED = "finished"
    TEST = "test"


class NotificationClass(StrEnum):
    INFO = "info"
    ATTENTION = "attention"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    """Policy that decides whether scheduler lifecycle events are noteworthy."""

    prewarning_mode: PrewarningMode = PrewarningMode.BLOCKERS_ONLY
    wait_enter_mode: WaitEnterMode = WaitEnterMode.DELAYED
    wait_delay_seconds: int = 120
    wait_reminder_enabled: bool = True
    wait_reminder_interval_seconds: int = 1800
    wait_reminder_max_count: int = 3
    start_mode: StartMode = StartMode.DEVIATION_ONLY
    finish_mode: FinishMode = FinishMode.INCOMPLETE_ONLY
    start_forecast_mode: StartForecastMode = StartForecastMode.OFF
    start_forecast_interval_seconds: int = 600
    start_forecast_deadline_minutes: int = 10

    @classmethod
    def balanced(cls) -> "NotificationPolicy":
        return cls()

    @classmethod
    def minimal(cls) -> "NotificationPolicy":
        return cls(
            prewarning_mode=PrewarningMode.BLOCKERS_ONLY,
            wait_enter_mode=WaitEnterMode.DELAYED,
            wait_delay_seconds=300,
            wait_reminder_enabled=False,
            wait_reminder_interval_seconds=1800,
            wait_reminder_max_count=0,
            start_mode=StartMode.OFF,
            finish_mode=FinishMode.ATTENTION_ONLY,
        )

    @classmethod
    def detailed(cls) -> "NotificationPolicy":
        return cls(
            prewarning_mode=PrewarningMode.ALWAYS,
            wait_enter_mode=WaitEnterMode.IMMEDIATE,
            wait_delay_seconds=0,
            wait_reminder_enabled=True,
            wait_reminder_interval_seconds=1800,
            wait_reminder_max_count=3,
            start_mode=StartMode.ALWAYS,
            finish_mode=FinishMode.ALWAYS,
        )

    @classmethod
    def for_preset(cls, preset: NotificationPreset | str) -> "NotificationPolicy":
        value = NotificationPreset(str(preset))
        if value is NotificationPreset.MINIMAL:
            return cls.minimal()
        if value is NotificationPreset.DETAILED:
            return cls.detailed()
        return cls.balanced()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "NotificationPolicy":
        data = dict(raw or {})
        return cls(
            prewarning_mode=PrewarningMode(str(data.get("prewarning_mode", PrewarningMode.BLOCKERS_ONLY.value))),
            start_forecast_mode=StartForecastMode(str(data.get("start_forecast_mode", "off"))),
            start_forecast_interval_seconds=max(60, int(data.get("start_forecast_interval_seconds", 600))),
            start_forecast_deadline_minutes=max(0, int(data.get("start_forecast_deadline_minutes", 10))),
            wait_enter_mode=WaitEnterMode(str(data.get("wait_enter_mode", WaitEnterMode.DELAYED.value))),
            wait_delay_seconds=max(0, int(data.get("wait_delay_seconds", 120))),
            wait_reminder_enabled=bool(data.get("wait_reminder_enabled", True)),
            wait_reminder_interval_seconds=max(60, int(data.get("wait_reminder_interval_seconds", 1800))),
            wait_reminder_max_count=max(0, int(data.get("wait_reminder_max_count", 3))),
            start_mode=StartMode(str(data.get("start_mode", StartMode.DEVIATION_ONLY.value))),
            finish_mode=FinishMode(str(data.get("finish_mode", FinishMode.INCOMPLETE_ONLY.value))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prewarning_mode": self.prewarning_mode.value,
            "start_forecast_mode": self.start_forecast_mode.value,
            "start_forecast_interval_seconds": self.start_forecast_interval_seconds,
            "start_forecast_deadline_minutes": self.start_forecast_deadline_minutes,
            "wait_enter_mode": self.wait_enter_mode.value,
            "wait_delay_seconds": self.wait_delay_seconds,
            "wait_reminder_enabled": self.wait_reminder_enabled,
            "wait_reminder_interval_seconds": self.wait_reminder_interval_seconds,
            "wait_reminder_max_count": self.wait_reminder_max_count,
            "start_mode": self.start_mode.value,
            "finish_mode": self.finish_mode.value,
        }

    def merged(self, overrides: Mapping[str, Any] | None) -> "NotificationPolicy":
        """Return a copy with only explicitly supplied event fields overridden."""
        if not overrides:
            return self
        payload = self.to_dict()
        for key, value in dict(overrides).items():
            if value is not None and key in payload:
                payload[key] = value
        return NotificationPolicy.from_dict(payload)


@dataclass(frozen=True, slots=True)
class NotificationChannel:
    channel_id: str
    name: str
    transport_type: TransportType
    target: str
    target_kind: str = "service"  # service | entity
    enabled: bool = True
    presence_policy: PresencePolicy = PresencePolicy.ALWAYS
    event_filter: ChannelEventFilter = ChannelEventFilter.ALL
    custom_events: tuple[str, ...] = ()
    actionable: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NotificationChannel":
        return cls(
            channel_id=str(raw.get("channel_id") or uuid4().hex),
            name=str(raw.get("name") or raw.get("target") or "Notification channel").strip(),
            transport_type=TransportType(str(raw.get("transport_type", TransportType.GENERIC_NOTIFY.value))),
            target=str(raw.get("target", "")).strip(),
            target_kind=str(raw.get("target_kind", "service")),
            enabled=bool(raw.get("enabled", True)),
            presence_policy=PresencePolicy(str(raw.get("presence_policy", PresencePolicy.ALWAYS.value))),
            event_filter=ChannelEventFilter(str(raw.get("event_filter", ChannelEventFilter.ALL.value))),
            custom_events=tuple(str(item) for item in raw.get("custom_events", ()) if str(item)),
            actionable=bool(raw.get("actionable", False)),
            options=dict(raw.get("options", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "transport_type": self.transport_type.value,
            "target": self.target,
            "target_kind": self.target_kind,
            "enabled": self.enabled,
            "presence_policy": self.presence_policy.value,
            "event_filter": self.event_filter.value,
            "custom_events": list(self.custom_events),
            "actionable": self.actionable,
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class NotificationRecipient:
    recipient_id: str
    name: str
    presence_entity_id: str | None = None
    language: str = "default"
    enabled: bool = True
    channels: tuple[NotificationChannel, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NotificationRecipient":
        language = str(raw.get("language", "default")).lower()
        # 0.12.44: the old ``auto`` recipient value becomes the clearer
        # ``default`` meaning: inherit the integration language.
        if language == "auto":
            language = "default"
        if language not in {"default", "ru", "uk", "en"}:
            language = "default"
        return cls(
            recipient_id=str(raw.get("recipient_id") or uuid4().hex),
            name=str(raw.get("name") or "Recipient").strip(),
            presence_entity_id=(str(raw["presence_entity_id"]).strip() if raw.get("presence_entity_id") else None),
            language=language,
            enabled=bool(raw.get("enabled", True)),
            channels=tuple(
                NotificationChannel.from_dict(item)
                for item in raw.get("channels", ())
                if isinstance(item, Mapping)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipient_id": self.recipient_id,
            "name": self.name,
            "presence_entity_id": self.presence_entity_id,
            "language": self.language,
            "enabled": self.enabled,
            "channels": [item.to_dict() for item in self.channels],
        }


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    enabled: bool = True
    preset: NotificationPreset = NotificationPreset.BALANCED
    policy: NotificationPolicy = field(default_factory=NotificationPolicy.balanced)
    recipients: tuple[NotificationRecipient, ...] = ()
    dry_run_delivery: str = "send"  # send | log_only

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "NotificationSettings":
        data = dict(raw or {})
        preset = NotificationPreset(str(data.get("preset", NotificationPreset.BALANCED.value)))
        policy_raw = data.get("policy")
        policy = (
            NotificationPolicy.from_dict(policy_raw)
            if isinstance(policy_raw, Mapping)
            else NotificationPolicy.for_preset(preset)
        )
        return cls(
            enabled=bool(data.get("enabled", True)),
            preset=preset,
            policy=policy,
            recipients=tuple(
                NotificationRecipient.from_dict(item)
                for item in data.get("recipients", ())
                if isinstance(item, Mapping)
            ),
            dry_run_delivery=(str(data.get("dry_run_delivery", "send")) if str(data.get("dry_run_delivery", "send")) in {"send", "log_only"} else "send"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "preset": self.preset.value,
            "policy": self.policy.to_dict(),
            "recipients": [item.to_dict() for item in self.recipients],
            "dry_run_delivery": self.dry_run_delivery,
        }

    def resolved_policy(self, schedule_policy: Mapping[str, Any] | None) -> NotificationPolicy:
        """Resolve global policy plus sparse per-schedule event overrides."""
        raw = dict(schedule_policy or {})
        mode = str(raw.get("mode", "inherit"))
        if mode == "disabled":
            return NotificationPolicy(
                prewarning_mode=PrewarningMode.OFF,
                wait_enter_mode=WaitEnterMode.OFF,
                wait_delay_seconds=self.policy.wait_delay_seconds,
                wait_reminder_enabled=False,
                wait_reminder_interval_seconds=self.policy.wait_reminder_interval_seconds,
                wait_reminder_max_count=0,
                start_mode=StartMode.OFF,
                finish_mode=FinishMode.OFF,
            )
        return self.policy.merged(raw.get("overrides") if isinstance(raw.get("overrides"), Mapping) else None)


def validate_notification_settings(settings: NotificationSettings) -> None:
    recipient_ids: set[str] = set()
    channel_ids: set[str] = set()
    for recipient in settings.recipients:
        if recipient.recipient_id in recipient_ids:
            raise ValueError("duplicate_recipient_id")
        recipient_ids.add(recipient.recipient_id)
        if not recipient.name:
            raise ValueError("recipient_name_required")
        if recipient.language not in {"default", "ru", "uk", "en"}:
            raise ValueError("invalid_recipient_language")
        for channel in recipient.channels:
            if channel.channel_id in channel_ids:
                raise ValueError("duplicate_channel_id")
            channel_ids.add(channel.channel_id)
            if channel.target_kind not in {"service", "entity"}:
                raise ValueError("invalid_channel_target_kind")
            if channel.enabled and not channel.target:
                raise ValueError("notification_channel_target_required")
            if channel.target and not channel.target.startswith("notify."):
                raise ValueError("notification_channel_target_must_be_notify")
            if channel.transport_type is TransportType.PUSHOVER:
                options = dict(channel.options)
                priority = options.get("priority")
                if priority is not None and int(priority) not in {-2, -1, 0, 1, 2}:
                    raise ValueError("invalid_pushover_priority")
                if int(priority or 0) == 2:
                    if int(options.get("retry") or 0) < 30:
                        raise ValueError("pushover_emergency_retry_required")
                    expire = int(options.get("expire") or 0)
                    if expire <= 0 or expire > 10800:
                        raise ValueError("pushover_emergency_expire_required")


def normalize_notification_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    settings = NotificationSettings.from_dict(raw)
    validate_notification_settings(settings)
    return settings.to_dict()
