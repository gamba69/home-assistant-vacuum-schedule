"""Semantic notification routing and delivery for Vacuum Schedule 0.8.0."""

from __future__ import annotations

from dataclasses import replace
import asyncio
import hashlib
from datetime import datetime, timedelta
import logging
from typing import Any, Mapping
from urllib.parse import urlencode

from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_INTERFACE_LANGUAGE,
    CONF_NOTIFICATION_SETTINGS,
    CONF_SCHEDULES,
    SUPPORTED_PRESENTATION_LANGUAGES,
)
from .job import JobInstance, JobResult, JobState, ZoneJobState
from .execution_models import ExecutionMode
from .localization import normalize_language, translate
from .notification_formatting import (
    RenderedNotification,
    SemanticAction,
    SemanticNotificationEvent,
    SemanticZoneSnapshot,
    render_compact,
    render_generic_notify,
    render_mobile_app,
    render_plain,
    render_pushover,
    render_telegram,
    semantic_type_for,
)
from .notification_models import (
    ChannelEventFilter,
    FinishMode,
    NotificationChannel,
    NotificationClass,
    NotificationEventType,
    NotificationPolicy,
    NotificationRecipient,
    NotificationSettings,
    PresencePolicy,
    PrewarningMode,
    StartMode,
    StartForecastMode,
    TransportType,
    WaitEnterMode,
)
from .notification_store import NotificationDelivery, NotificationStore
from .schedule import ScheduleDefinition
from .time_utils import as_utc, instant_add, instant_delta, instant_ge, instant_lt
from .start_forecast import canonical_codes, forecast_candidate, has_started, readiness

_LOGGER = logging.getLogger(__name__)

_USER_INITIATED_REASONS = {
    "user_skipped",
    "user_cancelled",
    "schedule_disabled_by_user",
}

_TELEGRAM_COMMAND_CODES = {
    "START_NOW": "N",
    "SKIP": "S",
    "RECHECK": "R",
    "CANCEL": "C",
}
_TELEGRAM_CODE_COMMANDS = {value: key for key, value in _TELEGRAM_COMMAND_CODES.items()}


class NotificationManager:
    """Resolve semantic events, policy/routing and transport-specific delivery."""

    def __init__(self, hass: HomeAssistant, entry: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.store = NotificationStore(hass, self.entry_id)
        self._transport_state_sync: set[str] = set()
        # Delivery claims are serialized only for the very short outbox-claim
        # section.  The network send itself happens outside the lock.  This
        # makes duplicate prevention atomic across concurrent zone timers while
        # preserving parallel delivery to independent endpoints.
        self._delivery_claim_lock = asyncio.Lock()
        # Serialize lifecycle notification evaluation per Job.  Different
        # semantic event families can describe the same user-visible transition
        # (notably STARTED and START_FORECAST(kind=started)).  Without this
        # boundary, concurrent scheduler wake-ups may evaluate route coalescing
        # while the earlier STARTED delivery is still only ``pending`` and send
        # both pushes.  Per-job serialization preserves lifecycle order without
        # blocking notification delivery for unrelated Jobs.
        self._job_process_locks: dict[str, asyncio.Lock] = {}

    async def async_start(self) -> None:
        await self.store.async_load()

    async def async_stop(self) -> None:
        await self.store.async_save()

    @property
    def settings(self) -> NotificationSettings:
        return NotificationSettings.from_dict(self.entry.options.get(CONF_NOTIFICATION_SETTINGS, {}))

    def _schedule(self, schedule_id: str) -> ScheduleDefinition | None:
        for raw in self.entry.options.get(CONF_SCHEDULES, ()):
            try:
                schedule = ScheduleDefinition.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if schedule.schedule_id == schedule_id:
                return schedule
        return None

    def policy_for_job(self, job: JobInstance) -> tuple[NotificationPolicy, set[str] | None]:
        settings = self.settings
        schedule = self._schedule(job.schedule_id)
        snapshot = job.metadata.get("notification_policy_snapshot")
        raw: Mapping[str, Any] | None = (
            schedule.notification_policy
            if schedule is not None
            else snapshot if isinstance(snapshot, Mapping) else None
        )
        recipient_ids: set[str] | None = None
        if isinstance(raw, Mapping) and isinstance(raw.get("recipient_ids"), list):
            recipient_ids = {str(item) for item in raw.get("recipient_ids", []) if str(item)}
        return settings.resolved_policy(raw), recipient_ids

    @staticmethod
    def _first_actual_start(job: JobInstance) -> datetime | None:
        starts = [run.actual_start for run in job.zone_runs.values() if run.actual_start is not None]
        return min(starts, key=as_utc) if starts else job.actual_start

    @staticmethod
    def _is_start_deviation(job: JobInstance) -> bool:
        first = NotificationManager._first_actual_start(job)
        # Any WAIT cycle is a historical execution deviation even after the
        # continuous WAIT timer has been reset on leaving WAIT.
        if int(getattr(job, "wait_cycle", 0)) > 0:
            return True
        if first is not None and abs(instant_delta(first, job.planned_start).total_seconds()) > 5:
            return True
        if job.manual_release_at is not None:
            return True
        if first is not None and job.zone_runs:
            # A first zone starting while another intended zone has not started
            # (including a zone already failed/skipped by pre-flight) is a
            # partial/deviating start and must be visible in DEVIATION_ONLY mode.
            if any(run.actual_start is None for run in job.zone_runs.values()):
                return True
        return False

    @staticmethod
    def _finish_needs_attention(job: JobInstance) -> bool:
        if job.result is JobResult.SUCCESS:
            return False
        if str(job.reason_code or "") in _USER_INITIATED_REASONS:
            return False
        return True

    def _policy_event_due(
        self, job: JobInstance, event_type: NotificationEventType, policy: NotificationPolicy, now: datetime
    ) -> bool:
        if event_type is NotificationEventType.PREWARNING:
            if job.advisory_checked_at is None or instant_ge(now, job.effective_start):
                return False
            if policy.prewarning_mode is PrewarningMode.OFF:
                return False
            if policy.prewarning_mode is PrewarningMode.BLOCKERS_ONLY:
                return bool(job.current_blockers if job.current_preflight_decision else job.advisory_blockers)
            return True
        if event_type is NotificationEventType.WAIT_ENTER:
            wait_context = self._wait_context(job)
            if wait_context is None:
                return False
            if policy.wait_enter_mode is WaitEnterMode.OFF:
                return False
            _signature, wait_since = wait_context
            due = wait_since
            if policy.wait_enter_mode is WaitEnterMode.DELAYED:
                due = instant_add(due, timedelta(seconds=policy.wait_delay_seconds))
            return instant_ge(now, due)
        if event_type is NotificationEventType.STARTED:
            if self._first_actual_start(job) is None:
                return False
            if policy.start_mode is StartMode.OFF:
                return False
            if policy.start_mode is StartMode.DEVIATION_ONLY:
                return self._is_start_deviation(job)
            return True
        if event_type is NotificationEventType.FINISHED:
            if not job.terminal or job.result is None:
                return False
            if policy.finish_mode is FinishMode.OFF:
                return False
            if policy.finish_mode is FinishMode.INCOMPLETE_ONLY:
                return (
                    job.result is not JobResult.SUCCESS
                    and str(job.reason_code or "") not in _USER_INITIATED_REASONS
                )
            if policy.finish_mode is FinishMode.FAILED_ONLY:
                return job.result is JobResult.FAILED
            if policy.finish_mode is FinishMode.ATTENTION_ONLY:
                return self._finish_needs_attention(job)
            return True
        return False

    def next_due_at(self, job: JobInstance) -> datetime | None:
        """Return the next policy timer required for one current job."""
        settings = self.settings
        if not settings.enabled or not settings.recipients or job.terminal:
            return None
        policy, _ = self.policy_for_job(job)
        candidates: list[datetime] = []
        if policy.start_forecast_mode is not StartForecastMode.OFF and not has_started(job):
            state = getattr(self.store, "start_forecasts", {}).get(job.job_id, {})
            if state.get("due_at"):
                candidates.append(datetime.fromisoformat(state["due_at"]))
            if policy.start_forecast_deadline_minutes and not state.get("closing_sent"):
                closing_at = instant_add(job.deadline_at, -timedelta(minutes=policy.start_forecast_deadline_minutes))
                if not state.get("closing_observed"):
                    candidates.append(max((job.warning_at, closing_at), key=as_utc))
        wait_context = self._wait_context(job)
        if wait_context is not None:
            _signature, wait_since = wait_context
            wait_id = self._event_id(job, NotificationEventType.WAIT_ENTER)
            if not self._event_has_final_delivery(wait_id):
                if policy.wait_enter_mode is WaitEnterMode.IMMEDIATE:
                    candidates.append(wait_since)
                elif policy.wait_enter_mode is WaitEnterMode.DELAYED:
                    candidates.append(instant_add(wait_since, timedelta(seconds=policy.wait_delay_seconds)))
            if policy.wait_reminder_enabled and policy.wait_reminder_max_count > 0:
                base = wait_since
                if policy.wait_enter_mode is WaitEnterMode.DELAYED:
                    base = instant_add(base, timedelta(seconds=policy.wait_delay_seconds))
                for index in range(1, policy.wait_reminder_max_count + 1):
                    event_id = self._event_id(job, NotificationEventType.WAIT_REMINDER, index)
                    if not self._event_has_final_delivery(event_id):
                        candidates.append(instant_add(base, timedelta(seconds=policy.wait_reminder_interval_seconds * index)))
                        break
        return min(candidates, key=as_utc) if candidates else None

    def _event_has_final_delivery(self, event_id: str) -> bool:
        prefix = f"{event_id}:"
        return any(key.startswith(prefix) for key in self.store.deliveries)

    @staticmethod
    def _waiting_zone_runs(job: JobInstance) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (zone_id, run)
            for zone_id, run in sorted(job.zone_runs.items())
            if run.state is ZoneJobState.WAIT and run.first_wait_at is not None
        )

    @classmethod
    def _wait_context(cls, job: JobInstance) -> tuple[str, datetime] | None:
        """Return the current Job-level WAIT episode signature and start time.

        A Job can be RUNNING while another zone independently waits.  WAIT
        notifications therefore follow the set of waiting zone lifecycles, not
        only the aggregate JobState.  Re-entering WAIT increments each zone's
        durable wait_cycle and naturally creates a new notification episode.
        """
        waiting = cls._waiting_zone_runs(job)
        if waiting:
            token = "|".join(
                f"{zone_id}:{max(1, int(getattr(run, 'wait_cycle', 0)))}"
                for zone_id, run in waiting
            )
            signature = hashlib.sha1(token.encode("utf-8")).hexdigest()[:12]
            since = min((run.first_wait_at for _zone_id, run in waiting), key=as_utc)
            return signature, since
        # Migration fallback for an old persisted Job with aggregate WAIT data
        # but no per-zone WAIT metadata.
        if job.state is JobState.WAIT and job.first_wait_at is not None:
            return f"legacy-{max(1, int(getattr(job, 'wait_cycle', 0)))}", job.first_wait_at
        return None

    @classmethod
    def _partial_start_wait_event_id(cls, job: JobInstance) -> str | None:
        """Return the current WAIT-enter id when STARTED already summarizes it.

        A progressive multi-zone Job can confirm one zone as RUNNING while a
        second zone is still WAITing in the same lifecycle snapshot.  The
        STARTED notification already contains both the started and waiting zone
        rows, so a separate WAIT_ENTER push would repeat the same user-visible
        fact.  Persisting the exact WAIT episode id on STARTED lets routing
        suppress only that episode; a later WAIT re-entry has a different id
        and remains independently notifiable.
        """
        if cls._first_actual_start(job) is None:
            return None
        waiting = cls._waiting_zone_runs(job)
        if not waiting:
            return None
        has_started_zone = any(
            run.actual_start is not None or run.state is ZoneJobState.RUNNING
            for run in job.zone_runs.values()
        )
        if not has_started_zone:
            return None
        return cls._event_id(job, NotificationEventType.WAIT_ENTER)

    @classmethod
    def _event_id(cls, job: JobInstance, kind: NotificationEventType, index: int | None = None) -> str:
        if kind in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}:
            context = cls._wait_context(job)
            signature = context[0] if context is not None else "no-wait"
            suffix = f":{signature}" + (f":{index}" if index is not None else "")
            return f"{job.job_id}:{kind.value}{suffix}"
        suffix = f":{index}" if index is not None else ""
        return f"{job.job_id}:{kind.value}{suffix}"

    def _language(self) -> str:
        configured = str(self.entry.options.get(CONF_INTERFACE_LANGUAGE, "auto") or "auto").lower()
        if configured in SUPPORTED_PRESENTATION_LANGUAGES:
            return normalize_language(configured)
        # There is no active frontend user during unattended delivery. The HA
        # server locale is therefore the deterministic backend fallback for an
        # integration language configured as Automatic.
        return normalize_language(getattr(self.hass.config, "language", "en"))

    def _recipient_language(
        self, recipient: NotificationRecipient, explicit: str | None = None
    ) -> str:
        """Resolve one delivery locale.

        Automatic scheduler notifications must not depend only on
        ``hass.config.language`` because Home Assistant users may select a
        different frontend language. Recipient profiles therefore persist the
        language used for their notifications. Explicit language is reserved
        for diagnostic/test sends.
        """
        if explicit:
            return normalize_language(explicit)
        value = str(recipient.language or "default").lower()
        if value in SUPPORTED_PRESENTATION_LANGUAGES:
            return normalize_language(value)
        return self._language()

    @staticmethod
    def _localized_action_title(command: str, language: str) -> str:
        key = f"notification.action.{command}"
        value = translate(key, language)
        return str(command) if value == key else value

    def _localize_event_actions(
        self, event: SemanticNotificationEvent, language: str
    ) -> SemanticNotificationEvent:
        if not event.actions:
            return event
        return replace(
            event,
            actions=tuple(
                replace(action, title=self._localized_action_title(action.command, language))
                for action in event.actions
            ),
        )

    def _target_names(self, job: JobInstance) -> tuple[str, ...]:
        names = tuple(run.zone_name or run.zone_id for run in job.zone_runs.values())
        return names or tuple(job.targets)

    @staticmethod
    def _zone_snapshots(job: JobInstance) -> tuple[SemanticZoneSnapshot, ...]:
        return tuple(
            SemanticZoneSnapshot(
                zone_id=zone_id,
                name=run.zone_name or zone_id,
                state=run.state.value,
                result=run.result.value if run.result is not None else None,
                reason_code=run.reason_code,
                blockers=tuple(str(item) for item in run.blockers),
                actual_start=run.actual_start,
            )
            for zone_id, run in sorted(job.zone_runs.items())
        )

    @staticmethod
    def _blockers(job: JobInstance, advisory: bool = False) -> tuple[str, ...]:
        values = job.advisory_blockers if advisory else job.current_blockers
        return tuple(str(item) for item in values)

    def _semantic_actions(
        self, event_type: NotificationEventType, job: JobInstance | None = None
    ) -> tuple[SemanticAction, ...]:
        if event_type is NotificationEventType.PREWARNING:
            return (SemanticAction("START_NOW", ""), SemanticAction("SKIP", ""))
        if event_type in (NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER):
            # A partial Job may already be RUNNING while another zone waits.
            # SKIP is not valid in that aggregate state; CANCEL is.
            has_started_work = bool(
                job is not None
                and any(
                    run.state in {ZoneJobState.STARTING, ZoneJobState.RUNNING}
                    or run.actual_start is not None
                    for run in job.zone_runs.values()
                )
            )
            command = "CANCEL" if has_started_work else "SKIP"
            return (SemanticAction("RECHECK", ""), SemanticAction(command, ""))
        if event_type is NotificationEventType.STARTED:
            return (SemanticAction("CANCEL", ""),)
        return ()

    def _build_event(
        self,
        job: JobInstance,
        event_type: NotificationEventType,
        now: datetime,
        *,
        reminder_index: int | None = None,
    ) -> SemanticNotificationEvent:
        advisory_blockers = self._blockers(job, advisory=True)
        current_blockers = self._blockers(job, advisory=False)
        waiting_blockers = tuple(
            sorted(
                {
                    str(blocker)
                    for _zone_id, run in self._waiting_zone_runs(job)
                    for blocker in run.blockers
                }
            )
        )
        blockers = (
            current_blockers
            if event_type is NotificationEventType.PREWARNING
            else waiting_blockers
            if event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}
            else current_blockers
        )
        if event_type is NotificationEventType.PREWARNING:
            has_blockers = bool(current_blockers if job.current_preflight_decision else advisory_blockers)
            notification_class = NotificationClass.ATTENTION if has_blockers else NotificationClass.INFO
            severity = "warning" if has_blockers else "info"
        elif event_type in (NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER):
            notification_class = NotificationClass.ATTENTION
            severity = "warning"
        elif event_type is NotificationEventType.STARTED:
            deviation = self._is_start_deviation(job)
            notification_class = NotificationClass.ATTENTION if deviation else NotificationClass.INFO
            severity = "warning" if deviation else "info"
        elif event_type is NotificationEventType.FINISHED:
            if job.result is JobResult.SUCCESS:
                notification_class = NotificationClass.INFO
                severity = "success"
            elif job.result in {JobResult.PARTIAL_SUCCESS, JobResult.SUPPRESSED}:
                notification_class = NotificationClass.ATTENTION
                severity = "warning"
            else:
                user_reason = str(job.reason_code or "") in _USER_INITIATED_REASONS
                notification_class = NotificationClass.ATTENTION if user_reason else NotificationClass.ERROR
                severity = "warning" if user_reason else "error"
        else:
            notification_class = NotificationClass.INFO
            severity = "info"

        wait_elapsed = None
        if event_type in (NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER):
            wait_context = self._wait_context(job)
            if wait_context is not None:
                _wait_signature, wait_since = wait_context
                if event_type is NotificationEventType.WAIT_ENTER:
                    # The first WAIT notification title should describe the
                    # configured user-visible delay, not scheduler jitter. An
                    # immediate policy therefore stays simply "Waiting".
                    policy, _ = self.policy_for_job(job)
                    wait_elapsed = (
                        max(0, int(policy.wait_delay_seconds))
                        if policy.wait_enter_mode is WaitEnterMode.DELAYED
                        else 0
                    )
                else:
                    wait_elapsed = max(0, int(instant_delta(now, wait_since).total_seconds()))

        result = job.result.value if job.result is not None else None
        reason = str(job.reason_code) if job.reason_code else None
        name = job.schedule_name or translate("notification.value.manual_cleaning", self._language())
        manual_overrides = job.metadata.get("manual_overrides", {})
        ignored_busy_ids = {
            str(item)
            for item in (
                manual_overrides.get("ignore_busy_zones", ())
                if isinstance(manual_overrides, dict)
                else ()
            )
        }
        occupancy_override_zones = tuple(
            (job.zone_runs[zone_id].zone_name or zone_id)
            for zone_id in sorted(ignored_busy_ids)
            if zone_id in job.zone_runs
        )
        policy, _ = self.policy_for_job(job)
        closing_note = (
            event_type in {NotificationEventType.PREWARNING, NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER}
            and policy.start_forecast_mode is not StartForecastMode.OFF
            and policy.start_forecast_deadline_minutes > 0
            and not has_started(job) and not job.terminal
            and instant_lt(now, job.deadline_at)
            and instant_ge(now, instant_add(job.deadline_at, -timedelta(minutes=policy.start_forecast_deadline_minutes)))
            and not getattr(self.store, "start_forecasts", {}).get(job.job_id, {}).get("closing_sent")
        )
        return SemanticNotificationEvent(
            event_id=self._event_id(job, event_type, reminder_index),
            forecast_kind="closing" if closing_note else None,
            compact_v2=True,
            duration_seconds=(
                max(0, int(instant_delta(job.finished_at, self._first_actual_start(job)).total_seconds()))
                if job.finished_at and self._first_actual_start(job) else None
            ),
            start_source=(
                "manual" if str(job.execution_source) == "MANUAL" else
                "early" if str(job.execution_source) == "FORCE" else
                "waited" if int(getattr(job, "wait_cycle", 0)) > 0 else "started"
            ),
            coalesced_wait_event_id=(
                self._partial_start_wait_event_id(job)
                if event_type is NotificationEventType.STARTED
                else None
            ),
            semantic_type=semantic_type_for(
                event_type,
                result=result,
                reason_code=reason,
                blockers=(advisory_blockers or current_blockers)
                if event_type is NotificationEventType.PREWARNING
                else blockers,
            ),
            event_type=event_type,
            notification_class=notification_class,
            severity=severity,
            created_at=now,
            job_id=job.job_id,
            schedule_name=name,
            zones=self._target_names(job),
            zone_snapshots=self._zone_snapshots(job),
            planned_at=job.planned_start,
            deadline_at=job.deadline_at,
            blockers=blockers,
            advisory_decision=job.advisory_preflight_decision if event_type is NotificationEventType.PREWARNING else None,
            advisory_blockers=advisory_blockers if event_type is NotificationEventType.PREWARNING else (),
            current_decision=job.current_preflight_decision if event_type is NotificationEventType.PREWARNING else None,
            current_blockers=current_blockers if event_type is NotificationEventType.PREWARNING else (),
            cleaning_params=dict(job.cleaning_params) if event_type is NotificationEventType.PREWARNING else {},
            wait_elapsed_seconds=wait_elapsed,
            result=result,
            reason_code=reason,
            dry_run=job.execution_mode is ExecutionMode.DRY_RUN,
            actions=self._semantic_actions(event_type, job),
            reminder_index=reminder_index,
            occupancy_override_zones=(
                occupancy_override_zones
                if event_type is NotificationEventType.STARTED
                else ()
            ),
        )

    def _test_event(
        self,
        event_type: NotificationEventType = NotificationEventType.TEST,
        notification_class: NotificationClass = NotificationClass.INFO,
        *,
        language: str | None = None,
    ) -> SemanticNotificationEvent:
        now = datetime.now()
        language = normalize_language(language or self._language())
        severity = "error" if notification_class is NotificationClass.ERROR else "warning" if notification_class is NotificationClass.ATTENTION else "info"
        blockers: tuple[str, ...] = ()
        wait_elapsed: int | None = None
        reminder_index: int | None = None
        result: str | None = None
        reason_code: str | None = None
        planned_at: datetime | None = None
        deadline_at: datetime | None = None
        advisory_decision: str | None = None
        current_decision: str | None = None
        advisory_blockers: tuple[str, ...] = ()
        current_blockers: tuple[str, ...] = ()
        cleaning_params: dict[str, Any] = {}
        zones: tuple[str, ...] = (translate("notification.value.test_zone", language),)
        zone_snapshots: tuple[SemanticZoneSnapshot, ...] = ()
        if event_type is NotificationEventType.PREWARNING:
            blockers = ("zone_busy",)
            advisory_blockers = blockers
            current_blockers = blockers
            advisory_decision = "WAIT"
            current_decision = "WAIT"
            cleaning_params = {"cleaning_mode":"vac_and_mop","fan_mode":"balanced","passes":2,"water_mode":"standard"}
            planned_at = now + timedelta(minutes=15)
        elif event_type is NotificationEventType.START_FORECAST:
            blockers = ("clean_water_insufficient",)
            planned_at = now + timedelta(minutes=15)
        elif event_type is NotificationEventType.WAIT_ENTER:
            blockers = ("zone_busy",)
            zone_snapshots = (SemanticZoneSnapshot("test", zones[0], "WAIT", blockers=blockers),)
            wait_elapsed = 120
            planned_at = now - timedelta(minutes=2)
            deadline_at = now + timedelta(minutes=30)
        elif event_type is NotificationEventType.WAIT_REMINDER:
            blockers = ("zone_busy",)
            zone_snapshots = (SemanticZoneSnapshot("test", zones[0], "WAIT", blockers=blockers),)
            wait_elapsed = 1800
            reminder_index = 1
            planned_at = now - timedelta(minutes=30)
            deadline_at = now + timedelta(minutes=30)
        elif event_type is NotificationEventType.STARTED:
            planned_at = now - timedelta(minutes=5)
            zone_snapshots = (SemanticZoneSnapshot("test", zones[0], "RUNNING", actual_start=now),)
        elif event_type is NotificationEventType.FINISHED:
            planned_at = now - timedelta(minutes=35)
            result = "FAILED"
            reason_code = "preflight_failed"
            zone_snapshots = (SemanticZoneSnapshot("test", zones[0], "FINISHED", result="FAILED", reason_code=reason_code),)

        return SemanticNotificationEvent(
            event_id=f"test-event:{event_type.value}:{now.timestamp()}",
            compact_v2=True,
            forecast_kind="attention" if event_type is NotificationEventType.START_FORECAST else None,
            semantic_type=semantic_type_for(
                event_type, result=result, reason_code=reason_code, blockers=blockers
            ),
            event_type=event_type,
            notification_class=notification_class,
            severity=severity,
            created_at=now,
            schedule_name=translate("notification.value.test_schedule", language),
            zones=zones,
            zone_snapshots=zone_snapshots,
            planned_at=planned_at,
            deadline_at=deadline_at,
            blockers=blockers,
            advisory_decision=advisory_decision,
            advisory_blockers=advisory_blockers,
            current_decision=current_decision,
            current_blockers=current_blockers,
            cleaning_params=cleaning_params,
            wait_elapsed_seconds=wait_elapsed,
            result=result,
            reason_code=reason_code,
            dry_run=True,
            reminder_index=reminder_index,
            diagnostic_marker=now.strftime("%H:%M:%S.%f")[:-3],
        )

    def _presence_state(self, recipient: NotificationRecipient) -> str:
        if not recipient.presence_entity_id:
            return "unknown"
        state = self.hass.states.get(recipient.presence_entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return "unknown"
        return "home" if state.state == STATE_HOME else "away"

    @staticmethod
    def _channel_accepts_class(channel: NotificationChannel, event: SemanticNotificationEvent) -> bool:
        if channel.event_filter is ChannelEventFilter.ALL:
            return True
        if channel.event_filter is ChannelEventFilter.ATTENTION_ONLY:
            return event.notification_class in (NotificationClass.ATTENTION, NotificationClass.ERROR)
        if channel.event_filter is ChannelEventFilter.ERROR_ONLY:
            return event.notification_class is NotificationClass.ERROR
        return event.event_type.value in set(channel.custom_events)

    def _channel_suppression(
        self, recipient: NotificationRecipient, channel: NotificationChannel, event: SemanticNotificationEvent
    ) -> str | None:
        if not recipient.enabled:
            return "recipient_disabled"
        if not channel.enabled or channel.presence_policy is PresencePolicy.DISABLED:
            return "channel_disabled"
        if not self._channel_accepts_class(channel, event):
            return "event_filtered"
        presence = self._presence_state(recipient)
        if channel.presence_policy is PresencePolicy.HOME_ONLY and presence != "home":
            return "presence_unknown" if presence == "unknown" else "not_home"
        if channel.presence_policy is PresencePolicy.AWAY_ONLY and presence != "away":
            return "presence_unknown" if presence == "unknown" else "not_away"
        return None

    def _partial_start_wait_route_suppression(
        self,
        recipient: NotificationRecipient,
        channel: NotificationChannel,
        event: SemanticNotificationEvent,
    ) -> str | None:
        """Suppress a WAIT route already covered by a sent partial STARTED.

        Suppression is route/endpoint aware: if STARTED was filtered or failed
        for this physical endpoint, WAIT is still allowed through.  The durable
        STARTED event stores the exact WAIT episode id, so a later WAIT re-entry
        is never swallowed by an older start notification.
        """
        if event.event_type is not NotificationEventType.WAIT_ENTER or not event.job_id:
            return None
        started_event_id = f"{event.job_id}:{NotificationEventType.STARTED.value}"
        record = self.store.get_event(started_event_id)
        if record is None or record.coalesced_wait_event_id != event.event_id:
            return None
        for delivery_id in reversed(self.store.history):
            delivery = self.store.deliveries.get(delivery_id)
            if delivery is None or delivery.status != "sent":
                continue
            if delivery.event_id != started_event_id or delivery.job_id != event.job_id:
                continue
            if delivery.target_kind == channel.target_kind and delivery.target == channel.target:
                return "partial_start_coalesced"
        return None

    @staticmethod
    def _recipient_token(recipient_id: str) -> str:
        return hashlib.sha1(str(recipient_id).encode("utf-8")).hexdigest()[:8]

    def recipient_from_action_token(self, token: str | None) -> NotificationRecipient | None:
        if not token:
            return None
        value = str(token)
        return next(
            (item for item in self.settings.recipients if self._recipient_token(item.recipient_id) == value),
            None,
        )

    def _mobile_action_token(
        self, event: SemanticNotificationEvent, command: str, recipient_id: str | None = None
    ) -> str:
        if not event.job_id:
            return command
        if recipient_id:
            code = _TELEGRAM_COMMAND_CODES.get(command, command[:1])
            return f"VS63|{self.entry_id[:8]}|{event.job_id}|{self._recipient_token(recipient_id)}|{code}"
        # Compatibility fallback for direct renderer tests/old code paths.
        return f"VS060|{self.entry_id}|{event.job_id}|{command}"

    def parse_mobile_action(self, token: str) -> tuple[str, str, str | None] | None:
        value = str(token)
        prefix = f"VS63|{self.entry_id[:8]}|"
        if value.startswith(prefix):
            parts = value.split("|", 4)
            if len(parts) != 5:
                return None
            _tag, _entry_prefix, job_id, recipient_token, code = parts
            command = _TELEGRAM_CODE_COMMANDS.get(code)
            if not job_id or command is None:
                return None
            recipient = self.recipient_from_action_token(recipient_token)
            return job_id, command, recipient.recipient_id if recipient else None
        legacy_prefix = f"VS060|{self.entry_id}|"
        if value.startswith(legacy_prefix):
            parts = value.split("|", 3)
            if len(parts) != 4:
                return None
            _tag, _entry_id, job_id, command = parts
            return (job_id, command, None) if job_id and command else None
        return None

    def _telegram_action_token(
        self, event: SemanticNotificationEvent, command: str, recipient_id: str | None = None
    ) -> str:
        if not event.job_id:
            return command
        code = _TELEGRAM_COMMAND_CODES.get(command, command[:1])
        # Telegram callback_data is size-limited. A short recipient fingerprint
        # gives the audit trail a stable configured-recipient identity while
        # keeping the token comfortably under Telegram's callback-data limit.
        if recipient_id:
            return f"VS6|{self.entry_id[:8]}|{event.job_id}|{self._recipient_token(recipient_id)}|{code}"
        return f"VS6|{self.entry_id[:8]}|{event.job_id}|{code}"

    def parse_telegram_callback(self, token: str) -> tuple[str, str, str | None] | None:
        prefix = f"VS6|{self.entry_id[:8]}|"
        if not str(token).startswith(prefix):
            return None
        parts = str(token).split("|")
        if len(parts) == 4:
            _tag, _entry_prefix, job_id, code = parts
            recipient_id = None
        elif len(parts) == 5:
            _tag, _entry_prefix, job_id, recipient_token, code = parts
            recipient = self.recipient_from_action_token(recipient_token)
            recipient_id = recipient.recipient_id if recipient else None
        else:
            return None
        command = _TELEGRAM_CODE_COMMANDS.get(code)
        if not job_id or command is None:
            return None
        return job_id, command, recipient_id

    def _panel_path(
        self,
        *,
        view: str | None = None,
        delivery_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        params: dict[str, str] = {"config_entry": self.entry_id}
        if view:
            params["view"] = view
        if delivery_id:
            params["delivery"] = delivery_id
        if message_id:
            params["message"] = message_id
        if job_id:
            params["job"] = job_id
        return f"/vacuum-schedule?{urlencode(params)}"

    @staticmethod
    def _mobile_app_navigation_uri(path: str | None = None) -> str:
        """Build an explicit Home Assistant Companion App navigation URI."""
        relative = str(path or "/vacuum-schedule").strip() or "/vacuum-schedule"
        if not relative.startswith("/"):
            relative = f"/{relative}"
        return f"homeassistant://navigate{relative}"

    def _ha_open_url(self, path: str | None = None) -> str | None:
        relative = str(path or "/vacuum-schedule")
        if not relative.startswith("/"):
            relative = f"/{relative}"
        for configured in (
            getattr(self.hass.config, "external_url", ""),
            getattr(self.hass.config, "internal_url", ""),
        ):
            base = str(configured or "").strip().rstrip("/")
            if base.lower().startswith(("https://", "http://")):
                return f"{base}{relative}"
        return None

    @staticmethod
    def _is_test_event(event: SemanticNotificationEvent) -> bool:
        return str(event.event_id).startswith("test-event:")

    def _event_for_delivery(
        self,
        event: SemanticNotificationEvent,
        delivery_id: str | None,
        *,
        language: str | None = None,
    ) -> SemanticNotificationEvent:
        """Attach a concrete panel deep-link and safe demo buttons to one route."""
        language = normalize_language(language or self._language())
        # Every notification opens its immutable logical message record.  Job
        # controls are rendered inside the panel from the Job's *current* state
        # instead of being frozen into the push at delivery time.
        open_path = self._panel_path(view="notifications", message_id=event.event_id)

        event = self._localize_event_actions(event, language)
        actions = event.actions
        if event.job_id:
            actions = ()
        if self._is_test_event(event):
            notifications_path = self._panel_path(view="notifications")
            actions = (
                SemanticAction("TEST_OPEN_RECORD", "", uri=open_path),
                SemanticAction("TEST_DESTRUCTIVE_DEMO", "", uri=notifications_path, destructive=True),
            )
        return replace(event, open_path=open_path, actions=actions)

    def _render_for_channel(
        self,
        channel: NotificationChannel,
        event: SemanticNotificationEvent,
        *,
        language: str | None = None,
        recipient_id: str | None = None,
    ) -> RenderedNotification:
        language = normalize_language(language or self._language())
        if channel.transport_type is TransportType.TELEGRAM:
            telegram_actions = tuple(
                replace(action, uri=self._ha_open_url(action.uri) or action.uri)
                if action.uri else action
                for action in event.actions
            )
            telegram_event = replace(event, actions=telegram_actions)
            return render_telegram(
                telegram_event,
                language,
                callback_token=lambda command: self._telegram_action_token(event, command, recipient_id),
                open_url=self._ha_open_url(event.open_path),
                tag_prefix=f"vacuum_schedule:{self.entry_id[:8]}",
                include_actions=bool(channel.actionable),
            )
        if channel.transport_type is TransportType.MOBILE_APP:
            mobile_actions = tuple(
                replace(action, uri=self._mobile_app_navigation_uri(action.uri))
                if action.uri and str(action.uri).startswith("/") else action
                for action in event.actions
            )
            mobile_event = replace(event, actions=mobile_actions)
            return render_mobile_app(
                mobile_event,
                language,
                action_token=lambda command: self._mobile_action_token(event, command, recipient_id),
                open_path=event.open_path,
                ios_open_url=self._mobile_app_navigation_uri(event.open_path),
                group_prefix=f"vacuum_schedule:{self.entry_id[:8]}",
                include_actions=bool(channel.actionable),
            )
        if channel.transport_type is TransportType.PUSHOVER:
            return render_pushover(
                event,
                language,
                open_url=self._ha_open_url(event.open_path),
                options=channel.options,
            )
        return render_generic_notify(event, language)

    @staticmethod
    def _telegram_keyboard(rendered: RenderedNotification) -> list[str]:
        """Convert renderer buttons to Home Assistant Telegram Bot row syntax."""
        return [
            ", ".join(f"{title}:{data}" for title, data in row)
            for row in rendered.inline_keyboard
        ]

    @staticmethod
    def _telegram_response_metadata(response: Any, target: str) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            return {}
        chats = response.get("chats")
        if not isinstance(chats, (list, tuple)):
            return {}
        selected: Mapping[str, Any] | None = None
        for raw in chats:
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("entity_id") or "") == target:
                selected = raw
                break
            if selected is None:
                selected = raw
        if selected is None:
            return {}
        message_id = selected.get("message_id")
        try:
            message_id = int(message_id) if message_id is not None else None
        except (TypeError, ValueError):
            message_id = None
        return {
            "provider_chat_id": str(selected.get("chat_id")) if selected.get("chat_id") is not None else None,
            "provider_message_id": message_id,
            "provider_entity_id": str(selected.get("entity_id") or target),
        }

    async def _async_send_telegram(
        self,
        channel: NotificationChannel,
        event: SemanticNotificationEvent,
        rendered: RenderedNotification,
        previous: NotificationDelivery | None,
        *,
        language: str | None = None,
    ) -> dict[str, Any]:
        services = self.hass.services
        if channel.target_kind != "entity" or not services.has_service("telegram_bot", "send_message"):
            # Legacy/non-entity Telegram notify targets remain usable through the
            # generic HA notify API, but cannot safely provide callbacks/updates.
            fallback = render_compact(event, language or self._language()) if event.compact_v2 else render_plain(event, language or self._language())
            if channel.target_kind == "entity":
                await services.async_call(
                    "notify",
                    "send_message",
                    {"message": fallback.message, "title": fallback.title},
                    blocking=True,
                    target={"entity_id": channel.target},
                )
            else:
                await self._async_send_legacy_notify(channel, fallback)
            return {"delivery_operation": "sent"}

        keyboard = self._telegram_keyboard(rendered)
        if (
            event.event_type in {NotificationEventType.WAIT_ENTER, NotificationEventType.WAIT_REMINDER, NotificationEventType.START_FORECAST}
            and previous is not None
            and previous.provider_message_id is not None
            and services.has_service("telegram_bot", "edit_message")
        ):
            # WAIT entry/reminders must be new Telegram pushes, not silent edits.
            # Remove stale actions from the previous lifecycle message, then send a new one.
            try:
                await services.async_call(
                    "telegram_bot",
                    "edit_message",
                    {
                        "entity_id": [channel.target],
                        "message_id": previous.provider_message_id,
                        "message": previous.message,
                        "parse_mode": rendered.parse_mode or "html",
                        "inline_keyboard": [],
                    },
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.debug("Could not retire previous Telegram WAIT actions for %s: %s", channel.target, err)
            previous = None
        if (
            previous is not None
            and previous.provider_message_id is not None
            and services.has_service("telegram_bot", "edit_message")
        ):
            edit_data: dict[str, Any] = {
                "entity_id": [channel.target],
                "message_id": previous.provider_message_id,
                "message": rendered.message,
                "parse_mode": rendered.parse_mode or "html",
                "inline_keyboard": keyboard,
            }
            try:
                await services.async_call("telegram_bot", "edit_message", edit_data, blocking=True)
                return {
                    "delivery_operation": "updated",
                    "provider_chat_id": previous.provider_chat_id,
                    "provider_message_id": previous.provider_message_id,
                    "provider_entity_id": previous.provider_entity_id or channel.target,
                    "provider_message_tag": rendered.message_tag,
                }
            except Exception as err:  # stale/deleted Telegram message => create a new one.
                _LOGGER.debug("Telegram edit failed for %s; sending a new message: %s", channel.target, err)

        send_data: dict[str, Any] = {
            "entity_id": [channel.target],
            "message": rendered.message,
            "parse_mode": rendered.parse_mode or "html",
            "inline_keyboard": keyboard,
            "disable_web_page_preview": True,
        }
        if rendered.message_tag:
            send_data["message_tag"] = rendered.message_tag
        response = await services.async_call(
            "telegram_bot",
            "send_message",
            send_data,
            blocking=True,
            return_response=True,
        )
        metadata = self._telegram_response_metadata(response, channel.target)
        metadata.update({"delivery_operation": "sent", "provider_message_tag": rendered.message_tag})
        return metadata

    async def _async_refresh_existing_telegram(
        self,
        channel: NotificationChannel,
        event: SemanticNotificationEvent,
        previous: NotificationDelivery,
        *,
        recipient_id: str | None = None,
        language: str | None = None,
    ) -> bool:
        """Refresh a previously delivered Telegram job message without a new push.

        This path is intentionally independent from notification policy. For
        example, Balanced policy may suppress a normal STARTED notification, but
        an earlier pre-warning message must still lose its stale Start/Skip
        buttons and reflect the running state.
        """
        if (
            channel.target_kind != "entity"
            or previous.provider_message_id is None
            or not self.hass.services.has_service("telegram_bot", "edit_message")
        ):
            return False
        resolved_language = language or self._language()
        event = self._localize_event_actions(event, resolved_language)
        rendered = self._render_for_channel(
            channel, event, language=resolved_language, recipient_id=recipient_id
        )
        data: dict[str, Any] = {
            "entity_id": [channel.target],
            "message_id": previous.provider_message_id,
            "message": rendered.message,
            "parse_mode": rendered.parse_mode or "html",
            "inline_keyboard": self._telegram_keyboard(rendered),
        }
        try:
            await self.hass.services.async_call("telegram_bot", "edit_message", data, blocking=True)
        except Exception as err:
            _LOGGER.debug("Could not silently refresh Telegram job message %s: %s", channel.target, err)
            return False
        return True

    async def _async_sync_mutable_state(
        self, event: SemanticNotificationEvent, *, recipient_ids: set[str] | None = None
    ) -> None:
        """Refresh an existing Telegram Job message when policy suppresses a push.

        This path handles the case where policy suppresses a state event but an
        existing Telegram lifecycle message still needs its stale actions retired.
        Provider-side mutable lifecycle state is intentionally a Telegram-only
        concern. Mobile App notifications are event notifications: they never use
        tags, replacement, or clear_notification, so there is nothing to sync or
        retire when a later event is suppressed by policy.
        """
        if not event.job_id:
            return
        for recipient in self.settings.recipients:
            if recipient_ids is not None and recipient.recipient_id not in recipient_ids:
                continue
            for configured_channel in recipient.channels:
                channel = self._resolved_channel_transport(configured_channel)
                if channel.transport_type is not TransportType.TELEGRAM:
                    continue
                sync_key = f"{event.event_id}:{recipient.recipient_id}:{channel.channel_id}"
                if sync_key in self._transport_state_sync:
                    continue
                previous = self.store.latest_for_job_channel(
                    event.job_id,
                    recipient.recipient_id,
                    channel.channel_id,
                    transport_type=channel.transport_type.value,
                )
                if previous is None:
                    continue
                if await self._async_refresh_existing_telegram(
                    channel,
                    event,
                    previous,
                    recipient_id=recipient.recipient_id,
                    language=self._recipient_language(recipient),
                ):
                    self._transport_state_sync.add(sync_key)

    async def _async_send_legacy_notify(
        self, channel: NotificationChannel, rendered: RenderedNotification
    ) -> None:
        """Send one rendered payload through a legacy ``notify.<service>`` action."""
        data: dict[str, Any] = {"message": rendered.message, "title": rendered.title}
        if rendered.targets:
            data["target"] = list(rendered.targets)
        if rendered.data:
            data["data"] = dict(rendered.data)
        target = channel.target
        if target.startswith("notify."):
            target = target.split(".", 1)[1]
        await self.hass.services.async_call("notify", target, data, blocking=True)

    async def _async_send_channel(
        self,
        channel: NotificationChannel,
        event: SemanticNotificationEvent,
        rendered: RenderedNotification,
        *,
        previous: NotificationDelivery | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        if channel.transport_type is TransportType.TELEGRAM:
            return await self._async_send_telegram(
                channel, event, rendered, previous, language=language
            )

        if channel.target_kind == "entity":
            # notify.send_message intentionally has a provider-neutral contract
            # (message + optional title). Provider-specific Companion/Pushover
            # payloads are therefore not leaked into a generic notify entity.
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {"message": rendered.message, "title": rendered.title},
                blocking=True,
                target={"entity_id": channel.target},
            )
            return {
                "delivery_operation": "sent_basic"
                if channel.transport_type is not TransportType.GENERIC_NOTIFY
                else "sent"
            }

        # Legacy notify.mobile_app_* actions receive one independent push per
        # semantic event. Mobile App tag/replacement/clear semantics are
        # deliberately not used; Vacuum Schedule's delivery_id is the sole
        # duplicate-prevention boundary.
        await self._async_send_legacy_notify(channel, rendered)
        return {"delivery_operation": "sent"}

    async def async_deliver_event(
        self,
        event: SemanticNotificationEvent,
        *,
        recipient_ids: set[str] | None = None,
        language: str | None = None,
        ignore_master_enabled: bool = False,
    ) -> dict[str, Any]:
        """Deliver one semantic event through an atomic durable outbox claim.

        Concurrent Scheduler wake-ups can observe the same Job transition (for
        example two zone completion timers firing together).  A delivery is
        therefore persisted as ``pending`` *before* awaiting the HA transport.
        A second coroutine sees that claim and cannot send the same semantic
        event again.  The same claim boundary also coalesces multiple logical
        routes that resolve to one physical notify endpoint.

        Exactly-once delivery cannot be proven across an external provider: a
        process may die after the provider accepted a push but before the final
        ``sent`` status is persisted.  Vacuum Schedule deliberately prefers
        at-most-once behavior here: a persisted pending claim is not blindly
        retried after restart.
        """
        settings = self.settings
        summary: dict[str, Any] = {
            "settings_enabled": settings.enabled,
            "master_bypassed": bool(ignore_master_enabled and not settings.enabled),
            "sent": 0,
            "suppressed": 0,
            "failed": 0,
            "duplicates": 0,
            "deliveries": [],
        }
        if not settings.enabled and not ignore_master_enabled:
            summary["master_suppressed"] = True
            return summary

        # Persist one route-independent message snapshot before any provider
        # awaits. Multiple recipients/channels remain deliveries of this one
        # immutable semantic event rather than duplicate archive messages.
        put_event = getattr(self.store, "put_event", None)
        if callable(put_event):
            put_event(event)
        await self.store.async_save()

        for recipient in settings.recipients:
            if recipient_ids is not None and recipient.recipient_id not in recipient_ids:
                continue
            route_language = self._recipient_language(recipient, language)
            for configured_channel in recipient.channels:
                channel = self._resolved_channel_transport(configured_channel)
                delivery_id = f"{event.event_id}:{recipient.recipient_id}:{channel.channel_id}"
                routed_event = self._event_for_delivery(event, delivery_id, language=route_language)
                rendered = self._render_for_channel(
                    channel, routed_event, language=route_language,
                    recipient_id=recipient.recipient_id,
                )
                item = NotificationDelivery(
                    delivery_id=delivery_id,
                    event_id=event.event_id,
                    job_id=event.job_id,
                    event_type=event.event_type.value,
                    notification_class=event.notification_class.value,
                    recipient_id=recipient.recipient_id,
                    recipient_name=recipient.name,
                    channel_id=channel.channel_id,
                    channel_name=channel.name,
                    transport_type=channel.transport_type.value,
                    target=channel.target,
                    target_kind=channel.target_kind,
                    created_at=event.created_at.isoformat(),
                    title=rendered.title,
                    message=rendered.message,
                    semantic_type=event.semantic_type,
                    execution_mode=(ExecutionMode.DRY_RUN.value if event.dry_run else ExecutionMode.REAL.value),
                )
                suppression = self._channel_suppression(recipient, channel, event)
                suppression = suppression or self._partial_start_wait_route_suppression(recipient, channel, event)
                suppression = suppression or self._forecast_route_suppression(recipient, channel, event)
                if event.dry_run and settings.dry_run_delivery == "log_only" and not self._is_test_event(event):
                    suppression = suppression or "dry_run_delivery_disabled"
                duplicate_endpoint = False

                # Claim/suppression insertion is the atomic boundary.  Do not
                # hold this lock while calling Home Assistant/provider services.
                async with self._delivery_claim_lock:
                    if self.store.get(delivery_id) is not None:
                        summary["duplicates"] += 1
                        continue
                    if suppression:
                        item.status = "suppressed"
                        item.suppression_reason = suppression
                        self.store.put(item)
                        await self.store.async_save()
                    else:
                        endpoint_claim = self.store.endpoint_claim(
                            event.event_id, channel.target_kind, channel.target
                        )
                        if endpoint_claim is not None:
                            duplicate_endpoint = True
                            item.status = "suppressed"
                            item.suppression_reason = "duplicate_endpoint_route"
                            self.store.put(item)
                            await self.store.async_save()
                        else:
                            item.status = "pending"
                            item.claimed_at = datetime.now().isoformat()
                            self.store.put(item)
                            # Persist before any external await.  This is the
                            # durable outbox claim that closes the zone-timer race.
                            await self.store.async_save()

                if suppression or duplicate_endpoint:
                    reason = suppression or "duplicate_endpoint_route"
                    summary["suppressed"] += 1
                    summary["deliveries"].append({
                        "recipient_id": recipient.recipient_id,
                        "recipient_name": recipient.name,
                        "channel_id": channel.channel_id,
                        "channel_name": channel.name,
                        "target": channel.target,
                        "target_kind": channel.target_kind,
                        "status": item.status,
                        "reason": reason,
                        "delivery_id": delivery_id,
                        "open_path": routed_event.open_path,
                    })
                    continue

                previous = None
                if event.job_id and channel.transport_type is TransportType.TELEGRAM:
                    previous = self.store.latest_for_job_channel(
                        event.job_id,
                        recipient.recipient_id,
                        channel.channel_id,
                        transport_type=channel.transport_type.value,
                    )

                item.attempts += 1
                try:
                    metadata = await self._async_send_channel(
                        channel, routed_event, rendered, previous=previous,
                        language=route_language,
                    )
                    item.status = "sent"
                    item.sent_at = datetime.now().isoformat()
                    item.error = None
                    item.delivery_operation = str(metadata.get("delivery_operation") or "sent")
                    item.provider_chat_id = metadata.get("provider_chat_id")
                    item.provider_message_id = metadata.get("provider_message_id")
                    item.provider_entity_id = metadata.get("provider_entity_id")
                    item.provider_message_tag = metadata.get("provider_message_tag")
                    summary["sent"] += 1
                except Exception as err:  # HA notification errors belong in delivery history.
                    # Do not auto-retry an ambiguous provider failure: if the
                    # provider accepted the first request but the response was
                    # lost, a second send would itself create a duplicate push.
                    item.status = "failed"
                    item.error = str(err)
                    summary["failed"] += 1

                async with self._delivery_claim_lock:
                    self.store.put(item)
                    await self.store.async_save()

                summary["deliveries"].append({
                    "recipient_id": recipient.recipient_id,
                    "recipient_name": recipient.name,
                    "channel_id": channel.channel_id,
                    "channel_name": channel.name,
                    "target": channel.target,
                    "target_kind": channel.target_kind,
                    "status": item.status,
                    "reason": item.error,
                    "operation": item.delivery_operation,
                    "delivery_id": delivery_id,
                    "open_path": routed_event.open_path,
                })
        return summary

    async def async_process_job(self, job: JobInstance, now: datetime) -> None:
        # Historical occurrences reconstructed after an offline interval belong
        # in durable history/statistics but must not create a burst of stale push
        # notifications when Home Assistant comes back online.
        if job.metadata.get("suppress_notifications"):
            return
        settings = self.settings
        if not settings.enabled or not settings.recipients:
            return

        # One Job can be observed by several scheduler wake-ups at virtually the
        # same instant (zone transition, robot observation, timer).  Exact-event
        # outbox claims already prevent duplicates of one event_id, but they do
        # not by themselves coalesce distinct event families with equivalent
        # meaning.  Run the complete lifecycle decision in order for each Job so
        # the later forecast-closure logic can see the already-sent STARTED event.
        process_lock = self._job_process_locks.setdefault(job.job_id, asyncio.Lock())
        async with process_lock:
            await self._async_process_job_locked(job, now)

    async def _async_process_job_locked(self, job: JobInstance, now: datetime) -> None:
        """Process one Job while holding its lifecycle notification lock."""
        policy, recipient_ids = self.policy_for_job(job)
        if policy.start_forecast_mode is StartForecastMode.OFF:
            await self._async_process_start_forecast(job, now, policy, recipient_ids)
        partial_start_wait = self._partial_start_wait_event_id(job) is not None
        lifecycle_order = (
            (
                NotificationEventType.PREWARNING,
                NotificationEventType.STARTED,
                NotificationEventType.WAIT_ENTER,
                NotificationEventType.FINISHED,
            )
            if partial_start_wait
            else (
                NotificationEventType.PREWARNING,
                NotificationEventType.WAIT_ENTER,
                NotificationEventType.STARTED,
                NotificationEventType.FINISHED,
            )
        )
        for event_type in lifecycle_order:
            event = self._build_event(job, event_type, now)
            if self._policy_event_due(job, event_type, policy, now):
                await self.async_deliver_event(event, recipient_ids=recipient_ids)
                continue
            # Keep existing mutable lifecycle cards current without a new push.
            state_reached = (
                event_type is NotificationEventType.STARTED and self._first_actual_start(job) is not None
            ) or (
                event_type is NotificationEventType.FINISHED and job.terminal and job.result is not None
            )
            if state_reached:
                await self._async_sync_mutable_state(event, recipient_ids=recipient_ids)

        wait_context = self._wait_context(job)
        if (
            wait_context is not None
            and policy.wait_reminder_enabled
            and policy.wait_reminder_max_count > 0
        ):
            _wait_signature, wait_since = wait_context
            base = wait_since
            if policy.wait_enter_mode is WaitEnterMode.DELAYED:
                base = instant_add(base, timedelta(seconds=policy.wait_delay_seconds))
            for index in range(1, policy.wait_reminder_max_count + 1):
                due = instant_add(base, timedelta(seconds=policy.wait_reminder_interval_seconds * index))
                if instant_lt(now, due):
                    break
                event = self._build_event(
                    job,
                    NotificationEventType.WAIT_REMINDER,
                    now,
                    reminder_index=index,
                )
                await self.async_deliver_event(event, recipient_ids=recipient_ids)

        await self._async_process_start_forecast(job, now, policy, recipient_ids)

    async def _async_process_start_forecast(
        self, job: JobInstance, now: datetime, policy: NotificationPolicy, recipient_ids: set[str] | None
    ) -> None:
        if not hasattr(self.store, "start_forecasts"):
            self.store.start_forecasts = {}
        if policy.start_forecast_mode is StartForecastMode.OFF or job.terminal:
            if self.store.start_forecasts.pop(job.job_id, None) is not None:
                await self.store.async_save()
            return
        state = self.store.start_forecasts.setdefault(job.job_id, {})
        before = repr(state)
        if instant_ge(now, instant_add(job.deadline_at, -timedelta(minutes=policy.start_forecast_deadline_minutes))):
            state["closing_observed"] = True
        if has_started(job) and not job.terminal:
            state.pop("due_at", None)
            if not state.get("start_closed"):
                state["start_closed"] = True
                # Delivery routing requires an unresolved warning on this exact
                # recipient/channel. This does not enable routine start pushes.
                base = self._build_event(job, NotificationEventType.STARTED, now)
                event = replace(base, event_id=f"{job.job_id}:start_forecast:started",
                    event_type=NotificationEventType.START_FORECAST,
                    semantic_type="vacuum.job.start_forecast", forecast_kind="started",
                    notification_class=NotificationClass.ATTENTION, blockers=())
                await self.async_deliver_event(event, recipient_ids=recipient_ids)
        else:
            candidate = forecast_candidate(job, now, policy, state)
            if candidate:
                kind = candidate["kind"]
                base = self._build_event(job, NotificationEventType.START_FORECAST, now)
                event = replace(base,
                    event_id=f"{job.job_id}:start_forecast:{candidate['sequence']}",
                    forecast_kind=kind, blockers=tuple(candidate["blockers"]),
                    notification_class=NotificationClass.ATTENTION,
                    severity="info" if kind == "ready" else "warning")
                # The durable sequence is reserved before external delivery.
                await self.store.async_save()
                await self.async_deliver_event(event, recipient_ids=recipient_ids)
        if before != repr(state):
            await self.store.async_save()

    def _forecast_route_suppression(
        self, recipient: NotificationRecipient, channel: NotificationChannel, event: SemanticNotificationEvent
    ) -> str | None:
        if not event.job_id or self._is_test_event(event):
            return None
        forecasts = getattr(self.store, "start_forecasts", {})
        if event.event_type is not NotificationEventType.START_FORECAST and event.job_id not in forecasts:
            return None
        latest = self.store.latest_for_job_channel(event.job_id, recipient.recipient_id, channel.channel_id)
        record = self.store.get_event(latest.event_id) if latest else None
        if event.forecast_kind in {"ready", "started", "improved"}:
            # Never announce recovery to somebody who did not see the problem,
            # or follow the actual STARTED message with a redundant recovery.
            if (not record or (not record.blockers and record.forecast_kind not in {"unknown", "risk"})
                    or record.event_type in {"started", "finished"}):
                return "forecast_no_prior_warning"
        if not latest or not record:
            return None
        if event.forecast_kind == "closing" and record.forecast_kind == "closing":
            return "forecast_coalesced"
        elapsed = instant_delta(event.created_at, datetime.fromisoformat(latest.created_at)).total_seconds()
        if elapsed < 0:
            return None
        lifecycle = {"prewarning", "wait_enter", "wait_reminder"}
        if event.event_type.value not in lifecycle | {"start_forecast"}:
            return None
        if record.event_type not in lifecycle | {"start_forecast"}:
            return None
        # Merge simultaneous notification categories independently per route.
        # A filtered or failed lifecycle delivery cannot swallow a forecast.
        if ((event.event_type is NotificationEventType.START_FORECAST or elapsed < 120)
                and event.forecast_kind != "closing"
                and canonical_codes(record.blockers) == canonical_codes(event.blockers)
                and (event.event_type is NotificationEventType.START_FORECAST
                     or record.event_type == "start_forecast"
                     or (record.event_type == "prewarning" and event.event_type is NotificationEventType.WAIT_ENTER))):
            return "forecast_coalesced"
        return None

    async def async_test_event(
        self,
        event_type: str,
        notification_class: str = "info",
        *,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Send one synthetic routing event without changing scheduler jobs."""
        kind = NotificationEventType(str(event_type))
        if kind is NotificationEventType.TEST:
            cls = NotificationClass(str(notification_class))
        else:
            default_classes = {
                NotificationEventType.PREWARNING: NotificationClass.ATTENTION,
                NotificationEventType.WAIT_ENTER: NotificationClass.ATTENTION,
                NotificationEventType.WAIT_REMINDER: NotificationClass.ATTENTION,
                NotificationEventType.STARTED: NotificationClass.INFO,
                NotificationEventType.FINISHED: NotificationClass.ERROR,
            }
            cls = default_classes.get(kind, NotificationClass.INFO)
        event = self._test_event(kind, cls, language=language)
        summary = await self.async_deliver_event(
            event,
            language=language,
            ignore_master_enabled=True,
        )
        summary["deliveries_created"] = (
            int(summary.get("sent", 0))
            + int(summary.get("suppressed", 0))
            + int(summary.get("failed", 0))
        )
        summary["ok"] = int(summary.get("sent", 0)) > 0 and int(summary.get("failed", 0)) == 0
        return summary

    @staticmethod
    def _transport_from_platform(platform: str, target: str = "") -> TransportType:
        value = str(platform or "").lower()
        lower_target = str(target or "").lower()
        if value == "mobile_app" or "mobile_app" in lower_target:
            return TransportType.MOBILE_APP
        if value == "telegram_bot" or "telegram" in lower_target:
            return TransportType.TELEGRAM
        if value == "pushover" or "pushover" in lower_target:
            return TransportType.PUSHOVER
        return TransportType.GENERIC_NOTIFY

    def _telegram_mode_for_registry_entry(self, reg_entry: Any) -> str | None:
        """Return Telegram Bot mode (broadcast/polling/webhooks) for a notify entity."""
        config_entry_id = getattr(reg_entry, "config_entry_id", None)
        if not config_entry_id:
            return None
        try:
            config_entry = self.hass.config_entries.async_get_entry(config_entry_id)
        except Exception:  # pragma: no cover - config entries unavailable during teardown
            return None
        if config_entry is None:
            return None
        mode = str(getattr(config_entry, "data", {}).get("platform", "") or "").lower()
        return mode if mode in {"broadcast", "polling", "webhooks"} else None

    def _telegram_actions_supported(self, reg_entry: Any) -> bool:
        """Telegram Broadcast is sending-only; Polling/Webhooks can receive callbacks."""
        mode = self._telegram_mode_for_registry_entry(reg_entry)
        return mode in {"polling", "webhooks"}

    def _resolved_channel_transport(self, channel: NotificationChannel) -> NotificationChannel:
        """Resolve a saved HA-owned binding against the current Entity Registry platform.

        Older Vacuum Schedule versions could persist an entity binding as generic
        notify.  Runtime detection deliberately upgrades that binding without
        rewriting the user's stored recipient settings.
        """
        detected = TransportType.GENERIC_NOTIFY
        registry_entry = None
        provider_neutral_entity = False
        if channel.target_kind == "entity" and channel.target:
            try:
                registry = er.async_get(self.hass)
                registry_entry = registry.async_get(channel.target)
            except Exception:  # pragma: no cover - registry may be unavailable during shutdown
                registry_entry = None
            detected = self._transport_from_platform(
                str(getattr(registry_entry, "platform", "") or ""),
                channel.target,
            )
            if detected in {TransportType.MOBILE_APP, TransportType.PUSHOVER}:
                # HA notify entities expose only notify.send_message. Treat them
                # honestly as generic transport bindings instead of advertising
                # Companion/Pushover rich features that cannot be delivered via
                # the entity contract. Provider-rich behavior is service-based.
                detected = TransportType.GENERIC_NOTIFY
                provider_neutral_entity = True
        elif channel.target:
            detected = self._transport_from_platform("", channel.target)

        # Never downgrade an explicit specialized binding merely because HA could
        # not identify the provider at this instant.
        if detected is TransportType.GENERIC_NOTIFY:
            if provider_neutral_entity and (
                channel.transport_type is not TransportType.GENERIC_NOTIFY or channel.actionable
            ):
                return replace(
                    channel,
                    transport_type=TransportType.GENERIC_NOTIFY,
                    actionable=False,
                )
            return channel

        actionable = channel.actionable
        if detected is TransportType.TELEGRAM and registry_entry is not None:
            # Broadcast can send formatted Telegram messages but cannot receive
            # callback queries, so do not render dead action buttons for it.
            actionable = self._telegram_actions_supported(registry_entry)
        elif detected is TransportType.MOBILE_APP:
            # Companion provider metadata/actions are available through the
            # legacy notify.mobile_app_* action. Generic notify entities expose
            # only the provider-neutral notify.send_message contract.
            actionable = channel.target_kind == "service"
        elif detected is TransportType.PUSHOVER:
            actionable = False

        if detected is channel.transport_type and actionable == channel.actionable:
            return channel
        return replace(channel, transport_type=detected, actionable=actionable)

    @staticmethod
    def _normalized_candidate_token(value: str) -> str:
        """Normalize a technical service/object id only for exact owner matching."""
        return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())

    def _service_owner_name(self, service_name: str, transport: TransportType) -> str:
        """Return a real HA config-entry title for a legacy notify service when known.

        We deliberately do not manufacture a user-facing name from the service id.
        If Home Assistant does not expose an owning title we leave the friendly name
        empty and the frontend shows the provider plus the technical target.
        """
        domain = {
            TransportType.MOBILE_APP: "mobile_app",
            TransportType.PUSHOVER: "pushover",
            TransportType.TELEGRAM: "telegram_bot",
        }.get(transport)
        if not domain:
            return ""
        try:
            entries = list(self.hass.config_entries.async_entries(domain))
        except Exception:
            return ""
        if not entries:
            return ""
        technical = str(service_name or "")
        object_part = technical[len("mobile_app_") :] if technical.startswith("mobile_app_") else technical
        token = self._normalized_candidate_token(object_part)
        for entry in entries:
            raw_names = [
                getattr(entry, "title", ""),
                getattr(entry, "data", {}).get("device_name", "") if isinstance(getattr(entry, "data", {}), Mapping) else "",
            ]
            if token and any(self._normalized_candidate_token(name) == token for name in raw_names if name):
                return str(getattr(entry, "title", "") or raw_names[1] or "").strip()
        if len(entries) == 1:
            return str(getattr(entries[0], "title", "") or "").strip()
        return ""

    def _entity_candidate_name(self, state: Any, reg_entry: Any, device_registry: Any) -> tuple[str, str]:
        """Resolve a real HA display name and its source without prettifying entity_id."""
        if reg_entry is not None:
            value = str(getattr(reg_entry, "name", "") or "").strip()
            if value:
                return value, "entity_registry"
        attrs = getattr(state, "attributes", {}) or {}
        if isinstance(attrs, Mapping):
            value = str(attrs.get("friendly_name") or "").strip()
            if value:
                return value, "friendly_name"
        if reg_entry is not None and getattr(reg_entry, "device_id", None) and device_registry is not None:
            try:
                device = device_registry.async_get(reg_entry.device_id)
            except Exception:
                device = None
            if device is not None:
                value = str(getattr(device, "name_by_user", "") or getattr(device, "name", "") or "").strip()
                if value:
                    return value, "device_registry"
        if reg_entry is not None:
            config_entry_id = getattr(reg_entry, "config_entry_id", None)
            if config_entry_id:
                try:
                    config_entry = self.hass.config_entries.async_get_entry(config_entry_id)
                except Exception:
                    config_entry = None
                value = str(getattr(config_entry, "title", "") or "").strip() if config_entry else ""
                if value:
                    return value, "config_entry"
            value = str(getattr(reg_entry, "original_name", "") or "").strip()
            if value:
                return value, "original_name"
        return "", "none"

    def candidates_payload(self) -> dict[str, Any]:
        """Auto-discover HA-owned notify channels; Scheduler stores bindings only."""
        services = self.hass.services.async_services().get("notify", {})
        channels: list[dict[str, Any]] = []
        for service_name in sorted(services):
            if service_name in {"send_message", "persistent_notification", "notify"}:
                continue
            transport = self._transport_from_platform("", service_name)
            owner_name = self._service_owner_name(service_name, transport)
            channels.append({
                "target_kind": "service",
                "target": f"notify.{service_name}",
                "name": owner_name,
                "name_source": "config_entry" if owner_name else "none",
                "transport_type": transport.value,
                "platform": "legacy_notify_service",
                "supports_actionable": transport is TransportType.MOBILE_APP,
                "supports_updates": False,
                "supports_open": transport in {TransportType.MOBILE_APP, TransportType.PUSHOVER},
            })

        try:
            registry = er.async_get(self.hass)
        except Exception:  # pragma: no cover - registry unavailable only during HA teardown/tests
            registry = None
        try:
            device_registry = dr.async_get(self.hass)
        except Exception:  # pragma: no cover - registry unavailable only during HA teardown/tests
            device_registry = None
        for state in self.hass.states.async_all():
            if not state.entity_id.startswith("notify."):
                continue
            reg_entry = registry.async_get(state.entity_id) if registry is not None else None
            platform = str(getattr(reg_entry, "platform", "") or "")
            transport = self._transport_from_platform(platform, state.entity_id)
            if transport in {TransportType.MOBILE_APP, TransportType.PUSHOVER}:
                transport = TransportType.GENERIC_NOTIFY
            telegram_mode = self._telegram_mode_for_registry_entry(reg_entry) if transport is TransportType.TELEGRAM else None
            telegram_actionable = (
                self._telegram_actions_supported(reg_entry)
                if transport is TransportType.TELEGRAM and reg_entry is not None
                else False
            )
            display_name, name_source = self._entity_candidate_name(state, reg_entry, device_registry)
            channels.append({
                "target_kind": "entity",
                "target": state.entity_id,
                "name": display_name,
                "name_source": name_source,
                "transport_type": transport.value,
                "platform": platform or "notify_entity",
                "telegram_mode": telegram_mode,
                "supports_actionable": telegram_actionable if transport is TransportType.TELEGRAM else False,
                "supports_updates": transport is TransportType.TELEGRAM,
                "supports_open": transport is TransportType.TELEGRAM,
            })

        unique: dict[tuple[str, str], dict[str, Any]] = {
            (item["target_kind"], item["target"]): item for item in channels
        }
        presence = [
            {"entity_id": state.entity_id, "name": state.name, "state": state.state}
            for state in self.hass.states.async_all()
            if state.entity_id.startswith(("person.", "device_tracker."))
        ]
        return {
            "channels": sorted(unique.values(), key=lambda item: ((item.get("name") or item["target"]).casefold(), item["target"])),
            "presence_entities": sorted(presence, key=lambda item: (item["name"].casefold(), item["entity_id"])),
        }

    def _message_record_payload(
        self, record: Any, *, language: str, include_deliveries: bool = False
    ) -> dict[str, Any]:
        """Return one logical message plus the exact compact outbound copy.

        The archive table must never invent a separate summary. Its title/body
        come from the same compact formatter used by Mobile App, Pushover,
        Telegram and generic notify. Detailed semantic fields remain available
        separately for the message page.
        """
        raw = record.to_dict()
        event = record.to_event()
        rendered = render_compact(event, language)
        # Migrated pre-0.8.12 records do not have the full semantic snapshot.
        # Preserve the historical text that was actually sent in that case.
        if record.legacy_title and not record.schedule_name and not record.zones:
            rendered_title = record.legacy_title
            rendered_message = record.legacy_message
        else:
            rendered_title = rendered.title
            rendered_message = rendered.message
        deliveries = self.store.deliveries_for_event(record.event_id)
        counts = {"sent": 0, "suppressed": 0, "failed": 0, "pending": 0}
        for item in deliveries:
            status = str(item.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
        delivery_routes = [
            {
                "delivery_id": str(item.get("delivery_id") or ""),
                "recipient_id": str(item.get("recipient_id") or ""),
                "recipient_name": str(item.get("recipient_name") or ""),
                "channel_id": str(item.get("channel_id") or ""),
                "channel_name": str(item.get("channel_name") or ""),
                "transport_type": str(item.get("transport_type") or ""),
                "target": str(item.get("target") or ""),
                "target_kind": str(item.get("target_kind") or "service"),
                "status": str(item.get("status") or "pending"),
                "delivery_operation": str(item.get("delivery_operation") or "sent"),
                "suppression_reason": item.get("suppression_reason"),
                "error": item.get("error"),
            }
            for item in deliveries
        ]
        raw.update({
            "title": rendered_title,
            "message": rendered_message,
            "execution_mode": ExecutionMode.DRY_RUN.value if record.dry_run else ExecutionMode.REAL.value,
            "delivery_summary": {**counts, "total": len(deliveries)},
            # Message-centric notification history needs enough immutable route
            # metadata to answer "who received this, where and how" without
            # expanding one logical message into multiple delivery rows.
            "delivery_routes": delivery_routes,
        })
        if include_deliveries:
            raw["deliveries"] = deliveries
        raw.pop("legacy_title", None)
        raw.pop("legacy_message", None)
        return raw

    def message_payload(self, event_id: str, *, language: str | None = None) -> dict[str, Any] | None:
        """Return one logical archive message rendered for the panel locale."""
        record = self.store.get_event(str(event_id))
        if record is None:
            return None
        resolved = normalize_language(language or self._language())
        return self._message_record_payload(record, language=resolved, include_deliveries=True)

    def payload(
        self, delivery_id: str | None = None, event_id: str | None = None, *, language: str | None = None
    ) -> dict[str, Any]:
        settings = self.settings
        data = settings.to_dict()
        preview = self._test_event()
        language = normalize_language(language or self._language())
        for recipient in data["recipients"]:
            presence_id = recipient.get("presence_entity_id")
            state = self.hass.states.get(presence_id) if presence_id else None
            recipient["presence_state"] = state.state if state is not None else None
            recipient_obj = NotificationRecipient.from_dict(recipient)
            for channel in recipient.get("channels", []):
                configured_channel = NotificationChannel.from_dict(channel)
                effective_channel = self._resolved_channel_transport(configured_channel)
                channel["detected_transport_type"] = effective_channel.transport_type.value
                channel["currently_routed"] = self._channel_suppression(
                    recipient_obj,
                    effective_channel,
                    preview,
                ) is None

        selected_event_id = str(event_id or "") or None
        if not selected_event_id and delivery_id:
            selected_event_id = self.store.event_id_for_delivery(delivery_id)

        records = self.store.recent_events(50)
        if selected_event_id:
            selected = self.store.get_event(selected_event_id)
            if selected is not None and all(item.event_id != selected_event_id for item in records):
                records.append(selected)
        messages = [
            self._message_record_payload(
                record, language=language, include_deliveries=bool(selected_event_id and record.event_id == selected_event_id)
            )
            for record in records
        ]

        # Keep the low-level delivery history in the API for routing diagnostics
        # and backward compatibility, but the panel archive is message-centric.
        history = self.store.recent(50)
        if delivery_id:
            selected_delivery = self.store.get(delivery_id)
            if selected_delivery is not None and not any(
                item.get("delivery_id") == delivery_id for item in history
            ):
                history.append(selected_delivery.to_dict())
        return {
            "settings": data,
            "candidates": self.candidates_payload(),
            "messages": messages,
            "selected_message_id": selected_event_id,
            "history": history,
        }

    async def async_test_channel(
        self, recipient_id: str, channel_id: str, *, language: str | None = None
    ) -> dict[str, Any]:
        settings = self.settings
        recipient = next((item for item in settings.recipients if item.recipient_id == recipient_id), None)
        if recipient is None:
            raise ValueError("recipient_not_found")
        channel = next((item for item in recipient.channels if item.channel_id == channel_id), None)
        if channel is None:
            raise ValueError("channel_not_found")
        channel = self._resolved_channel_transport(channel)
        route_language = self._recipient_language(recipient, language)
        event = self._test_event(language=route_language)
        event = self._event_for_delivery(event, None, language=route_language)
        rendered = self._render_for_channel(channel, event, language=route_language)
        # Test-send intentionally bypasses presence/event filters: the user is
        # validating this exact HA-owned channel. It still uses the normal adapter.
        try:
            await self._async_send_channel(channel, event, rendered, language=route_language)
        except Exception as err:
            return {"sent": False, "error": str(err)}
        return {"sent": True}

    async def async_finalize_telegram_callback(
        self,
        event_data: Mapping[str, Any],
        command: str,
        *,
        success: bool,
        already_processed: bool = False,
        error: str | None = None,
    ) -> None:
        """Acknowledge a Telegram button and remove stale actions from its message."""
        language = self._language()
        if success:
            if already_processed:
                status = translate("notification.callback.already_processed", language)
            else:
                key = f"notification.callback.{command}"
                status = translate(key, language)
                if status == key:
                    status = translate("notification.callback.done", language)
        else:
            status = translate("notification.callback.failed", language) + (f": {error}" if error else "")

        callback_id = event_data.get("id") or event_data.get("callback_query_id")
        if callback_id is not None and self.hass.services.has_service("telegram_bot", "answer_callback_query"):
            try:
                await self.hass.services.async_call(
                    "telegram_bot",
                    "answer_callback_query",
                    {
                        "callback_query_id": callback_id,
                        "message": status,
                        "show_alert": not success,
                    },
                    blocking=True,
                )
            except Exception as err:  # notification callbacks must never break scheduler actions
                _LOGGER.debug("Could not answer Telegram callback: %s", err)

        message = event_data.get("message")
        if not isinstance(message, Mapping) or not self.hass.services.has_service("telegram_bot", "edit_message"):
            return
        message_id = message.get("message_id")
        chat_id = event_data.get("chat_id")
        if chat_id is None:
            chat = message.get("chat")
            if isinstance(chat, Mapping):
                chat_id = chat.get("id")
        if message_id is None or chat_id is None:
            return
        original = str(message.get("text") or message.get("caption") or "").strip()
        from html import escape as _escape

        edited = f"{_escape(original)}\n\n<b>{'✅' if success else '❌'} {_escape(status)}</b>"
        edit_data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "message": edited,
            "parse_mode": "html",
            "inline_keyboard": [],
        }
        bot = event_data.get("bot")
        if isinstance(bot, Mapping) and bot.get("config_entry_id"):
            edit_data["config_entry_id"] = bot.get("config_entry_id")
        try:
            await self.hass.services.async_call("telegram_bot", "edit_message", edit_data, blocking=True)
        except Exception as err:
            _LOGGER.debug("Could not update Telegram callback message: %s", err)
