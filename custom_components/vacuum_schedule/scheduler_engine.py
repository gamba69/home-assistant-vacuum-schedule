"""Vacuum Schedule 0.8.0 state machine with physical execution protocol."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval, async_track_state_change_event

from .cleaning_scope import canonicalize_effective_cleaning_params
from .const import (CONF_PREFLIGHT_POLICY, CONF_SCHEDULES, CONF_EXECUTION_MODE, CONF_DRY_RUN_SETTINGS, CONF_REAL_EXECUTION_SETTINGS, DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES, DOMAIN, SIGNAL_JOB_UPDATED, SIGNAL_SCHEDULER_UPDATED, TARGET_TYPE_CLEANING_ZONES, LEGACY_ZONE_EXECUTION_POLICY, ZONE_EXECUTION_POLICY_COMBINED)
from .job import (JobExecutionSource, JobInstance, JobOrigin, JobReason, JobResult, JobState, ZoneExecution, ZoneJobState, ZoneResult)
from .job_store import JobStore
from .planner import OccurrencePlanner
from .dependency_index import DependencyIndex
from .input_provider import InputProvider
from .preflight import PreflightEngine
from .preflight_models import PreflightDecision, PreflightPhase, dominant_fail_reason
from .input_overrides import TestOverrideManager, OverrideMode
from .cleaning_zones import CleaningZoneControl
from .bindings import ExecutionGateMode, PreflightPolicy
from .schedule import Occurrence, ScheduleDefinition, ScheduleValidationError
from .notification_manager import NotificationManager
from .start_forecast import readiness
from .scheduler_clock import SchedulerClock
from .execution_manager import ExecutionManager
from .execution_models import ExecutionAttemptState, ExecutionMode
from .time_utils import as_utc, instant_add, instant_delta, instant_ge, instant_gt, instant_le, instant_lt
from .statistics_manager import StatisticsManager
from .scheduler_arbiter import ArbiterCandidate, SchedulerArbiter
from .force_conditions import ForceConditionEngine, force_dependencies, select_force_candidate
from .schedule_refresh import select_schedule_refresh_occurrence
from .migrations import migrate_pre_071_runtime_state, migrate_pre_074_materialization
from .external_execution import ExternalExecutionTracker
from .force_protection import (
    DEFAULT_SAFETY_BUFFER_SECONDS,
    DEFAULT_UNKNOWN_DURATION_MIN_GAP_SECONDS,
    evaluate_force_plan_protection,
)
from .live_progress import robust_observed_rate
from .status_overlays import apply_execution_lease_overlay

WATCHDOG_INTERVAL = timedelta(seconds=30)
_DEBUG_STATE_SCHEMA = 1
_TIMELINE_MATERIALIZATION_SCHEMA = 1


class SchedulerEngine:
    """Persistent deterministic scheduler with a backend execution boundary.

    SchedulerEngine owns planning/pre-flight/lifecycle only. Physical Home
    Assistant commands are reachable exclusively through RealExecutionBackend.
    """

    def __init__(self, hass: HomeAssistant, entry: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.clock = SchedulerClock(hass)
        domain_data = hass.data.setdefault(DOMAIN, {})
        volatile_by_entry = domain_data.setdefault("volatile_overrides", {})
        volatile_store = volatile_by_entry.setdefault(self.entry_id, {})
        self.overrides = TestOverrideManager(volatile_store)
        runtime = getattr(entry, "runtime_data", None)
        executor = getattr(runtime, "executor", None)
        if executor is None:
            raise RuntimeError("vacuum_schedule_runtime_not_ready")
        self.input_provider = InputProvider(hass, entry, executor, self.overrides)
        dry_settings = dict(entry.options.get(CONF_DRY_RUN_SETTINGS, {}))
        real_settings = dict(entry.options.get(CONF_REAL_EXECUTION_SETTINGS, {}))
        self.store = JobStore(hass, self.entry_id)
        self.statistics = StatisticsManager(hass, entry, self.input_provider)
        self.preflight = PreflightEngine(self.input_provider, forecast_provider=self.statistics)
        self.execution = ExecutionManager(
            hass, executor, clock=self.clock,
            dry_run_duration_seconds=int(dry_settings.get("execution_duration_seconds", 10)),
            persist_callback=self.store.async_save,
            restore_previous_settings=bool(real_settings.get("restore_previous_settings", True)),
            input_provider=self.input_provider,
            runtime_error_recovery_minutes=int(
                real_settings.get("runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES)
            ),
        )
        self.notifications = NotificationManager(hass, entry)
        self.external_execution = ExternalExecutionTracker(
            hass, self.entry_id, self.execution.real.adapter
        )
        self.dependency_index = DependencyIndex(hass, self.async_recheck_jobs)
        self._lock = asyncio.Lock()
        self._timer_unsubs: list[Callable[[], None]] = []
        self._watchdog_unsub: Callable[[], None] | None = None
        self._zone_input_unsub: Callable[[], None] | None = None
        self._zone_reconcile_requested = False
        self._zone_reconcile_task: asyncio.Task[Any] | None = None
        self._notification_action_unsub: Callable[[], None] | None = None
        self._force_input_unsub: Callable[[], None] | None = None
        self._started = False
        self._startup_recovery_in_progress = False
        self._last_recovery_status: dict[str, Any] = {}
        self._pending_offline_anchors: dict[str, JobInstance] = {}
        self._dry_run_timeline_generation = 0
        self._timeline_materialization_schema = 0
        self.arbiter = SchedulerArbiter()
        self._last_arbiter_status: dict[str, Any] = {}
        self.force_conditions = ForceConditionEngine(hass)
        self._force_status: dict[str, Any] = {
            "evaluations": [],
            "selected_candidate_job_id": None,
            "protection_policy": {
                "minimum_real_samples": 3,
                "source": "time_forecast_model",
                "hidden_safety_buffer_seconds": 0,
                "unknown_duration_fallback_seconds": 0,
            },
        }
        self._force_next_transition_at: datetime | None = None
        # Water threshold recovery and later maintenance interpretation can
        # change predictive readiness without another HA entity transition.
        # Reuse the coalesced reconcile path so pending maintenance never waits
        # for the watchdog and confirmed model updates are applied immediately.
        self.statistics.water.set_operational_update_callback(self._request_zone_reconcile)

    def _schedules(self) -> list[ScheduleDefinition]:
        result: list[ScheduleDefinition] = []
        for raw in self.entry.options.get(CONF_SCHEDULES, []):
            try:
                result.append(ScheduleDefinition.from_dict(raw))
            except (ScheduleValidationError, TypeError, ValueError):
                continue
        return result

    @property
    def execution_mode(self) -> ExecutionMode:
        """Return the config-entry-wide execution safety mode."""
        try:
            return ExecutionMode(str(self.entry.options.get(CONF_EXECUTION_MODE, ExecutionMode.DRY_RUN.value)).upper())
        except ValueError:
            return ExecutionMode.DRY_RUN

    @property
    def dry_run_settings(self) -> dict[str, Any]:
        return dict(self.entry.options.get(CONF_DRY_RUN_SETTINGS, {}))

    @property
    def real_execution_settings(self) -> dict[str, Any]:
        settings = dict(self.entry.options.get(CONF_REAL_EXECUTION_SETTINGS, {}))
        settings.setdefault("restore_previous_settings", True)
        settings.setdefault("runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES)
        return settings

    async def async_start(self) -> None:
        """Load state, recover persisted execution, then arm live callbacks.

        Startup reconciliation deliberately runs before state subscriptions,
        exact timers, or the watchdog can schedule concurrent work. This keeps
        recovery deterministic and prevents a persisted COMMAND_INTENT from
        racing a fresh start decision.
        """
        if self._started:
            return
        self._started = True
        self._startup_recovery_in_progress = True
        await self.store.async_load()
        await self.statistics.async_start(self.store.terminal_archive, self.store.events_for_job)
        await self.notifications.async_start()
        await self.external_execution.async_start()
        self._restore_debug_state()
        migration_changed = self._migrate_pre_071_runtime_state()
        migration_changed |= self._migrate_pre_074_materialization()
        if migration_changed:
            await self.store.async_save()

        await self._async_startup_recovery()
        self._startup_recovery_in_progress = False
        self._subscribe_zone_inputs()
        self._subscribe_force_inputs()
        self._subscribe_notification_actions()
        # Only after the recovery barrier may normal scheduling start a current
        # still-valid occurrence, deliver notifications, and arm timers.
        await self.async_reconcile()
        self._watchdog_unsub = async_track_time_interval(
            self.hass, self._watchdog_callback, WATCHDOG_INTERVAL
        )

    async def async_stop(self) -> None:
        """Persist and remove all timers/listeners."""
        if not self._started:
            return
        self._started = False
        self._zone_reconcile_requested = False
        self._clear_timers()
        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None
        self.dependency_index.stop()
        if self._zone_input_unsub is not None:
            self._zone_input_unsub()
            self._zone_input_unsub = None
        if self._notification_action_unsub is not None:
            self._notification_action_unsub()
            self._notification_action_unsub = None
        if self._force_input_unsub is not None:
            self._force_input_unsub()
            self._force_input_unsub = None
        self._capture_debug_state()
        await self.store.async_save()
        await self.statistics.async_sync_terminal_jobs(self.store.terminal_archive, self.store.events_for_job)
        await self.external_execution.async_stop()
        await self.statistics.async_stop()
        await self.notifications.async_stop()

    def _restore_debug_state(self) -> None:
        state = self.store.debug_state
        # 0.7.1 deliberately resets the pre-0.7.1 virtual clock once. 0.6.x and
        # 0.7.0 persisted development Test Clock offsets in the same JobStore;
        # carrying such an offset into the official scheduler can make a fresh
        # planned Job appear overdue and execute immediately after an upgrade.
        try:
            schema = int(state.get("dry_run_state_schema", 0))
        except (TypeError, ValueError):
            schema = 0
        try:
            offset_seconds = int(state.get("clock_offset_seconds", 0)) if schema >= _DEBUG_STATE_SCHEMA else 0
        except (TypeError, ValueError):
            offset_seconds = 0
        if self.execution_mode is ExecutionMode.REAL or schema < _DEBUG_STATE_SCHEMA:
            self.clock.reset()
        else:
            self.clock.set_offset(timedelta(seconds=offset_seconds))
        try:
            self._dry_run_timeline_generation = max(
                0, int(state.get("dry_run_timeline_generation", 0))
            )
        except (TypeError, ValueError):
            self._dry_run_timeline_generation = 0
        try:
            self._timeline_materialization_schema = max(
                0, int(state.get("timeline_materialization_schema", 0))
            )
        except (TypeError, ValueError):
            self._timeline_materialization_schema = 0
        raw_overrides = state.get("overrides", {})
        if isinstance(raw_overrides, dict):
            self.overrides.restore_persistent(raw_overrides)
        raw_recovery = state.get("last_startup_recovery", {})
        if isinstance(raw_recovery, dict):
            self._last_recovery_status = dict(raw_recovery)

    def _capture_debug_state(self) -> None:
        previous_recovery = self.store.debug_state.get("last_startup_recovery")
        self.store.debug_state = {
            "dry_run_state_schema": _DEBUG_STATE_SCHEMA,
            "clock_offset_seconds": int(self.clock.offset.total_seconds()),
            "dry_run_timeline_generation": self._dry_run_timeline_generation,
            "timeline_materialization_schema": self._timeline_materialization_schema,
            "overrides": self.overrides.persistent_dict(),
        }
        if isinstance(previous_recovery, dict):
            self.store.debug_state["last_startup_recovery"] = dict(previous_recovery)

    def _migrate_pre_071_runtime_state(self) -> bool:
        """Delegate the one-time pre-0.7.1 runtime migration."""
        return migrate_pre_071_runtime_state(
            self, debug_state_schema=_DEBUG_STATE_SCHEMA
        )

    def _migrate_pre_074_materialization(self) -> bool:
        """Delegate the one-time pre-0.7.4 materialization migration."""
        return migrate_pre_074_materialization(
            self, timeline_materialization_schema=_TIMELINE_MATERIALIZATION_SCHEMA
        )

    async def _async_save_debug_state(self) -> None:
        self._capture_debug_state()
        await self.store.async_save()

    def _startup_anchor_jobs(self) -> dict[str, JobInstance]:
        """Return one persisted scheduled anchor per schedule for recovery."""
        anchors: dict[str, JobInstance] = {}
        for job in sorted(self.store.active.values(), key=lambda item: item.planned_start):
            if job.origin is not JobOrigin.SCHEDULED:
                continue
            anchors.setdefault(job.schedule_id, job)
        return anchors

    def _recovered_job_from_occurrence(
        self, occurrence: Occurrence, schedule: ScheduleDefinition, now: datetime,
        *, mode: ExecutionMode, anchor: JobInstance, terminal: bool,
    ) -> JobInstance:
        """Create one startup-recovered occurrence without issuing commands."""
        job = JobInstance.from_occurrence(occurrence, now)
        job.execution_mode = mode
        job.simulation = mode is ExecutionMode.DRY_RUN
        job.metadata["notification_policy_snapshot"] = dict(schedule.notification_policy)
        job.metadata["startup_recovered_at"] = now.isoformat()
        job.metadata["startup_recovery_anchor_occurrence_id"] = anchor.occurrence_id
        job.metadata["startup_recovery_anchor_planned_start"] = anchor.planned_start.isoformat()
        if mode is ExecutionMode.DRY_RUN:
            job.metadata["dry_run_timeline_generation"] = int(
                anchor.metadata.get("dry_run_timeline_generation", self._dry_run_timeline_generation) or 0
            )
        if terminal:
            job.metadata["reconstructed_historical_occurrence"] = True
            job.metadata["suppress_notifications"] = True
        else:
            job.metadata["recovered_current_occurrence"] = True
        return job

    def _backfill_offline_occurrences(
        self, planner: OccurrencePlanner, schedules: list[ScheduleDefinition],
        anchors: dict[str, JobInstance], now: datetime,
    ) -> tuple[bool, int, int, set[str]]:
        """Reconstruct every unconsumed offline occurrence from persisted anchors.

        0.10.2 deliberately removes the old successor-displacement rule. A past
        occurrence is still current while its own deadline is open, even if a
        later occurrence has already arrived. Therefore recovery may restore more
        than one independent active Job for the same schedule. Expired occurrences
        are recorded as historical misses and are never physically caught up.
        """
        changed = False
        missed_count = 0
        current_recovered = 0
        completed_anchors: set[str] = set()
        schedule_by_id = {item.schedule_id: item for item in schedules}
        for schedule_id, anchor in anchors.items():
            schedule = schedule_by_id.get(schedule_id)
            if schedule is None or not schedule.enabled or schedule.revision != anchor.schedule_revision:
                completed_anchors.add(schedule_id)
                continue

            anchor_generation = (
                int(anchor.metadata.get("dry_run_timeline_generation", 0) or 0)
                if anchor.execution_mode is ExecutionMode.DRY_RUN
                else None
            )
            occurrence = planner.next_for_schedule(schedule, anchor.planned_start, inclusive=False)
            for _ in range(4096):
                if occurrence is None or not instant_lt(occurrence.planned_start, now):
                    completed_anchors.add(schedule_id)
                    break
                if self.store.by_occurrence_id(
                    occurrence.occurrence_id,
                    anchor.execution_mode,
                    dry_run_timeline_generation=anchor_generation,
                ) is not None:
                    occurrence = planner.next_for_schedule(
                        schedule, occurrence.planned_start, inclusive=False
                    )
                    continue

                still_valid = instant_gt(occurrence.deadline_at, now)
                recovered = self._recovered_job_from_occurrence(
                    occurrence, schedule, now, mode=anchor.execution_mode,
                    anchor=anchor, terminal=not still_valid
                )
                self._sync_zone_runs(recovered)
                if still_valid:
                    self.store.add_active(recovered)
                    self._emit_job(recovered, "startup_recovered_current")
                    current_recovered += 1
                    changed = True
                else:
                    self._finish_remaining_zones(
                        recovered, JobReason.MISSED_WHILE_OFFLINE.value, now
                    )
                    recovered.metadata["statistics_eligible"] = True
                    recovered.metadata["offline_missed_planned_start"] = occurrence.planned_start.isoformat()
                    recovered.finish(JobResult.FAILED, JobReason.MISSED_WHILE_OFFLINE.value, now)
                    self.store.archive(recovered)
                    self._emit_job(recovered, "startup_recovered_missed")
                    missed_count += 1
                    changed = True
                occurrence = planner.next_for_schedule(
                    schedule, occurrence.planned_start, inclusive=False
                )
            else:
                completed_anchors.add(schedule_id)
        return changed, missed_count, current_recovered, completed_anchors

    def _continue_offline_backfill(
        self, planner: OccurrencePlanner, schedules: list[ScheduleDefinition], now: datetime
    ) -> bool:
        """Continue deferred startup hole reconstruction after an attempt resolves."""
        if not self._pending_offline_anchors:
            return False
        changed, missed_count, current_recovered, completed = self._backfill_offline_occurrences(
            planner, schedules, self._pending_offline_anchors, now
        )
        for schedule_id in completed:
            self._pending_offline_anchors.pop(schedule_id, None)
        if missed_count or current_recovered or completed:
            self._last_recovery_status["missed_occurrences_recorded"] = int(
                self._last_recovery_status.get("missed_occurrences_recorded", 0)
            ) + missed_count
            self._last_recovery_status["current_occurrences_recovered"] = int(
                self._last_recovery_status.get("current_occurrences_recovered", 0)
            ) + current_recovered
            self._last_recovery_status["pending_anchor_schedules"] = len(
                self._pending_offline_anchors
            )
            self.store.debug_state["last_startup_recovery"] = dict(self._last_recovery_status)
        return changed or bool(completed)

    async def _async_startup_recovery(self) -> None:
        """Reconcile persistence before any live scheduler callback is armed."""
        async with self._lock:
            now = self.clock.now()
            planner = OccurrencePlanner(self.hass.config.time_zone)
            schedules = self._schedules()
            anchors = self._startup_anchor_jobs()
            changed = False
            recovered_attempt_jobs = 0
            terminal_zone_repairs = 0

            # Phase 1: persisted execution first. COMMAND_INTENT and later states
            # are observation-only and therefore cannot duplicate a start call.
            for job in list(self.store.active.values()):
                if not job.execution_attempts:
                    continue
                before_terminal_zones = sum(zone.terminal for zone in job.zone_runs.values())
                precommand_abort_reason: str | None = None
                if instant_ge(now, job.deadline_at):
                    precommand_abort_reason = JobReason.DEADLINE_EXPIRED.value
                attempt_changed = await self.execution.async_recover_job(
                    job, now, precommand_abort_reason=precommand_abort_reason
                )
                if attempt_changed:
                    recovered_attempt_jobs += 1
                    changed = True
                terminal_zone_repairs += max(
                    0, sum(zone.terminal for zone in job.zone_runs.values()) - before_terminal_zones
                )
                if self._aggregate_job_if_complete(job, now):
                    changed = True
                elif self._sync_job_state_from_zones(job, now):
                    self._emit_job(job, "startup_reconciled")
                    changed = True

            # Phase 2: close only unstarted work whose own deadline elapsed
            # offline. A later occurrence no longer displaces an independent WAIT.
            for job in list(self.store.active.values()):
                if job.origin is not JobOrigin.SCHEDULED or job.terminal:
                    continue
                if not instant_ge(now, job.deadline_at):
                    continue
                self._sync_zone_runs(job)
                changed |= self._finish_unstarted_zones(
                    job, JobReason.DEADLINE_EXPIRED.value, now
                )
                if self._aggregate_job_if_complete(job, now):
                    changed = True
                elif self._sync_job_state_from_zones(job, now):
                    self._emit_job(job, "startup_reconciled")
                    changed = True

            # Phase 3: reconstruct holes after a persisted anchor. Historical Jobs
            # are born terminal; only one currently valid occurrence can survive.
            self._pending_offline_anchors = dict(anchors)
            backfill_changed, missed_count, current_recovered, completed_anchors = (
                self._backfill_offline_occurrences(
                    planner, schedules, self._pending_offline_anchors, now
                )
            )
            for schedule_id in completed_anchors:
                self._pending_offline_anchors.pop(schedule_id, None)
            changed |= backfill_changed

            self._last_recovery_status = {
                "at": now.isoformat(),
                "persisted_anchor_schedules": len(anchors),
                "attempt_jobs_reconciled": recovered_attempt_jobs,
                "terminal_zone_repairs": terminal_zone_repairs,
                "missed_occurrences_recorded": missed_count,
                "current_occurrences_recovered": current_recovered,
                "pending_anchor_schedules": len(self._pending_offline_anchors),
                "changed": changed,
            }
            self.store.debug_state["last_startup_recovery"] = dict(self._last_recovery_status)
            self._capture_debug_state()
            self.store.debug_state["last_startup_recovery"] = dict(self._last_recovery_status)
            # Persist the completed recovery barrier before subscriptions/timers.
            await self.store.async_save()

    def _subscribe_zone_inputs(self) -> None:
        """Keep cleaning-zone status/UI reactive even when no job is active."""
        if self._zone_input_unsub is not None:
            self._zone_input_unsub()
            self._zone_input_unsub = None
        snapshot = self.input_provider.snapshot(self.clock.now())
        entity_ids = {
            entity_id
            for zone in snapshot.zones.values()
            for entity_id in zone.dependencies
            if entity_id
        }
        entity_ids.update(
            item.source_entity_id for item in snapshot.values.values() if item.source_entity_id
        )
        if self.input_provider.executor.vacuum_entity_id:
            entity_ids.add(self.input_provider.executor.vacuum_entity_id)
        # REAL execution observes more than the primary vacuum entity. Diagnostic
        # entities such as Roborock ``status`` and ``last_clean_end`` are part of
        # the physical state machine and must wake it immediately as well.
        try:
            entity_ids.update(self.execution.real.observation_dependencies())
        except Exception:
            # Registry discovery is best-effort; the primary vacuum subscription
            # and watchdog remain safe fallbacks.
            pass
        entity_ids = sorted(entity_ids)
        if not entity_ids:
            return

        @callback
        def _zone_input_changed(_event: Any) -> None:
            self._request_zone_reconcile()

        self._zone_input_unsub = async_track_state_change_event(
            self.hass, entity_ids, _zone_input_changed
        )

    @callback
    def _request_zone_reconcile(self) -> None:
        """Coalesce bursts of HA state events without losing the final update."""
        if not self._started or self._startup_recovery_in_progress:
            return
        self._zone_reconcile_requested = True
        if self._zone_reconcile_task is not None and not self._zone_reconcile_task.done():
            return

        async def _drain() -> None:
            try:
                while self._started and self._zone_reconcile_requested:
                    self._zone_reconcile_requested = False
                    await self.async_reconcile()
            finally:
                self._zone_reconcile_task = None

        self._zone_reconcile_task = self.hass.async_create_task(_drain())

    async def _async_execute_notification_command(
        self,
        job_id: str,
        command: str,
        *,
        source: str,
        recipient_id: str | None = None,
    ) -> None:
        """Execute one transport-neutral notification action with audit identity."""
        recipient = next(
            (item for item in self.notifications.settings.recipients if item.recipient_id == recipient_id),
            None,
        )
        actor_name = recipient.name if recipient is not None else None
        if command == "START_NOW":
            await self.async_start_job_now(job_id, source=source, recipient_id=recipient_id, actor_name=actor_name)
        elif command == "SKIP":
            await self.async_skip_job(job_id, source=source, recipient_id=recipient_id, actor_name=actor_name)
        elif command == "RECHECK":
            await self.async_recheck_job(job_id, source=source, recipient_id=recipient_id, actor_name=actor_name)
        elif command == "CANCEL":
            await self.async_cancel_job(job_id, source=source, recipient_id=recipient_id, actor_name=actor_name)
        else:
            raise ValueError("unsupported_notification_action")

    @callback
    def _subscribe_force_inputs(self) -> None:
        """Re-evaluate force candidates immediately when a referenced entity changes."""
        if self._force_input_unsub is not None:
            self._force_input_unsub()
            self._force_input_unsub = None
        entities: set[str] = set()
        for schedule in self._schedules():
            if schedule.force_enabled and not schedule.paused:
                entities.update(force_dependencies(schedule.force_config()))
        if not entities:
            return

        @callback
        def _changed(_event: Any) -> None:
            # A group such as "at least two people away" may receive several
            # state transitions in one short burst. Reuse the coalesced
            # operational reconcile path so the final aggregate state is never
            # lost and two callbacks cannot race to acquire the execution lease.
            self._request_zone_reconcile()

        self._force_input_unsub = async_track_state_change_event(
            self.hass, sorted(entities), _changed
        )

    def _subscribe_notification_actions(self) -> None:
        """Handle Companion App and Telegram actionable-notification callbacks."""
        if self._notification_action_unsub is not None:
            self._notification_action_unsub()
            self._notification_action_unsub = None

        @callback
        def _mobile_action(event: Any) -> None:
            parsed = self.notifications.parse_mobile_action(str(event.data.get("action", "")))
            if parsed is None:
                return
            job_id, command, recipient_id = parsed

            async def _run() -> None:
                try:
                    await self._async_execute_notification_command(
                        job_id, command, source="mobile_notification", recipient_id=recipient_id
                    )
                except ValueError:
                    return

            self.hass.async_create_task(_run())

        @callback
        def _telegram_action(event: Any) -> None:
            token = str(event.data.get("data", ""))
            parsed = self.notifications.parse_telegram_callback(token)
            if parsed is None:
                return
            job_id, command, recipient_id = parsed

            async def _run() -> None:
                success = True
                already_processed = False
                error_text: str | None = None
                try:
                    await self._async_execute_notification_command(
                        job_id, command, source="telegram_notification", recipient_id=recipient_id
                    )
                except ValueError as err:
                    error_text = str(err)
                    if error_text == "job_not_found":
                        already_processed = True
                    else:
                        success = False
                except Exception as err:  # callback failures must not affect scheduler timers
                    success = False
                    error_text = str(err)
                await self.notifications.async_finalize_telegram_callback(
                    event.data,
                    command,
                    success=success,
                    already_processed=already_processed,
                    error=error_text,
                )

            self.hass.async_create_task(_run())

        mobile_unsub = self.hass.bus.async_listen(
            "mobile_app_notification_action", _mobile_action
        )
        telegram_unsub = self.hass.bus.async_listen("telegram_callback", _telegram_action)

        def _unsubscribe() -> None:
            mobile_unsub()
            telegram_unsub()

        self._notification_action_unsub = _unsubscribe

    @callback
    def _watchdog_callback(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_reconcile())

    def _clear_timers(self) -> None:
        for unsub in self._timer_unsubs:
            unsub()
        self._timer_unsubs.clear()

    def _arm_target(self, job_id: str, kind: str, target: datetime | None) -> None:
        if target is None:
            return
        now = self.clock.now()
        if instant_le(target, now):
            return
        real_utc = self.clock.virtual_to_real_utc(target)

        @callback
        def _due(_real_now: datetime) -> None:
            self.hass.async_create_task(self._async_timer_due(job_id, kind))

        self._timer_unsubs.append(
            async_track_point_in_utc_time(self.hass, _due, real_utc)
        )

    @staticmethod
    def _job_has_unstarted_zone(job: JobInstance) -> bool:
        if not job.zone_runs:
            return True
        return any(
            zone.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)
            for zone in job.zone_runs.values()
            if not zone.terminal
        )

    def _arm_job_timers(self) -> None:
        self._clear_timers()
        for job in self.store.active.values():
            if job.terminal:
                continue
            if job.state is JobState.PLANNED:
                self._arm_target(job.job_id, "warning", job.warning_at)
                self._arm_target(job.job_id, "planned_start", job.effective_start)
            if self._job_has_unstarted_zone(job):
                self._arm_target(job.job_id, "deadline", job.deadline_at)
            self._arm_target(
                job.job_id, "preflight_recheck",
                self._metadata_dt(job, "current_preflight_next_recheck_at"),
            )
            self._arm_target(job.job_id, "notification", self.notifications.next_due_at(job))
            self._arm_target(
                job.job_id, "execution", self.execution.next_transition_at(job)
            )
        self._arm_target("force", "force_condition", self._force_next_transition_at)
        # Normal reconciliation keeps one future placeholder per enabled
        # schedule, independently from older WAIT/RUNNING occurrences. This
        # fallback only protects transient gaps while settings are reconciled.
        planner = OccurrencePlanner(self.hass.config.time_zone)
        now = self.clock.now()
        for schedule in self._schedules():
            if not schedule.enabled or self.store.active_for_schedule(schedule.schedule_id):
                continue
            occurrence = self._candidate_occurrence(planner, schedule, now)
            if occurrence is not None and instant_gt(occurrence.warning_at, now):
                self._arm_target(f"schedule:{schedule.schedule_id}", "activation", occurrence.warning_at)

    @staticmethod
    def _metadata_dt(job: JobInstance, key: str) -> datetime | None:
        value = job.metadata.get(key)
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _zone_metadata_dt(zone_run: ZoneExecution, key: str) -> datetime | None:
        value = zone_run.metadata.get(key)
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    async def _async_timer_due(self, _job_id: str, _kind: str) -> None:
        await self.async_reconcile()

    def _candidate_occurrence(
        self, planner: OccurrencePlanner, schedule: ScheduleDefinition, now: datetime
    ):
        """Return the next not-yet-started occurrence for fresh materialization.

        A missing Job must never resurrect an occurrence whose planned_start is
        already in the past merely because its execution window is still open.
        Late execution is reserved for a Job that was materialized before its
        planned start and persisted across WAIT/restart.
        """
        return planner.next_for_schedule(schedule, now, inclusive=True)

    def _occurrence_consumed(
        self,
        occurrence: Occurrence,
        mode: ExecutionMode,
        *,
        exclude_job_id: str | None = None,
    ) -> bool:
        """Return whether one occurrence is consumed in the active namespace."""
        dry_generation = (
            self._dry_run_timeline_generation
            if mode is ExecutionMode.DRY_RUN
            else None
        )
        owner = self.store.by_occurrence_id(
            occurrence.occurrence_id,
            mode,
            dry_run_timeline_generation=dry_generation,
            exclude_job_id=exclude_job_id,
        )
        if owner is not None:
            return True
        # occurrence_id includes the Schedule revision.  A revision change
        # while an occurrence is already running must not make the very same
        # planned slot executable for a second time under the new revision.
        return self.store.by_schedule_slot(
            occurrence.schedule_id,
            occurrence.planned_start,
            mode,
            dry_run_timeline_generation=dry_generation,
            exclude_job_id=exclude_job_id,
        ) is not None

    def _dematerialize_shadowed_schedule_slots(self, now: datetime) -> bool:
        """Drop stale unstarted projections already owned by started/history work.

        This repairs persisted states created before 0.12.18 where a Schedule
        revision could materialize another Job for the same planned_start while
        the previous-revision Job had already crossed the execution boundary.
        Explicit operator re-arms are preserved.
        """
        changed = False
        all_jobs = [
            *self.store.active.values(),
            *self.store.history,
            *self.store.terminal_archive,
        ]
        for job in list(self.store.active.values()):
            if job.origin is not JobOrigin.SCHEDULED:
                continue
            if job.execution_attempts or job.state in (JobState.STARTING, JobState.RUNNING):
                continue
            if bool(job.metadata.get("rearmed_by_schedule_refresh")):
                continue
            dry_generation = (
                int(job.metadata.get("dry_run_timeline_generation", 0) or 0)
                if job.execution_mode is ExecutionMode.DRY_RUN
                else None
            )
            owners = [
                other
                for other in all_jobs
                if other.job_id != job.job_id
                and other.origin is JobOrigin.SCHEDULED
                and other.schedule_id == job.schedule_id
                and other.execution_mode is job.execution_mode
                and other.planned_start == job.planned_start
                and (
                    job.execution_mode is not ExecutionMode.DRY_RUN
                    or int(other.metadata.get("dry_run_timeline_generation", 0) or 0)
                    == dry_generation
                )
            ]
            if not any(
                other.terminal
                or other.execution_attempts
                or other.state in (JobState.STARTING, JobState.RUNNING)
                or other.actual_start is not None
                for other in owners
            ):
                continue
            self.dependency_index.remove_job(job.job_id)
            if self.store.discard_active_projection(job.job_id) is not None:
                changed = True
        return changed

    def _next_unconsumed_occurrence(
        self,
        planner: OccurrencePlanner,
        schedule: ScheduleDefinition,
        occurrence: Occurrence | None,
        mode: ExecutionMode,
        *,
        exclude_job_id: str | None = None,
    ) -> Occurrence | None:
        """Advance over occurrences already consumed in one execution namespace."""
        for _ in range(64):
            if occurrence is None or not self._occurrence_consumed(
                occurrence, mode, exclude_job_id=exclude_job_id
            ):
                return occurrence
            occurrence = planner.next_for_schedule(
                schedule, occurrence.planned_start, inclusive=False
            )
        return None

    def _timeline_candidate_occurrence(
        self,
        planner: OccurrencePlanner,
        schedule: ScheduleDefinition,
        now: datetime,
        *,
        exclude_job_id: str | None = None,
    ) -> Occurrence | None:
        """Return the nearest occurrence whose planned start is not in the past.

        Explicit timeline recovery is anchored strictly to ``planned_start``.
        ``warning_at`` and ``deadline_at`` describe the lifecycle of an already
        materialized Job and must never make a past occurrence eligible for new
        materialization. Therefore recovery chooses the first occurrence with
        ``planned_start >= now`` and advances only over occurrences consumed in
        the current execution namespace.

        This distinction is critical for REAL execution: an execution window may
        allow an existing WAIT Job to continue, but it never authorizes recovery
        to create a missing past Job and start the robot late.
        """
        upcoming = planner.next_for_schedule(schedule, now, inclusive=True)
        return self._next_unconsumed_occurrence(
            planner,
            schedule,
            upcoming,
            self.execution_mode,
            exclude_job_id=exclude_job_id,
        )

    def _dematerialize_stale_future_jobs(
        self, schedules: dict[str, ScheduleDefinition], now: datetime
    ) -> bool:
        """Replace future pre-warning placeholders without counting failures."""
        changed = False
        for job in list(self.store.active.values()):
            if job.origin is JobOrigin.MANUAL:
                continue
            # Never invalidate already-started execution just because the
            # schedule definition changed. This is especially critical for REAL
            # execution where commands may already have reached the robot.
            if job.execution_attempts or job.state in (JobState.STARTING, JobState.RUNNING):
                continue
            schedule = schedules.get(job.schedule_id)
            revision_matches = (
                schedule is not None
                and schedule.enabled
                and schedule.revision == job.schedule_revision
            )
            if revision_matches:
                continue
            if job.state is JobState.PLANNED and instant_lt(now, job.warning_at):
                self.store.append_event(
                    job,
                    "dematerialized",
                    now,
                    details={"reason": "schedule_definition_changed_before_warning"},
                )
                self.store.remove_active(job.job_id)
                changed = True
                continue
            reason = (
                JobReason.SCHEDULE_REMOVED_AFTER_WARNING
                if schedule is None
                else JobReason.SCHEDULE_DISABLED_BY_USER
                if not schedule.enabled
                else JobReason.SCHEDULE_CHANGED_AFTER_WARNING
            )
            if job.state in (JobState.PLANNED, JobState.WAIT):
                changed |= self._finish(job, JobResult.FAILED, reason.value, now)
        return changed

    def _materialize_missing(
        self, planner: OccurrencePlanner, schedules: list[ScheduleDefinition], now: datetime
    ) -> bool:
        """Keep one future placeholder while preserving independent current Jobs.

        0.10.2 allows multiple non-terminal scheduled JobInstances for one
        schedule when older occurrences are still WAITing or executing. Only the
        *future placeholder* is limited to one. When that placeholder reaches its
        planned start, the next occurrence is materialized immediately, so an old
        WAIT never hides or blocks a later scheduled occurrence.
        """
        changed = False
        mode = self.execution_mode
        for schedule in schedules:
            if not schedule.enabled:
                continue
            existing = [
                job for job in self.store.active_for_schedule(schedule.schedule_id)
                if job.origin is JobOrigin.SCHEDULED
                and job.execution_mode is mode
                and job.schedule_revision == schedule.revision
            ]
            if any(instant_gt(job.planned_start, now) for job in existing):
                continue

            occurrence = self._next_unconsumed_occurrence(
                planner, schedule, self._candidate_occurrence(planner, schedule, now), mode
            )
            if occurrence is None:
                continue

            job = JobInstance.from_occurrence(occurrence, now)
            job.execution_mode = mode
            job.simulation = mode is ExecutionMode.DRY_RUN
            job.metadata["notification_policy_snapshot"] = dict(schedule.notification_policy)
            if mode is ExecutionMode.DRY_RUN:
                job.metadata["dry_run_timeline_generation"] = self._dry_run_timeline_generation
            self.store.add_active(job)
            self._emit_job(job, "created")
            changed = True
        return changed

    def _finish(
        self, job: JobInstance, result: JobResult, reason_code: str, now: datetime
    ) -> bool:
        if not job.finish(result, reason_code, now):
            return False
        self.dependency_index.remove_job(job.job_id)
        self.store.archive(job)
        self._emit_job(job, "finished")
        return True

    def _sync_zone_runs(self, job: JobInstance) -> dict[str, Any]:
        configured = {zone.zone_id: zone for zone in self.input_provider.cleaning_zones}
        for zone_id in job.targets:
            job.zone_runs.setdefault(str(zone_id), ZoneExecution(zone_id=str(zone_id)))
        for zone_id, zone_run in job.zone_runs.items():
            zone = configured.get(zone_id)
            if zone is not None and not zone_run.terminal:
                zone_run.zone_name = zone.name
                zone_run.robot_target_type = zone.robot_target_type.value
                zone_run.robot_target_id = zone.robot_target_id
                if "nominal_area_m2" not in zone_run.metadata:
                    zone_run.metadata["nominal_area_m2"] = zone.nominal_area_m2
                    zone_run.metadata["nominal_area_source"] = (
                        "manual" if zone.nominal_area_m2 is not None else "unknown"
                    )
        return configured

    @staticmethod
    def _zone_reason_from_report(report: Any) -> str:
        """Return the concrete authoritative FAIL reason for one zone."""
        return dominant_fail_reason(report, JobReason.PREFLIGHT_FAILED.value)

    def _finish_remaining_zones(self, job: JobInstance, reason: str, now: datetime) -> bool:
        changed = False
        for zone_run in job.zone_runs.values():
            if not zone_run.terminal:
                changed |= zone_run.finish(ZoneResult.FAILED, reason, now)
        return changed

    def _finish_unstarted_zones(self, job: JobInstance, reason: str, now: datetime) -> bool:
        """Close only zones that have not begun execution."""
        return job.finish_unstarted_zones(reason, now)

    def _suppress_unstarted_zones(self, job: JobInstance, reason: str, now: datetime) -> bool:
        """Administratively skip work that has not started physical execution.

        Global schedule execution disable is an operator policy, not an
        execution failure.  Already STARTING/RUNNING work keeps its lease; only
        PLANNED/WAIT zone work is closed as SKIPPED.
        """
        changed = False
        for zone_run in job.zone_runs.values():
            if zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT):
                changed |= zone_run.finish(ZoneResult.SKIPPED, reason, now)
        return changed

    def _execution_gate_suppression_reason(self, now: datetime) -> str | None:
        """Return the active administrative gate reason at *now*, if any."""
        policy = self.input_provider.policy
        if policy.execution_gate is ExecutionGateMode.DISABLED:
            return JobReason.GLOBAL_DISABLED.value
        if policy.execution_gate is ExecutionGateMode.DISABLED_UNTIL:
            until = policy.disabled_until_datetime
            if until is None:
                return JobReason.GLOBAL_DISABLED_UNTIL.value
            if until.tzinfo is None or until.utcoffset() is None:
                until = until.replace(tzinfo=now.tzinfo)
            if instant_lt(now, until):
                return JobReason.GLOBAL_DISABLED_UNTIL.value
        return None

    def _schedule_pause_suppression_reason(self, job: JobInstance) -> str | None:
        """Return per-schedule administrative suppression without creating WAIT."""
        try:
            schedule = self._schedule_by_id(job.schedule_id)
        except ValueError:
            return None
        if schedule.enabled and schedule.paused:
            return JobReason.SCHEDULE_PAUSED.value
        return None

    @staticmethod
    def _job_has_started_physical_work(job: JobInstance) -> bool:
        """Return whether any physical execution for this occurrence began."""
        if job.execution_attempts:
            return True
        return any(
            zone.actual_start is not None
            or zone.state in (ZoneJobState.STARTING, ZoneJobState.RUNNING)
            or zone.result is ZoneResult.SUCCESS
            for zone in job.zone_runs.values()
        )

    def _aggregate_job_if_complete(self, job: JobInstance, now: datetime) -> bool:
        if not job.zone_runs or not all(zone.terminal for zone in job.zone_runs.values()):
            return False
        results = [zone.result for zone in job.zone_runs.values()]
        successes = sum(result is ZoneResult.SUCCESS for result in results)
        if successes == len(results):
            return self._finish(job, JobResult.SUCCESS, JobReason.EXECUTION_SUCCESS.value, now)
        if successes > 0:
            # Preserve an explicit user-initiated terminal cause when a Job was
            # partially completed before the user cancelled/skipped the
            # remaining work.  The aggregate result stays PARTIAL_SUCCESS, but
            # notification policy can now suppress the redundant confirmation
            # and render the correct semantic title/reason.
            user_reason = next(
                (
                    zone.reason_code
                    for zone in job.zone_runs.values()
                    if zone.result is not ZoneResult.SUCCESS
                    and zone.reason_code in {
                        JobReason.USER_CANCELLED.value,
                        JobReason.USER_SKIPPED.value,
                        JobReason.SCHEDULE_DISABLED_BY_USER.value,
                        JobReason.GLOBAL_DISABLED.value,
                        JobReason.GLOBAL_DISABLED_UNTIL.value,
                        JobReason.SCHEDULE_PAUSED.value,
                    }
                ),
                None,
            )
            return self._finish(
                job,
                JobResult.PARTIAL_SUCCESS,
                user_reason or JobReason.PARTIAL_SUCCESS.value,
                now,
            )
        # No physical target succeeded: the scheduled cleaning did not happen.
        dominant = next((zone.reason_code for zone in job.zone_runs.values() if zone.reason_code), JobReason.NO_ZONE_SUCCEEDED.value)
        return self._finish(job, JobResult.FAILED, dominant, now)

    def _sync_job_state_from_zones(self, job: JobInstance, now: datetime) -> bool:
        if job.terminal:
            return False
        active = [zone for zone in job.zone_runs.values() if not zone.terminal]
        if not active:
            return False
        if any(zone.state is ZoneJobState.RUNNING for zone in active):
            state = JobState.RUNNING
        elif any(zone.state is ZoneJobState.STARTING for zone in active):
            state = JobState.STARTING
        elif any(zone.state is ZoneJobState.WAIT for zone in active):
            state = JobState.WAIT
        else:
            state = JobState.PLANNED
        before = job.state
        job.transition(state, now)
        return before is not job.state

    def _capture_advisory_snapshot(
        self, job: JobInstance, report: Any, now: datetime
    ) -> bool:
        """Persist the first advisory report as an immutable user-facing snapshot."""
        if job.advisory_checked_at is not None:
            return False
        job.advisory_blockers = report.blocker_codes
        job.advisory_preflight_decision = report.decision.value
        job.advisory_checked_at = now
        job.metadata["advisory_preflight_report"] = report.to_dict(include_snapshot=True)
        self._emit_job(job, "updated")
        return True

    def _update_current_preflight(self, job: JobInstance, report: Any) -> bool:
        """Update live readiness without mutating the advisory snapshot."""
        decision = report.decision.value
        codes = report.blocker_codes
        next_recheck = report.next_recheck_at.isoformat() if report.next_recheck_at else None
        signature = {
            "decision": decision,
            "blockers": [item.to_dict() for item in report.blockers],
            "dependencies": list(report.dependencies),
            "next_recheck_at": next_recheck,
        }
        changed = job.metadata.get("current_preflight_signature") != signature
        job.current_blockers = codes
        job.current_preflight_decision = decision
        if changed:
            job.metadata["current_preflight_signature"] = signature
            job.metadata["current_preflight_report"] = report.to_dict(include_snapshot=True)
        if next_recheck:
            job.metadata["current_preflight_next_recheck_at"] = next_recheck
        else:
            job.metadata.pop("current_preflight_next_recheck_at", None)
        self.dependency_index.update_job(job.job_id, report.dependencies)
        if changed:
            self._emit_job(job, "updated")
        return changed

    @staticmethod
    def _zone_execution_policy(job: JobInstance) -> str:
        """Return the occurrence-snapshotted zone execution policy.

        Jobs persisted before 0.8.5 have no policy snapshot and therefore keep
        the historical progressive behavior after upgrade.
        """
        return str(job.metadata.get("zone_execution_policy", LEGACY_ZONE_EXECUTION_POLICY))

    async def _prepare_job(
        self, job: JobInstance, now: datetime
    ) -> tuple[bool, tuple[str, ...]]:
        """Advance one Job without acquiring the global execution lease.

        The preparation phase owns lifecycle recovery and authoritative
        pre-flight. It may leave a Job in WAIT, but it never starts physical
        execution. Runnable Jobs are returned to SchedulerArbiter so one WAIT can
        never block another independent Job by control-flow position.
        """
        if job.terminal:
            return False, ()
        changed = False
        configured = self._sync_zone_runs(job)

        if job.target_type != TARGET_TYPE_CLEANING_ZONES:
            self._finish_remaining_zones(job, "legacy_target_model", now)
            return self._finish(job, JobResult.FAILED, "legacy_target_model", now), ()

        # Existing backend attempts always progress before fresh arbitration.
        changed |= await self.execution.async_progress_job(job, now)
        if self._aggregate_job_if_complete(job, now):
            return True, ()

        # 0.11.9: the global execution gate is an administrative suppression,
        # not a failed pre-flight.  Variant B semantics: if the automatic
        # scheduled occurrence reaches its release time while the gate is
        # closed, it is consumed and never catches up after re-enable.  Explicit
        # user start-now releases keep the historical pre-flight behavior.
        suppression_reason = (
            (self._execution_gate_suppression_reason(now) or self._schedule_pause_suppression_reason(job))
            if instant_ge(now, job.effective_start)
            and job.origin is JobOrigin.SCHEDULED
            and job.manual_release_at is None
            else None
        )
        if suppression_reason is not None:
            had_started = self._job_has_started_physical_work(job)
            changed |= self._suppress_unstarted_zones(job, suppression_reason, now)
            if not had_started:
                # No physical attempt was made: this is a dedicated terminal
                # non-error outcome and therefore must not inflate failures or WAIT.
                return self._finish(job, JobResult.SUPPRESSED, suppression_reason, now) or changed, ()
            # Progressive Job: already-started work is allowed to finish, while
            # no additional zones may start after the operator closes the gate.
            if self._aggregate_job_if_complete(job, now):
                return True, ()
            if self._sync_job_state_from_zones(job, now):
                changed = True
            if changed and not job.terminal:
                job.blockers = ()
                job.current_blockers = ()
                job.current_preflight_decision = PreflightDecision.PASS.value
                self._emit_job(job, "updated")
            return changed, ()

        # 0.10.2: only the occurrence's own deadline is terminal. A later
        # occurrence is independent and does not displace this Job.
        if instant_ge(now, job.deadline_at):
            changed |= self._finish_unstarted_zones(
                job, JobReason.DEADLINE_EXPIRED.value, now
            )
            if self._aggregate_job_if_complete(job, now):
                return True, ()
            if self._sync_job_state_from_zones(job, now):
                changed = True
            if changed:
                job.blockers = tuple(
                    sorted({blocker for zone in job.zone_runs.values() for blocker in zone.blockers})
                )
                job.current_blockers = job.blockers
                job.current_preflight_decision = (
                    PreflightDecision.WAIT.value if job.blockers else PreflightDecision.PASS.value
                )
                self._emit_job(job, "updated")
            return changed, ()

        if (
            job.state is JobState.PLANNED
            and instant_ge(now, job.warning_at)
            and job.advisory_checked_at is None
        ):
            report = self.preflight.evaluate(job, PreflightPhase.ADVISORY, now)
            changed |= self._capture_advisory_snapshot(job, report, now)

        # 0.11.6: once the advisory checkpoint has happened, the "Readiness"
        # column is a live preview all the way until the occurrence is released.
        # Previously PLANNED jobs returned here without refreshing current
        # pre-flight, so a changed execution gate / occupancy / route / resource
        # sensor could leave the advisory-era blocker visible until planned_start.
        # The advisory snapshot itself remains immutable.
        if instant_lt(now, job.effective_start):
            if job.state is JobState.PLANNED and job.advisory_checked_at is not None:
                pending_zone_ids = tuple(
                    zone_id
                    for zone_id, zone_run in job.zone_runs.items()
                    if not zone_run.terminal
                    and zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)
                )
                if pending_zone_ids:
                    live_report = self.preflight.evaluate(
                        job,
                        PreflightPhase.CURRENT_PREVIEW,
                        now,
                        zone_ids=pending_zone_ids,
                    )
                    changed |= self._update_current_preflight(job, live_report)
            return changed, ()

        # Zone-level disable/disabled-until is a per-occurrence skip.
        for zone_id, zone_run in job.zone_runs.items():
            if zone_run.terminal:
                continue
            zone = configured.get(zone_id)
            if zone is None:
                changed |= zone_run.finish(ZoneResult.FAILED, "invalid_configuration", now)
                continue
            if zone.control is CleaningZoneControl.DISABLED or zone.disabled_at(job.planned_start):
                reason = (
                    "zone_disabled_until"
                    if zone.control is CleaningZoneControl.DISABLED_UNTIL
                    else "zone_disabled"
                )
                changed |= zone_run.finish(ZoneResult.SKIPPED, reason, now)

        all_dependencies: set[str] = set()
        zone_reports: dict[str, Any] = {}
        ready: list[ZoneExecution] = []
        for zone_id, zone_run in job.zone_runs.items():
            if zone_run.terminal or zone_run.state in (ZoneJobState.STARTING, ZoneJobState.RUNNING):
                continue
            report = self.preflight.evaluate_zone(
                job, zone_id, PreflightPhase.AUTHORITATIVE, now
            )
            zone_reports[zone_id] = report.to_dict(include_snapshot=True)
            all_dependencies.update(report.dependencies)
            if report.decision is PreflightDecision.FAIL:
                changed |= zone_run.finish(
                    ZoneResult.FAILED, self._zone_reason_from_report(report), now
                )
            elif report.decision is PreflightDecision.WAIT:
                new_blockers = report.blocker_codes
                if zone_run.blockers != new_blockers or zone_run.state is not ZoneJobState.WAIT:
                    zone_run.blockers = new_blockers
                    zone_run.transition(ZoneJobState.WAIT, now)
                    changed = True
            else:
                # A previous execution_lease_busy marker is scheduler-local and
                # is cleared by successful authoritative pre-flight. The WAIT
                # state itself is preserved until arbitration either starts the
                # Job or confirms that the lease is still busy, avoiding noisy
                # WAIT re-entry cycles on every watchdog pass.
                if zone_run.blockers:
                    zone_run.blockers = ()
                    changed = True
                ready.append(zone_run)

        if zone_reports:
            signature = {key: value for key, value in sorted(zone_reports.items())}
            if job.metadata.get("current_zone_preflight_reports") != signature:
                job.metadata["current_zone_preflight_reports"] = signature
                changed = True
        self.dependency_index.update_job(job.job_id, tuple(sorted(all_dependencies)))

        if self._zone_execution_policy(job) == ZONE_EXECUTION_POLICY_COMBINED:
            failed_zones = [
                zone_run
                for zone_run in job.zone_runs.values()
                if zone_run.terminal and zone_run.result is ZoneResult.FAILED
            ]
            has_started = bool(job.execution_attempts) or any(
                zone_run.actual_start is not None
                or zone_run.state in (ZoneJobState.STARTING, ZoneJobState.RUNNING)
                for zone_run in job.zone_runs.values()
            )
            active_target_types = {
                configured[zone_run.zone_id].robot_target_type.value
                for zone_run in job.zone_runs.values()
                if not zone_run.terminal and zone_run.zone_id in configured
            }
            if len(active_target_types) > 1 and not has_started:
                reason = "combined_execution_mixed_target_types"
                changed |= self._finish_remaining_zones(job, reason, now)
                if all(zone.terminal for zone in job.zone_runs.values()):
                    return self._finish(job, JobResult.FAILED, reason, now) or changed, ()
            if failed_zones and not has_started:
                failure_reason = next(
                    (zone.reason_code for zone in failed_zones if zone.reason_code),
                    JobReason.PREFLIGHT_FAILED.value,
                )
                changed |= self._finish_remaining_zones(job, failure_reason, now)
                if all(zone.terminal for zone in job.zone_runs.values()):
                    return self._finish(job, JobResult.FAILED, failure_reason, now) or changed, ()

            active_unstarted = [
                zone_run
                for zone_run in job.zone_runs.values()
                if not zone_run.terminal
                and zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)
            ]
            if active_unstarted:
                ready_ids = {zone.zone_id for zone in ready}
                if any(zone.zone_id not in ready_ids for zone in active_unstarted):
                    ready = []

        pending_zone_ids = tuple(
            zone_id
            for zone_id, zone_run in job.zone_runs.items()
            if not zone_run.terminal
            and zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)
        )
        if pending_zone_ids:
            live_report = self.preflight.evaluate(
                job, PreflightPhase.CURRENT_PREVIEW, now, zone_ids=pending_zone_ids
            )
            changed |= self._update_current_preflight(job, live_report)

        shared_blocked = self.execution.shared_target_blocked_zone_ids(job, ready)
        if shared_blocked:
            launch_ready: list[ZoneExecution] = []
            for zone_run in ready:
                if zone_run.zone_id in shared_blocked:
                    if (
                        zone_run.state is not ZoneJobState.WAIT
                        or zone_run.blockers != ("shared_target_waiting",)
                    ):
                        zone_run.blockers = ("shared_target_waiting",)
                        zone_run.transition(ZoneJobState.WAIT, now)
                        changed = True
                else:
                    launch_ready.append(zone_run)
            ready = launch_ready

        if self._aggregate_job_if_complete(job, now):
            return True, ()
        if self._sync_job_state_from_zones(job, now):
            changed = True
        if changed and not job.terminal:
            job.blockers = tuple(
                sorted({blocker for zone in job.zone_runs.values() for blocker in zone.blockers})
            )
            job.current_blockers = job.blockers
            if job.blockers:
                job.current_preflight_decision = PreflightDecision.WAIT.value
            elif any(not zone.terminal for zone in job.zone_runs.values()):
                job.current_preflight_decision = PreflightDecision.PASS.value
            self._emit_job(job, "updated")
        return changed, tuple(zone.zone_id for zone in ready if not zone.terminal)

    def _force_readiness(
        self,
        job: JobInstance,
        now: datetime,
        configured: dict[str, Any],
    ) -> tuple[tuple[str, ...], list[str], dict[str, list[str]], list[datetime], PreflightDecision]:
        """Return read-only zone readiness for one early-run probe.

        A Force probe must never mutate Job/Zone lifecycle state. FAIL/WAIT here
        therefore only removes the candidate from the current arbitration cycle;
        normal scheduled execution remains untouched for ``planned_start``.
        """
        ready_zone_ids: list[str] = []
        blocking_codes: list[str] = []
        deferred_by_zone: dict[str, list[str]] = {}
        next_rechecks: list[datetime] = []
        considered_zone_ids: list[str] = []
        active_target_types: set[str] = set()
        blocked_decisions: list[PreflightDecision] = []
        combined = self._zone_execution_policy(job) == ZONE_EXECUTION_POLICY_COMBINED

        for zone_id in job.targets:
            zone_run = job.zone_runs.get(str(zone_id))
            if zone_run is not None and zone_run.terminal:
                continue
            zone = configured.get(str(zone_id))
            if zone is None:
                blocking_codes.append("invalid_configuration")
                blocked_decisions.append(PreflightDecision.FAIL)
                deferred_by_zone[str(zone_id)] = ["invalid_configuration"]
                continue
            # Disabled/disabled-until is snapshotted against the occurrence day
            # and is simply omitted from a force probe, exactly as it would be
            # skipped once the normal occurrence becomes authoritative.
            if zone.control is CleaningZoneControl.DISABLED or zone.disabled_at(job.planned_start):
                continue
            considered_zone_ids.append(str(zone_id))
            active_target_types.add(zone.robot_target_type.value)
            report = self.preflight.evaluate_zone(
                job, str(zone_id), PreflightPhase.CURRENT_PREVIEW, now
            )
            if report.next_recheck_at is not None and instant_gt(report.next_recheck_at, now):
                next_rechecks.append(report.next_recheck_at)
            if report.decision is PreflightDecision.PASS:
                ready_zone_ids.append(str(zone_id))
            else:
                codes = list(report.blocker_codes)
                blocked_decisions.append(report.decision)
                deferred_by_zone[str(zone_id)] = codes
                if combined:
                    blocking_codes.extend(codes)

        if not considered_zone_ids:
            blocking_codes.append("no_force_runnable_zones")
            return (
                (), sorted(set(blocking_codes)), deferred_by_zone, next_rechecks,
                PreflightDecision.FAIL,
            )

        # Combined execution cannot issue one physical command for mixed target
        # types and must wait until every active logical zone is simultaneously
        # ready. Progressive schedules may force only their currently runnable
        # subset and leave the rest untouched for later normal progression.
        if combined:
            if len(active_target_types) > 1:
                blocking_codes.append("combined_execution_mixed_target_types")
                blocked_decisions.append(PreflightDecision.FAIL)
                ready_zone_ids = []
            elif len(ready_zone_ids) != len(considered_zone_ids):
                ready_zone_ids = []

        ready_runs = [
            job.zone_runs[zone_id]
            for zone_id in ready_zone_ids
            if zone_id in job.zone_runs and not job.zone_runs[zone_id].terminal
        ]
        shared_blocked = self.execution.shared_target_blocked_zone_ids(job, ready_runs)
        if shared_blocked:
            ready_zone_ids = [zone_id for zone_id in ready_zone_ids if zone_id not in shared_blocked]
            for zone_id in sorted(shared_blocked):
                deferred_by_zone.setdefault(zone_id, []).append("shared_target_waiting")
            if combined:
                blocking_codes.append("shared_target_waiting")
                blocked_decisions.append(PreflightDecision.WAIT)
                ready_zone_ids = []

        if not ready_zone_ids:
            if not blocking_codes:
                # Progressive schedules can have every zone temporarily blocked.
                # Surface the real zone blockers without mutating them into WAIT.
                blocking_codes.extend(
                    code
                    for codes in deferred_by_zone.values()
                    for code in codes
                )
            if not blocking_codes:
                blocking_codes.append("no_force_runnable_zones")

        decision = PreflightDecision.PASS
        if not ready_zone_ids or blocking_codes:
            decision = (
                PreflightDecision.FAIL
                if PreflightDecision.FAIL in blocked_decisions
                else PreflightDecision.WAIT
            )
        return (
            tuple(ready_zone_ids),
            sorted(set(blocking_codes)),
            deferred_by_zone,
            next_rechecks,
            decision,
        )

    def _evaluate_force_candidates(self, now: datetime) -> dict[str, tuple[str, ...]]:
        """Compute early-run candidates for the global SchedulerArbiter.

        Evaluation remains read-only. 0.10.6 enables physical/simulated early
        execution only after the selected candidate passes through the same
        global Arbiter and ExecutionLease boundary as every normal Job.
        """
        evaluations: list[dict[str, Any]] = []
        next_transitions: list[datetime] = []
        lease_owner = self.execution.lease_owner(list(self.store.active.values()))
        configured = {zone.zone_id: zone for zone in self.input_provider.cleaning_zones}
        schedules = {schedule.schedule_id: schedule for schedule in self._schedules()}

        for job in sorted(self.store.active.values(), key=lambda item: as_utc(item.planned_start)):
            schedule = schedules.get(job.schedule_id)
            if (
                schedule is None
                or schedule.paused
                or
                job.terminal
                or job.origin is not JobOrigin.SCHEDULED
                or job.state is not JobState.PLANNED
                or job.manual_release_at is not None
                or job.execution_attempts
                or isinstance(job.metadata.get("force_execution"), dict)
            ):
                self.force_conditions.reset_job(job.job_id)
                continue
            config = job.metadata.get("force_snapshot")
            if not isinstance(config, dict) or not bool(config.get("enabled")):
                self.force_conditions.reset_job(job.job_id)
                continue
            try:
                advance_minutes = max(1, int(config.get("max_advance_minutes", 0) or 0))
                priority = int(config.get("priority", 0) or 0)
                preempts_scheduled = bool(config.get("preempts_scheduled", False))
            except (TypeError, ValueError):
                continue
            groups = config.get("condition_groups", ()) or ()
            window_start = instant_add(job.planned_start, -timedelta(minutes=advance_minutes))
            evaluation: dict[str, Any] = {
                "job_id": job.job_id,
                "occurrence_id": job.occurrence_id,
                "schedule_id": job.schedule_id,
                "schedule_name": job.schedule_name,
                "planned_start": job.planned_start.isoformat(),
                "window_start": window_start.isoformat(),
                "max_advance_minutes": advance_minutes,
                "priority": priority,
                "preempts_scheduled": preempts_scheduled,
                "window_state": "BEFORE_WINDOW",
                "conditions_matched": False,
                "eligible": False,
                "selected": False,
                "execution_selected": False,
                "preflight_decision": None,
                "preflight_blockers": [],
                "deferred_zone_blockers": {},
                "ready_zone_ids": [],
                "condition_evaluation": {"matched": False, "groups": [], "next_transition_at": None},
                "plan_protection": None,
            }
            if instant_lt(now, window_start):
                next_transitions.append(window_start)
                self.force_conditions.reset_job(job.job_id)
                evaluations.append(evaluation)
                continue
            if instant_ge(now, job.planned_start):
                evaluation["window_state"] = "ENDED"
                self.force_conditions.reset_job(job.job_id)
                evaluations.append(evaluation)
                continue

            evaluation["window_state"] = "IN_WINDOW"
            condition_result = self.force_conditions.evaluate(
                job_id=job.job_id, groups=groups, now=now
            )
            evaluation["condition_evaluation"] = condition_result
            evaluation["conditions_matched"] = bool(condition_result.get("matched"))
            next_condition = condition_result.get("next_transition_at")
            if next_condition:
                try:
                    next_transitions.append(datetime.fromisoformat(str(next_condition)))
                except ValueError:
                    pass
            next_transitions.append(job.planned_start)
            if not evaluation["conditions_matched"]:
                evaluations.append(evaluation)
                continue

            ready_zone_ids, blockers, deferred, rechecks, readiness_decision = self._force_readiness(
                job, now, configured
            )
            next_transitions.extend(rechecks)
            protection = None
            if ready_zone_ids and not blockers:
                if preempts_scheduled:
                    protection = {
                        "allowed": True,
                        "reason": "preempts_scheduled",
                        "override_plan_protection": True,
                    }
                else:
                    protection = self._force_plan_protection(job, ready_zone_ids, now)
                latest_safe_start = protection.get("latest_safe_start")
                if latest_safe_start and bool(protection.get("allowed")):
                    try:
                        # Equality is allowed (>= required gap), therefore the
                        # exact transition to unsafe is one second later.
                        next_transitions.append(
                            datetime.fromisoformat(str(latest_safe_start)) + timedelta(seconds=1)
                        )
                    except ValueError:
                        pass
            evaluation["plan_protection"] = protection
            if lease_owner is not None:
                blockers = sorted(set([*blockers, "execution_lease_busy"]))
            evaluation["ready_zone_ids"] = list(ready_zone_ids)
            evaluation["deferred_zone_blockers"] = deferred
            evaluation["preflight_blockers"] = blockers
            evaluation["preflight_decision"] = (
                PreflightDecision.WAIT.value
                if lease_owner is not None and readiness_decision is PreflightDecision.PASS
                else readiness_decision.value
            )
            evaluation["eligible"] = bool(
                ready_zone_ids
                and not blockers
                and lease_owner is None
                and (protection is None or bool(protection.get("allowed")))
            )
            evaluations.append(evaluation)

        selected_id = select_force_candidate(evaluations)
        force_ready_by_job: dict[str, tuple[str, ...]] = {}
        for item in evaluations:
            item["selected"] = bool(selected_id and item.get("job_id") == selected_id)
            if bool(item.get("eligible")):
                force_ready_by_job[str(item["job_id"])] = tuple(
                    str(zone_id) for zone_id in item.get("ready_zone_ids", ())
                )
        self._force_status = {
            "stage": "LIVE_EXECUTION",
            "physical_early_start_enabled": self.execution_mode is ExecutionMode.REAL,
            "early_execution_enabled": True,
            "protection_policy": {
                "minimum_real_samples": 3,
                "source": "time_forecast_model",
                "hidden_safety_buffer_seconds": 0,
                "unknown_duration_fallback_seconds": 0,
            },
            "selected_candidate_job_id": selected_id,
            "execution_selected_job_id": None,
            "evaluations": evaluations,
        }
        future = [item for item in next_transitions if instant_gt(item, now)]
        self._force_next_transition_at = min(future, key=as_utc) if future else None
        return force_ready_by_job

    def _next_future_planned_for_force(
        self, current_job: JobInstance, now: datetime
    ) -> dict[str, Any] | None:
        """Return the nearest future planned occurrence not already consumed early.

        The current Force occurrence itself is excluded; its successor is still
        a real future plan and therefore participates in protection.  A future
        occurrence already completed/started early is skipped because it no
        longer represents future robot demand at its original planned time.
        """
        planner = OccurrencePlanner(self.hass.config.time_zone)
        candidates: list[dict[str, Any]] = []
        dry_generation = (
            self._dry_run_timeline_generation
            if self.execution_mode is ExecutionMode.DRY_RUN
            else None
        )
        for schedule in self._schedules():
            cursor = now
            for _ in range(64):
                occurrence = planner.next_for_schedule(schedule, cursor, inclusive=False)
                if occurrence is None:
                    break
                if occurrence.occurrence_id == current_job.occurrence_id:
                    cursor = occurrence.planned_start
                    continue
                owner = self.store.by_occurrence_id(
                    occurrence.occurrence_id,
                    self.execution_mode,
                    dry_run_timeline_generation=dry_generation,
                )
                if owner is not None:
                    if owner.terminal:
                        cursor = occurrence.planned_start
                        continue
                    # A progressive occurrence may already have completed an
                    # early batch yet still contain PLANNED/WAIT zones that are
                    # genuine demand at its original planned time. Preserve it
                    # as a future protected plan in that case.
                    if not self._job_has_unstarted_zone(owner) and (
                        bool(owner.execution_attempts)
                        or owner.actual_start is not None
                        or isinstance(owner.metadata.get("force_execution"), dict)
                    ):
                        cursor = occurrence.planned_start
                        continue
                candidates.append(
                    {
                        "planned_start": occurrence.planned_start,
                        "occurrence_id": occurrence.occurrence_id,
                        "schedule_id": occurrence.schedule_id,
                        "schedule_name": occurrence.schedule_name,
                        "job_id": owner.job_id if owner is not None else None,
                    }
                )
                break
        if not candidates:
            return None
        return min(candidates, key=lambda item: as_utc(item["planned_start"]))

    def _force_plan_protection(
        self,
        job: JobInstance,
        ready_zone_ids: tuple[str, ...],
        now: datetime,
    ) -> dict[str, Any]:
        """Apply the configured time Forecast Model to future-plan Force safety."""
        future = self._next_future_planned_for_force(job, now)
        estimate = self.statistics.forecast_estimate(
            "time", zone_ids=ready_zone_ids, cleaning_params=job.cleaning_params,
            minimum_samples=3, now=now,
            pending_terminal_jobs=tuple(self.store.terminal_archive),
        )
        result = evaluate_force_plan_protection(
            now=now,
            next_planned_start=future["planned_start"] if future else None,
            duration_estimate=estimate,
        )
        if future:
            result.update(
                {
                    "next_planned_occurrence_id": future.get("occurrence_id"),
                    "next_planned_job_id": future.get("job_id"),
                    "next_planned_schedule_id": future.get("schedule_id"),
                    "next_planned_schedule_name": future.get("schedule_name"),
                }
            )
        return result

    def _set_execution_lease_wait(
        self, job: JobInstance, zone_ids: tuple[str, ...], now: datetime
    ) -> bool:
        """Mark runnable zones as waiting for the single physical execution lease."""
        changed = False
        for zone_id in zone_ids:
            zone_run = job.zone_runs.get(zone_id)
            if zone_run is None or zone_run.terminal:
                continue
            if zone_run.blockers != ("execution_lease_busy",):
                zone_run.blockers = ("execution_lease_busy",)
                changed = True
            if zone_run.state is not ZoneJobState.WAIT:
                zone_run.transition(ZoneJobState.WAIT, now)
                changed = True
        if self._sync_job_state_from_zones(job, now):
            changed = True
        if changed and not job.terminal:
            job.blockers = tuple(
                sorted({blocker for zone in job.zone_runs.values() for blocker in zone.blockers})
            )
            job.current_blockers = job.blockers
            job.current_preflight_decision = PreflightDecision.WAIT.value
            self._emit_job(job, "execution_lease_wait")
        return changed

    async def _arbitrate_jobs(
        self,
        ready_by_job: dict[str, tuple[str, ...]],
        force_ready_by_job: dict[str, tuple[str, ...]],
        now: datetime,
    ) -> bool:
        """Select at most one runnable Manual/Scheduled/Forced Job.

        Force candidates enter only after read-only condition+pre-flight checks.
        Losing Force candidates remain PLANNED and never become WAIT merely
        because another runnable Job won the single execution lease.
        """
        candidates: list[ArbiterCandidate] = []
        for job_id, zone_ids in ready_by_job.items():
            job = self.store.active.get(job_id)
            if job is None or job.terminal or not zone_ids:
                continue
            force_execution = job.metadata.get("force_execution")
            if (
                isinstance(force_execution, dict)
                and bool(force_execution.get("committed", True))
                and instant_lt(now, job.planned_start)
                and job.origin is JobOrigin.SCHEDULED
                and job.manual_release_at is None
            ):
                candidates.append(
                    ArbiterCandidate.from_job(
                        job,
                        zone_ids,
                        candidate_class="forced",
                        force_priority=int(force_execution.get("priority", 0) or 0),
                        preempts_scheduled=bool(force_execution.get("preempts_scheduled", False)),
                    )
                )
            else:
                candidates.append(ArbiterCandidate.from_job(job, zone_ids))

        force_by_id = {
            str(item.get("job_id")): item
            for item in self._force_status.get("evaluations", [])
            if isinstance(item, dict)
        }
        for job_id, zone_ids in force_ready_by_job.items():
            # A Job can never be both due-Scheduled and Forced in one cycle; the
            # guard keeps the priority contract explicit if future code changes.
            if job_id in ready_by_job:
                continue
            job = self.store.active.get(job_id)
            evaluation = force_by_id.get(str(job_id), {})
            if job is None or job.terminal or not zone_ids or not bool(evaluation.get("eligible")):
                continue
            candidates.append(
                ArbiterCandidate.from_job(
                    job,
                    zone_ids,
                    candidate_class="forced",
                    force_priority=int(evaluation.get("priority", 0) or 0),
                    preempts_scheduled=bool(evaluation.get("preempts_scheduled", False)),
                )
            )

        lease_owner = self.execution.lease_owner(list(self.store.active.values()))
        ordered = self.arbiter.ordered(candidates)
        selected = self.arbiter.select(ordered, lease_owner=lease_owner)
        selected_force_id = (
            selected.job_id
            if selected is not None and selected.candidate_class == "forced"
            else None
        )
        for evaluation in self._force_status.get("evaluations", []):
            if isinstance(evaluation, dict):
                evaluation["selected"] = bool(
                    selected_force_id and str(evaluation.get("job_id")) == selected_force_id
                )
        self._force_status["selected_candidate_job_id"] = selected_force_id

        self._last_arbiter_status = {
            "at": now.isoformat(),
            "lease_owner_job_id": lease_owner[0] if lease_owner else None,
            "lease_owner_attempt_id": lease_owner[1] if lease_owner else None,
            "runnable_job_ids": [candidate.job_id for candidate in ordered],
            "runnable_candidates": [
                {
                    "job_id": candidate.job_id,
                    "class": candidate.candidate_class,
                    "force_priority": candidate.force_priority if candidate.candidate_class == "forced" else None,
                    "preempts_scheduled": candidate.preempts_scheduled if candidate.candidate_class == "forced" else False,
                }
                for candidate in ordered
            ],
            "selected_job_id": selected.job_id if selected else None,
            "selected_class": selected.candidate_class if selected else None,
            "policy": [
                "manual",
                "preemptive_force_by_priority_when_scheduled_due",
                "scheduled",
                "normal_force_by_priority",
            ],
        }
        changed = False
        if selected is None:
            if lease_owner is not None:
                for candidate in candidates:
                    # A force probe never becomes WAIT just because the lease is
                    # busy. It will be re-evaluated on the next scheduler cycle.
                    if candidate.candidate_class == "forced":
                        continue
                    job = self.store.active.get(candidate.job_id)
                    if job is not None:
                        changed |= self._set_execution_lease_wait(
                            job, candidate.ready_zone_ids, now
                        )
            return changed

        selected_job = self.store.active.get(selected.job_id)
        if selected_job is None or selected_job.terminal:
            return changed
        selected_zones = [
            selected_job.zone_runs[zone_id]
            for zone_id in selected.ready_zone_ids
            if zone_id in selected_job.zone_runs
            and not selected_job.zone_runs[zone_id].terminal
        ]
        if selected_zones:
            is_force = selected.candidate_class == "forced"
            force_execution: dict[str, Any] | None = None
            attempts_before = set(selected_job.execution_attempts)
            if is_force:
                evaluation = force_by_id.get(selected.job_id, {})
                existing_force = selected_job.metadata.get("force_execution")
                if not isinstance(existing_force, dict):
                    existing_force = {}
                advance_seconds = max(
                    0, int(instant_delta(selected_job.planned_start, now).total_seconds())
                )
                force_execution = {
                    "committed": True,
                    "selected_at": existing_force.get("selected_at") or now.isoformat(),
                    "planned_start": existing_force.get("planned_start") or selected_job.planned_start.isoformat(),
                    "window_start": evaluation.get("window_start", existing_force.get("window_start")),
                    "max_advance_minutes": int(
                        evaluation.get(
                            "max_advance_minutes",
                            existing_force.get("max_advance_minutes", 0),
                        )
                        or 0
                    ),
                    "priority": int(
                        evaluation.get("priority", existing_force.get("priority", selected.force_priority))
                        or 0
                    ),
                    "preempts_scheduled": bool(
                        evaluation.get(
                            "preempts_scheduled",
                            existing_force.get("preempts_scheduled", selected.preempts_scheduled),
                        )
                    ),
                    "advance_seconds_at_selection": int(
                        existing_force.get("advance_seconds_at_selection", advance_seconds)
                        or advance_seconds
                    ),
                    "ready_zone_ids": list(selected.ready_zone_ids),
                    "condition_evaluation": dict(
                        evaluation.get(
                            "condition_evaluation",
                            existing_force.get("condition_evaluation", {}),
                        )
                        or {}
                    ),
                    "plan_protection": dict(
                        evaluation.get(
                            "plan_protection",
                            existing_force.get("plan_protection", {}),
                        )
                        or {}
                    ),
                    "execution_mode": selected_job.execution_mode.value,
                }
                # The real backend persists COMMAND_INTENT before a device command
                # can be emitted. Storing this marker before entering the backend
                # means the same barrier also durably identifies the command as
                # an early execution of this exact occurrence.
                selected_job.metadata["force_execution"] = force_execution
                evaluation["execution_selected"] = True
                self._force_status["execution_selected_job_id"] = selected_job.job_id
                self._emit_job(selected_job, "force_execution_selected")

            changed |= await self.execution.async_start_ready_zones(
                selected_job, selected_zones, now, lease_available=True
            )
            attempts_after = set(selected_job.execution_attempts)
            created_attempts = sorted(attempts_after - attempts_before)

            if is_force and force_execution is not None:
                if created_attempts:
                    force_execution["attempt_ids"] = created_attempts
                    force_execution["start_requested_at"] = now.isoformat()
                    selected_job.metadata["force_execution"] = force_execution
                    self.force_conditions.reset_job(selected_job.job_id)
                    self._emit_job(selected_job, "force_execution_started")
                else:
                    # A failed build/probe before an ExecutionAttempt exists is
                    # not an execution and must not consume or WAIT the occurrence.
                    selected_job.metadata.pop("force_execution", None)
                    evaluation["execution_selected"] = False
                    evaluation["start_not_committed"] = True
                    self._force_status["execution_selected_job_id"] = None

            selected_job.metadata["last_arbiter_selected_at"] = now.isoformat()
            selected_job.metadata["last_arbiter_class"] = selected.candidate_class
            if self._sync_job_state_from_zones(selected_job, now):
                changed = True
            self._emit_job(selected_job, "arbiter_selected")

        # Once one normal Job has started, other normal runnable Jobs WAIT for
        # the lease. Force losers stay PLANNED: a force probe never reserves the
        # robot and never creates a WAIT backlog.
        for candidate in candidates:
            if candidate.job_id == selected.job_id:
                continue
            if candidate.candidate_class == "forced":
                evaluation = force_by_id.get(candidate.job_id)
                if evaluation is not None:
                    evaluation["deferred_by_arbiter_job_id"] = selected.job_id
                    evaluation["deferred_by_arbiter_class"] = selected.candidate_class
                continue
            job = self.store.active.get(candidate.job_id)
            if job is not None:
                changed |= self._set_execution_lease_wait(job, candidate.ready_zone_ids, now)
        return changed

    def _rebuild_occurrence_candidate(
        self,
        planner: OccurrencePlanner,
        schedule: ScheduleDefinition,
        now: datetime,
        *,
        restore_early_executed: bool,
    ) -> tuple[Occurrence | None, JobInstance | None, bool]:
        """Return the canonical slot for an explicit rebuild from Schedule+now."""
        dry_generation = (
            self._dry_run_timeline_generation
            if self.execution_mode is ExecutionMode.DRY_RUN
            else None
        )

        def consumed_lookup(occurrence: Occurrence) -> JobInstance | None:
            # Explicit refresh follows the same canonical slot semantics as
            # normal reconciliation. occurrence_id is revision-aware, but an
            # early execution under revision N still consumes the same
            # schedule_id + planned_start slot under revision N+1.
            owner = self.store.by_occurrence_id(
                occurrence.occurrence_id,
                self.execution_mode,
                dry_run_timeline_generation=dry_generation,
            )
            if owner is not None:
                return owner
            return self.store.by_schedule_slot(
                occurrence.schedule_id,
                occurrence.planned_start,
                self.execution_mode,
                dry_run_timeline_generation=dry_generation,
            )

        return select_schedule_refresh_occurrence(
            planner,
            schedule,
            now,
            consumed_lookup=consumed_lookup,
            restore_early_executed=restore_early_executed,
        )

    async def async_rebuild_schedule_projection(
        self, *, restore_early_executed: bool = False
    ) -> dict[str, Any]:
        """Recreate unstarted scheduled Jobs from current schedule definitions.

        This is an explicit operator repair/refresh action, not a lifecycle
        transition.  Unstarted scheduled projections are disposable cache and
        are removed together with their trace; no terminal Job, execution history
        or statistics row is created.  Started attempts and ad-hoc manual Jobs
        are preserved.

        Historical terminal consumption is not used as a cursor for this explicit
        rebuild. A still-future slot is rebuilt from Schedule+now even if an older
        skip/failure/expiry record exists. Future slots already completed through
        FORCE/early execution remain consumed by default; with
        ``restore_early_executed`` enabled they are deliberately re-armed in a
        separate replay generation so both physical executions remain auditable.
        """
        if not self._started:
            return {
                "now": self.clock.now(),
                "removed_jobs": 0,
                "created_jobs": 0,
                "restored_early_jobs": 0,
            }

        async with self._lock:
            if self.execution_mode is ExecutionMode.REAL and self.clock.offset.total_seconds():
                self.clock.reset()
            now = self.clock.now()
            planner = OccurrencePlanner(self.hass.config.time_zone)
            schedules = self._schedules()
            removed_jobs = 0
            created_jobs = 0
            restored_early_jobs = 0

            # Rebuild only the schedule projection.  A manual/additional Job is
            # independent of this projection, while STARTING/RUNNING or any Job
            # with an ExecutionAttempt has crossed the physical-execution safety
            # boundary and must never be replaced by a UI refresh action.
            for job in list(self.store.active.values()):
                if job.origin is not JobOrigin.SCHEDULED:
                    continue
                if job.execution_attempts or job.state in (JobState.STARTING, JobState.RUNNING):
                    continue
                self.dependency_index.remove_job(job.job_id)
                if self.store.discard_active_projection(job.job_id) is not None:
                    removed_jobs += 1

            mode = self.execution_mode
            dry_generation = (
                self._dry_run_timeline_generation
                if mode is ExecutionMode.DRY_RUN
                else None
            )
            for schedule in schedules:
                if not schedule.enabled:
                    continue
                occurrence, rearmed_from, restored_early = self._rebuild_occurrence_candidate(
                    planner,
                    schedule,
                    now,
                    restore_early_executed=bool(restore_early_executed),
                )
                if occurrence is None:
                    continue

                job = JobInstance.from_occurrence(occurrence, now)
                job.execution_mode = mode
                job.simulation = mode is ExecutionMode.DRY_RUN
                job.metadata["notification_policy_snapshot"] = dict(schedule.notification_policy)
                if mode is ExecutionMode.DRY_RUN:
                    job.metadata["dry_run_timeline_generation"] = self._dry_run_timeline_generation
                if rearmed_from is not None:
                    job.metadata["occurrence_rearm_generation"] = (
                        self.store.next_occurrence_rearm_generation(
                            occurrence.occurrence_id,
                            mode,
                            dry_run_timeline_generation=dry_generation,
                        )
                    )
                    job.metadata["rearmed_by_schedule_refresh"] = True
                    job.metadata["rearmed_from_job_id"] = rearmed_from.job_id
                    if restored_early:
                        restored_early_jobs += 1

                self.store.add_active(job)
                self._emit_job(job, "created")
                created_jobs += 1

            self._capture_debug_state()
            await self.store.async_save()

        # Run the ordinary reconciliation once so current gates, pause state and
        # pre-flight display are immediately reflected on the recreated Jobs.
        await self.async_reconcile()
        return {
            "now": self.clock.now(),
            "removed_jobs": removed_jobs,
            "created_jobs": created_jobs,
            "restored_early_jobs": restored_early_jobs,
        }

    async def async_reconcile_current_timeline(
        self, *, reset_test_clock: bool = False
    ) -> datetime:
        """Reconcile stale placeholders without deleting valid independent WAITs.

        0.10.2 changes the REAL recovery invariant: a past scheduled Job remains
        legitimate until its own deadline, even when a later occurrence exists.
        Explicit timeline recovery therefore preserves every unstarted Job whose
        schedule revision/execution namespace is still valid and only rebuilds
        missing future placeholders. Dry-run replay generation semantics remain
        intentionally stronger and may discard unstarted simulated Jobs.
        """
        if not self._started:
            return self.clock.now()
        if reset_test_clock and self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")

        async with self._lock:
            if self.execution_mode is ExecutionMode.REAL and self.clock.offset.total_seconds():
                self.clock.reset()
            generation_changed = False
            if reset_test_clock:
                self.clock.reset()
            if self.execution_mode is ExecutionMode.DRY_RUN:
                self._dry_run_timeline_generation += 1
                generation_changed = True

            now = self.clock.now()
            planner = OccurrencePlanner(self.hass.config.time_zone)
            schedules = self._schedules()
            by_id = {item.schedule_id: item for item in schedules}
            changed = False

            for job in list(self.store.active.values()):
                started_attempt = bool(job.execution_attempts) or job.state in (
                    JobState.STARTING, JobState.RUNNING
                )
                if started_attempt and not (
                    reset_test_clock and job.execution_mode is ExecutionMode.DRY_RUN
                ):
                    continue

                if job.origin is JobOrigin.MANUAL:
                    if reset_test_clock and job.execution_mode is ExecutionMode.DRY_RUN:
                        self.store.append_event(
                            job,
                            "timeline_reconciled",
                            now,
                            details={
                                "reason": "dry_run_time_reset_manual_job_removed",
                                "statistics_eligible": False,
                            },
                        )
                        self.dependency_index.remove_job(job.job_id)
                        self.store.remove_active(job.job_id)
                        changed = True
                    continue

                schedule = by_id.get(job.schedule_id)
                valid = (
                    schedule is not None
                    and schedule.enabled
                    and schedule.revision == job.schedule_revision
                    and job.execution_mode is self.execution_mode
                    and (
                        job.execution_mode is not ExecutionMode.DRY_RUN
                        or int(job.metadata.get("dry_run_timeline_generation", 0) or 0)
                        == self._dry_run_timeline_generation
                    )
                )
                if valid:
                    continue

                self.store.append_event(
                    job,
                    "timeline_reconciled",
                    now,
                    details={
                        "reason": "stale_unstarted_job_dematerialized",
                        "statistics_eligible": False,
                    },
                )
                self.dependency_index.remove_job(job.job_id)
                self.store.remove_active(job.job_id)
                changed = True

            changed |= self._dematerialize_shadowed_schedule_slots(now)
            changed |= self._materialize_missing(planner, schedules, now)
            self._capture_debug_state()
            if changed or reset_test_clock or generation_changed:
                await self.store.async_save()

        await self.async_reconcile()
        return self.clock.now()

    def _scheduler_owns_real_execution(self) -> bool:
        """Return whether a non-terminal REAL attempt currently owns the robot."""
        return any(
            attempt.execution_mode is ExecutionMode.REAL and not attempt.terminal
            for job in self.store.active.values()
            for attempt in job.execution_attempts.values()
        )

    async def _async_observe_external_execution(self, now: datetime) -> bool:
        """Materialize a completed physical session that Scheduler did not start."""
        job = await self.external_execution.async_observe(
            now, scheduler_owned=self._scheduler_owns_real_execution()
        )
        if job is None:
            return False
        external = dict(job.metadata.get("external_execution") or {})
        self.store.append_event(
            job,
            "external_execution_detected",
            job.actual_start or now,
            details={
                "session_id": external.get("session_id"),
                "task_kind": external.get("task_kind"),
                "targets_resolved": external.get("targets_resolved", False),
                "water_observation_complete": external.get("water_observation_complete", False),
            },
        )
        self.store.append_event(
            job,
            "finished",
            job.finished_at or now,
            details={"source": "external", "statistics_eligible": True},
        )
        self.store.archive(job)
        return True

    async def async_reconcile(self) -> None:
        """Reconcile persisted jobs against clock, schedule revisions and timers."""
        if not self._started:
            return
        async with self._lock:
            now = self.clock.now()
            planner = OccurrencePlanner(self.hass.config.time_zone)
            schedules = self._schedules()
            by_id = {item.schedule_id: item for item in schedules}
            changed = self._dematerialize_stale_future_jobs(by_id, now)
            changed |= self._dematerialize_shadowed_schedule_slots(now)
            changed |= self._continue_offline_backfill(planner, schedules, now)
            changed |= self._materialize_missing(planner, schedules, now)
            # External physical cleaning is observed before pre-flight/arbitration.
            # It never acquires Scheduler's lease; the live vacuum state itself
            # keeps planned work in normal robot-busy WAIT semantics.
            changed |= await self._async_observe_external_execution(now)

            # Prepare every Job independently before the arbiter sees any of
            # them. This is the key 0.10.2 ordering barrier: an early WAIT can
            # never prevent a later runnable Job from being considered.
            ready_by_job: dict[str, tuple[str, ...]] = {}
            for job in list(self.store.active.values()):
                job_changed, ready_zone_ids = await self._prepare_job(job, now)
                changed |= job_changed
                if ready_zone_ids and not job.terminal:
                    ready_by_job[job.job_id] = ready_zone_ids

            # Force evaluation is read-only and happens before arbitration so
            # eligible future occurrences can compete as the lowest-priority
            # runnable class in the same deterministic decision cycle.
            force_ready_by_job = self._evaluate_force_candidates(now)
            changed |= await self._arbitrate_jobs(ready_by_job, force_ready_by_job, now)

            # Finishing a recovered execution can reveal additional occurrences
            # that were fully missed while HA was offline. Record those before
            # normal future materialization can skip over them.
            changed |= self._continue_offline_backfill(planner, schedules, now)
            changed |= self._dematerialize_shadowed_schedule_slots(now)
            # Finishing a job can make the next occurrence materializable.
            changed |= self._materialize_missing(planner, schedules, now)

            if changed:
                self._capture_debug_state()
                await self.store.async_save()
            await self.statistics.async_sync_terminal_jobs(
                self.store.terminal_archive, self.store.events_for_job
            )
            notification_jobs = [
                *self.store.active.values(),
                *(job for job in self.store.history[-3:] if job.origin is not JobOrigin.EXTERNAL),
            ]
        for notification_job in notification_jobs:
            await self.notifications.async_process_job(notification_job, now)
        self._arm_job_timers()
        self._emit_scheduler()

    async def async_recheck_jobs(self, job_ids: set[str]) -> None:
        """Re-evaluate dependency changes through the global arbiter.

        A local recheck cannot safely start a Job because another independent
        Job may have higher Manual/Scheduled priority. Dependency events therefore
        trigger one normal reconciliation of the complete runnable set.
        """
        if not self._started or not job_ids:
            return
        for job_id in job_ids:
            if job_id not in self.store.active:
                self.dependency_index.remove_job(job_id)
        await self.async_reconcile()

    async def async_settings_changed(self) -> None:
        """Re-evaluate immediately after policy/binding/zone/execution changes.

        Execution mode is intentionally *not* copied into existing jobs here.
        Mode changes have their own explicit regeneration transaction so an
        already-started or partially completed job can never change backend as
        a side effect of an unrelated settings refresh.
        """
        self.input_provider.reset_zone_stability()
        self.execution.set_dry_run_duration(
            int(self.dry_run_settings.get("execution_duration_seconds", 10))
        )
        self.execution.set_restore_previous_settings(
            bool(self.real_execution_settings.get("restore_previous_settings", True))
        )
        self.execution.set_runtime_error_recovery_minutes(
            int(self.real_execution_settings.get(
                "runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES
            ))
        )
        if self.execution_mode is ExecutionMode.REAL and self.clock.offset.total_seconds():
            self.clock.reset()
        self._subscribe_zone_inputs()
        # Force dependencies belong to the editable Schedule definition. A
        # changed person/entity set must replace the previous listener before
        # the following reconcile evaluates the new condition snapshot.
        self._subscribe_force_inputs()
        await self.async_reconcile()

    async def async_real_execution_readiness(self) -> dict[str, Any]:
        """Validate static prerequisites for enabling physical execution.

        Runtime blockers such as occupancy, DND, low battery, or a manually
        busy robot remain normal pre-flight WAIT conditions. This check covers
        prerequisites whose failure would make a physical command unsafe or
        structurally impossible.
        """
        now = self.clock.now()
        adapter = self.execution.real.adapter
        observation = adapter.observe(now)
        issues: list[dict[str, Any]] = []

        def issue(code: str, *, severity: str = "error", detail: Any = None, **extra: Any) -> None:
            row: dict[str, Any] = {"code": code, "severity": severity}
            if detail not in (None, ""):
                row["detail"] = detail
            row.update(extra)
            issues.append(row)

        vacuum_entity_id = str(adapter.executor.vacuum_entity_id or "")
        vacuum_state = self.hass.states.get(vacuum_entity_id) if vacuum_entity_id else None
        if not vacuum_entity_id or vacuum_state is None:
            issue("vacuum_entity_missing")

        schedules = [item for item in self._schedules() if item.enabled]
        configured = {zone.zone_id: zone for zone in self.input_provider.cleaning_zones}
        used_zones: dict[str, Any] = {}
        for schedule in schedules:
            for zone_id in schedule.targets:
                zone = configured.get(str(zone_id))
                if zone is None:
                    issue("cleaning_zone_missing", detail=zone_id, schedule_id=schedule.schedule_id)
                else:
                    used_zones[zone.zone_id] = zone

        segment_zones = [
            zone for zone in used_zones.values() if zone.robot_target_type.value == "segment"
        ]
        segment_targets = {zone.robot_target_id for zone in segment_zones}
        if segment_targets:
            loaded = adapter._loaded_vacuum_entity()
            if (
                loaded is None
                or not callable(getattr(loaded, "async_get_segments", None))
                or not callable(getattr(loaded, "async_clean_segments", None))
            ):
                issue("segment_cleaning_not_supported", detail=adapter.vendor)
            for zone in segment_zones:
                valid, problem = self.input_provider.target_configuration_valid(zone)
                if not valid:
                    issue(
                        "robot_target_missing" if problem == "segment_not_found" else str(problem or "robot_target_missing"),
                        detail=zone.robot_target_id,
                        target_type="segment",
                    )
            if adapter.vendor == "roborock" and observation.current_map is not None:
                for target in sorted(segment_targets):
                    if "_" in target and target.split("_", 1)[0] != str(observation.current_map):
                        # Multi-floor configurations are valid; current-map
                        # mismatch is a runtime WAIT, not a reason to forbid REAL.
                        issue(
                            "target_map_not_active", severity="warning", detail=target,
                            current_map=str(observation.current_map),
                        )

        zone_targets = [
            zone.robot_target_id
            for zone in used_zones.values()
            if zone.robot_target_type.value == "zone"
        ]
        if zone_targets and not adapter.coordinate_zone_supported:
            issue("zone_cleaning_vendor_not_supported", detail=adapter.vendor)

        # Validate explicit schedule parameter entities/options. Empty values are
        # intentionally "do not override" and require no physical capability.
        for schedule in schedules:
            params = canonicalize_effective_cleaning_params(schedule.cleaning_params)
            for key in ("cleaning_mode", "cleaning_route", "mop_mode", "water_mode"):
                value = params.get(key)
                entity_id = str(params.get(f"{key}_entity_id", "") or "").strip()
                if value in (None, ""):
                    continue
                if not entity_id:
                    issue(
                        "execution_parameter_entity_missing",
                        detail=key, schedule_id=schedule.schedule_id, value=value,
                    )
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or str(state.state) in {"unknown", "unavailable"}:
                    issue(
                        "execution_parameter_unavailable", detail=entity_id,
                        schedule_id=schedule.schedule_id, value=value,
                    )
                    continue
                domain = entity_id.split(".", 1)[0]
                if domain not in {"select", "input_select", "number", "input_number"}:
                    issue("execution_parameter_unsupported", detail=entity_id, schedule_id=schedule.schedule_id)
                    continue
                options = state.attributes.get("options")
                if isinstance(options, (list, tuple)) and str(value) not in {str(item) for item in options}:
                    issue(
                        "execution_parameter_invalid", detail=f"{entity_id}:{value}",
                        schedule_id=schedule.schedule_id,
                    )

            fan_mode = str(params.get("fan_mode", "") or "").strip()
            if fan_mode:
                vacuum_state = self.hass.states.get(str(adapter.executor.vacuum_entity_id or ""))
                options = vacuum_state.attributes.get("fan_speed_list") if vacuum_state is not None else None
                if isinstance(options, (list, tuple)) and fan_mode not in {str(item) for item in options}:
                    issue("execution_parameter_invalid", detail=f"fan:{fan_mode}", schedule_id=schedule.schedule_id)

        if not schedules:
            issue("no_enabled_schedules", severity="warning")
        if self.clock.offset.total_seconds():
            issue("test_clock_will_reset", severity="warning", detail=int(self.clock.offset.total_seconds()))

        return {
            "ready": not any(item["severity"] == "error" for item in issues),
            "vendor": adapter.vendor,
            "checked_at": now.isoformat(),
            "issues": issues,
        }

    def _real_runtime_preview_job(self, now: datetime) -> JobInstance | None:
        """Return the nearest not-yet-started Job projected into REAL mode."""
        candidates = [
            job
            for job in self.store.active.values()
            if not job.terminal
            and not job.execution_attempts
            and job.state in (JobState.PLANNED, JobState.WAIT)
        ]
        if candidates:
            source = min(candidates, key=lambda item: item.planned_start)
            preview = JobInstance.from_dict(source.to_dict())
            preview.execution_mode = ExecutionMode.REAL
            preview.simulation = False
            return preview

        planner = OccurrencePlanner(self.hass.config.time_zone)
        occurrences: list[tuple[datetime, Occurrence]] = []
        for schedule in self._schedules():
            if not schedule.enabled:
                continue
            occurrence = planner.next_for_schedule(schedule, now, inclusive=True)
            if occurrence is not None:
                occurrences.append((occurrence.planned_start, occurrence))
        if not occurrences:
            return None
        occurrence = min(occurrences, key=lambda item: item[0])[1]
        preview = JobInstance.from_occurrence(occurrence, now)
        preview.execution_mode = ExecutionMode.REAL
        preview.simulation = False
        return preview

    async def async_real_execution_runtime_status(self) -> dict[str, Any]:
        """Return current start blockers using the authoritative pre-flight engine.

        This is deliberately separate from structural REAL readiness. Runtime
        conditions may prevent a start *now* but never prevent the user from
        selecting REAL as the global execution backend.
        """
        now = self.clock.now()
        preview = self._real_runtime_preview_job(now)
        if preview is None:
            return {
                "available": False,
                "can_start_now": None,
                "checked_at": now.isoformat(),
                "blockers": [],
            }
        report = self.preflight.evaluate(preview, PreflightPhase.CURRENT_PREVIEW, now)
        return {
            "available": True,
            "can_start_now": report.decision is PreflightDecision.PASS,
            "checked_at": now.isoformat(),
            "job_id": preview.job_id,
            "schedule_name": preview.schedule_name,
            "decision": report.decision.value,
            "blockers": [item.to_dict() for item in report.blockers],
        }

    @staticmethod
    def _job_safe_for_mode_regeneration(job: JobInstance) -> bool:
        """Return whether a job can be discarded and rebuilt without replay risk."""
        if job.terminal or job.execution_attempts:
            return False
        if job.state not in (JobState.PLANNED, JobState.WAIT):
            return False
        # A finished/starting/running zone means this occurrence has already
        # crossed a semantic or physical execution boundary. Keep it pinned to
        # its original backend until completion.
        return all(
            (not zone.terminal) and zone.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)
            for zone in job.zone_runs.values()
        )

    def execution_mode_change_preview(self, mode: str | ExecutionMode) -> dict[str, Any]:
        """Describe exactly what an execution-mode switch can safely rebuild."""
        try:
            selected = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
        except ValueError as err:
            raise ValueError("invalid_execution_mode") from err
        current = self.execution_mode
        regenerable = [job for job in self.store.active.values() if self._job_safe_for_mode_regeneration(job)]
        preserved = [job for job in self.store.active.values() if job not in regenerable]
        return {
            "current_mode": current.value,
            "target_mode": selected.value,
            "changed": selected is not current,
            "regenerable_jobs": len(regenerable),
            "preserved_jobs": len(preserved),
            "preserved_running_jobs": sum(
                job.state in (JobState.STARTING, JobState.RUNNING) or bool(job.execution_attempts)
                for job in preserved
            ),
            "may_start_immediately": any(
                instant_ge(self.clock.now(), job.effective_start) and instant_lt(self.clock.now(), job.deadline_at)
                for job in regenerable
            ),
        }

    def _regenerated_job_for_mode(
        self, job: JobInstance, selected: ExecutionMode, now: datetime
    ) -> JobInstance:
        """Re-materialize one untouched occurrence while preserving its identity.

        Keeping ``job_id`` preserves notification de-duplication and deep links.
        Runtime/pre-flight state is rebuilt from scratch; an occupancy override
        is deliberately not carried into another execution mode.
        """
        raw = job.to_dict()
        metadata = dict(job.metadata)
        for key in (
            "current_preflight_signature", "current_preflight_report",
            "current_preflight_next_recheck_at", "current_zone_preflight_reports",
            "manual_overrides", "execution_attempt_id", "execution_substate",
            "last_observation", "start_observation", "final_guard_observation",
            "target_validation",
        ):
            metadata.pop(key, None)
        if selected is ExecutionMode.DRY_RUN:
            metadata["dry_run_timeline_generation"] = self._dry_run_timeline_generation
        else:
            metadata.pop("dry_run_timeline_generation", None)
        metadata["execution_mode_regenerated_at"] = now.isoformat()
        metadata["execution_mode_regenerated_from"] = job.execution_mode.value
        raw.update({
            "created_at": now.isoformat(),
            "state": JobState.PLANNED.value,
            "result": None,
            "reason_code": None,
            "blockers": [],
            "current_blockers": [],
            "current_preflight_decision": None,
            # Preserve the immutable advisory snapshot so a mode switch cannot
            # emit the same prewarning twice for the same occurrence.
            "first_wait_at": None,
            "wait_cycle": 0,
            "last_state_change_at": now.isoformat(),
            "starting_at": None,
            "actual_start": None,
            "finished_at": None,
            "execution_mode": selected.value,
            "simulation": selected is ExecutionMode.DRY_RUN,
            "transition_generation": int(job.transition_generation) + 1,
            "zone_runs": {
                str(zone_id): ZoneExecution(zone_id=str(zone_id)).to_dict()
                for zone_id in job.targets
            },
            "execution_attempts": {},
            "metadata": metadata,
        })
        return JobInstance.from_dict(raw)

    async def _apply_execution_mode_change(
        self, previous: ExecutionMode, selected: ExecutionMode
    ) -> dict[str, Any]:
        """Regenerate only untouched jobs after the global mode was persisted."""
        summary = {
            "current_mode": previous.value, "target_mode": selected.value,
            "changed": selected is not previous, "regenerated_jobs": 0,
            "preserved_jobs": 0, "dropped_consumed_jobs": 0,
        }
        if selected is previous:
            return summary

        if selected is ExecutionMode.REAL:
            self.clock.reset()
            self.execution.inject_fault_once(None)
        else:
            # A fresh Dry-Run namespace lets the same future occurrence be
            # simulated again without erasing previous simulation audit data.
            self._dry_run_timeline_generation += 1

        now = self.clock.now()
        async with self._lock:
            candidates = list(self.store.active.values())
            for job in candidates:
                if not self._job_safe_for_mode_regeneration(job):
                    summary["preserved_jobs"] += 1
                    continue
                self.dependency_index.remove_job(job.job_id)
                self.store.append_event(
                    job, "execution_mode_regeneration", now,
                    details={
                        "from": job.execution_mode.value, "to": selected.value,
                        "statistics_eligible": False,
                    },
                )
                self.store.remove_active(job.job_id)

                generation = self._dry_run_timeline_generation if selected is ExecutionMode.DRY_RUN else None
                if self.store.by_occurrence_id(
                    job.occurrence_id, selected,
                    dry_run_timeline_generation=generation,
                ) is not None:
                    # The occurrence already exists in the target execution
                    # namespace (most importantly: an already completed REAL
                    # occurrence). Do not replay it; normal materialization will
                    # select the next valid occurrence.
                    summary["dropped_consumed_jobs"] += 1
                    continue
                replacement = self._regenerated_job_for_mode(job, selected, now)
                self.store.add_active(replacement)
                self._emit_job(replacement, "regenerated")
                summary["regenerated_jobs"] += 1

            self._capture_debug_state()
            await self.store.async_save()

        self.input_provider.reset_zone_stability()
        self.execution.set_dry_run_duration(
            int(self.dry_run_settings.get("execution_duration_seconds", 10))
        )
        self.execution.set_restore_previous_settings(
            bool(self.real_execution_settings.get("restore_previous_settings", True))
        )
        self.execution.set_runtime_error_recovery_minutes(
            int(self.real_execution_settings.get(
                "runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES
            ))
        )
        self._subscribe_zone_inputs()
        await self.async_reconcile()
        return summary

    async def async_set_execution_mode(self, mode: str | ExecutionMode) -> dict[str, Any]:
        """Persist mode immediately and safely regenerate untouched jobs."""
        try:
            selected = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
        except ValueError as err:
            raise ValueError("invalid_execution_mode") from err
        previous = self.execution_mode
        if selected is ExecutionMode.REAL:
            readiness = await self.async_real_execution_readiness()
            if not readiness.get("ready"):
                codes = ",".join(
                    str(item.get("code", "unknown"))
                    for item in readiness.get("issues", [])
                    if item.get("severity") == "error"
                )
                raise ValueError(f"real_execution_not_ready:{codes}")
        if selected is previous:
            return {**self.execution_mode_change_preview(selected), "regenerated_jobs": 0, "dropped_consumed_jobs": 0}
        options = dict(self.entry.options)
        options[CONF_EXECUTION_MODE] = selected.value
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        return await self._apply_execution_mode_change(previous, selected)

    async def async_update_dry_run_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Update official dry-run behavior without changing scheduler semantics."""
        current = self.dry_run_settings
        current.update(dict(patch))
        try:
            duration = int(current.get("execution_duration_seconds", 10))
        except (TypeError, ValueError) as err:
            raise ValueError("invalid_dry_run_duration") from err
        if not 1 <= duration <= 300:
            raise ValueError("invalid_dry_run_duration")
        current["execution_duration_seconds"] = duration
        options = dict(self.entry.options)
        options[CONF_DRY_RUN_SETTINGS] = current
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return current

    async def async_update_real_execution_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Update safety-oriented REAL execution behavior."""
        current = self.real_execution_settings
        current.update(dict(patch))
        current["restore_previous_settings"] = bool(
            current.get("restore_previous_settings", True)
        )
        try:
            recovery_minutes = int(
                current.get("runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES)
            )
        except (TypeError, ValueError) as err:
            raise ValueError("invalid_runtime_error_recovery_minutes") from err
        if not 1 <= recovery_minutes <= 1440:
            raise ValueError("invalid_runtime_error_recovery_minutes")
        current["runtime_error_recovery_minutes"] = recovery_minutes
        options = dict(self.entry.options)
        options[CONF_REAL_EXECUTION_SETTINGS] = current
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return current

    async def async_update_execution_settings(
        self,
        *,
        mode: str | ExecutionMode,
        dry_run: dict[str, Any],
        real: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist the execution-settings form as one snapshot.

        Validation is completed before the config entry is touched, so a bad
        value cannot leave execution mode, dry-run behavior, and REAL safety
        options partially updated.
        """
        try:
            selected = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
        except ValueError as err:
            raise ValueError("invalid_execution_mode") from err

        dry_settings = self.dry_run_settings
        dry_settings.update(dict(dry_run))
        try:
            duration = int(dry_settings.get("execution_duration_seconds", 10))
        except (TypeError, ValueError) as err:
            raise ValueError("invalid_dry_run_duration") from err
        if not 1 <= duration <= 300:
            raise ValueError("invalid_dry_run_duration")
        dry_settings["execution_duration_seconds"] = duration

        real_settings = self.real_execution_settings
        real_settings.update(dict(real))
        real_settings["restore_previous_settings"] = bool(
            real_settings.get("restore_previous_settings", True)
        )
        try:
            recovery_minutes = int(
                real_settings.get("runtime_error_recovery_minutes", DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES)
            )
        except (TypeError, ValueError) as err:
            raise ValueError("invalid_runtime_error_recovery_minutes") from err
        if not 1 <= recovery_minutes <= 1440:
            raise ValueError("invalid_runtime_error_recovery_minutes")
        real_settings["runtime_error_recovery_minutes"] = recovery_minutes

        if selected is ExecutionMode.REAL:
            readiness = await self.async_real_execution_readiness()
            if not readiness.get("ready"):
                codes = ",".join(
                    str(item.get("code", "unknown"))
                    for item in readiness.get("issues", [])
                    if item.get("severity") == "error"
                )
                raise ValueError(f"real_execution_not_ready:{codes}")

        previous = self.execution_mode
        options = dict(self.entry.options)
        options[CONF_EXECUTION_MODE] = selected.value
        options[CONF_DRY_RUN_SETTINGS] = dry_settings
        options[CONF_REAL_EXECUTION_SETTINGS] = real_settings
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        if selected is previous:
            await self.async_settings_changed()
            mode_change = {"changed": False, "regenerated_jobs": 0, "preserved_jobs": 0, "dropped_consumed_jobs": 0}
        else:
            mode_change = await self._apply_execution_mode_change(previous, selected)
        return {
            "mode": selected.value,
            "dry_run": dry_settings,
            "real": real_settings,
            "mode_change": mode_change,
        }

    def _schedule_by_id(self, schedule_id: str) -> ScheduleDefinition:
        schedule = next((item for item in self._schedules() if item.schedule_id == schedule_id), None)
        if schedule is None:
            raise ValueError("schedule_not_found")
        return schedule

    def _scheduler_local_date(self, value: datetime):
        """Return one scheduler instant as a Home Assistant local calendar date."""
        planner = OccurrencePlanner(self.hass.config.time_zone)
        return value.astimezone(planner.zone).date()

    def _pending_scheduled_job_for_schedule(
        self,
        schedule_id: str,
        now: datetime,
        *,
        local_date=None,
        future_only: bool = False,
    ) -> JobInstance | None:
        """Return the nearest not-yet-started scheduled Job matching the request.

        Schedule-level Home Assistant actions intentionally resolve to a
        *materialized* Job instead of creating a replacement occurrence. This
        preserves the occurrence's immutable effective-parameter snapshot when
        it is moved earlier.
        """
        planner = OccurrencePlanner(self.hass.config.time_zone)
        candidates: list[JobInstance] = []
        for job in self.store.active.values():
            if (
                job.terminal
                or job.origin is not JobOrigin.SCHEDULED
                or job.schedule_id != schedule_id
                or job.state not in (JobState.PLANNED, JobState.WAIT)
                or instant_ge(now, job.deadline_at)
            ):
                continue
            if future_only and not instant_gt(job.planned_start, now):
                continue
            if local_date is not None and job.planned_start.astimezone(planner.zone).date() != local_date:
                continue
            candidates.append(job)
        if not candidates:
            return None
        return min(candidates, key=lambda item: as_utc(item.planned_start))

    async def async_set_schedule_enabled(
        self, schedule_id: str, enabled: bool, *, source: str = "frontend", user_id: str | None = None
    ) -> ScheduleDefinition:
        """Enable/disable one schedule through the shared scheduler action layer."""
        schedules = self._schedules()
        updated: list[ScheduleDefinition] = []
        selected: ScheduleDefinition | None = None
        for schedule in schedules:
            if schedule.schedule_id == schedule_id:
                schedule = schedule.revised(enabled=bool(enabled))
                selected = schedule
            updated.append(schedule)
        if selected is None:
            raise ValueError("schedule_not_found")
        options = dict(self.entry.options)
        options[CONF_SCHEDULES] = [item.to_dict() for item in updated]
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return selected

    async def async_set_schedule_paused(
        self, schedule_id: str, paused: bool, *, source: str = "frontend", user_id: str | None = None
    ) -> ScheduleDefinition:
        """Pause/resume execution for one enabled schedule without disabling planning."""
        schedules = self._schedules()
        updated: list[ScheduleDefinition] = []
        selected: ScheduleDefinition | None = None
        for schedule in schedules:
            if schedule.schedule_id == schedule_id:
                if not schedule.enabled and paused:
                    raise ValueError("schedule_disabled")
                schedule = schedule.revised(paused=bool(paused))
                selected = schedule
            updated.append(schedule)
        if selected is None:
            raise ValueError("schedule_not_found")
        options = dict(self.entry.options)
        options[CONF_SCHEDULES] = [item.to_dict() for item in updated]
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return selected

    @staticmethod
    def _validated_disabled_until(mode: ExecutionGateMode, value: str | None) -> str | None:
        if mode is not ExecutionGateMode.DISABLED_UNTIL:
            return None
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("disabled_until_required")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as err:
            raise ValueError("invalid_disabled_until") from err
        return parsed.isoformat()

    async def async_set_execution_gate(
        self, mode: str | ExecutionGateMode, disabled_until: str | None = None,
        *, source: str = "frontend", user_id: str | None = None,
    ) -> PreflightPolicy:
        """Change the global execution gate through the shared scheduler action layer."""
        try:
            gate = mode if isinstance(mode, ExecutionGateMode) else ExecutionGateMode(str(mode))
        except ValueError as err:
            raise ValueError("invalid_execution_gate") from err
        until = self._validated_disabled_until(gate, disabled_until)
        raw = self.input_provider.policy.to_dict()
        raw["execution_gate"] = gate.value
        raw["disabled_until"] = until
        policy = PreflightPolicy.from_dict(raw)
        options = dict(self.entry.options)
        options[CONF_PREFLIGHT_POLICY] = policy.to_dict()
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return policy

    async def async_update_preflight_policy(
        self, patch: dict[str, Any], *, source: str = "frontend", user_id: str | None = None
    ) -> PreflightPolicy:
        """Update global pre-flight policy, validating gate semantics in one place."""
        current = self.input_provider.policy.to_dict()
        current.update(dict(patch))
        try:
            gate = ExecutionGateMode(str(current.get("execution_gate", ExecutionGateMode.ENABLED.value)))
        except ValueError as err:
            raise ValueError("invalid_execution_gate") from err
        current["disabled_until"] = self._validated_disabled_until(gate, current.get("disabled_until"))
        policy = PreflightPolicy.from_dict(current)
        options = dict(self.entry.options)
        options[CONF_PREFLIGHT_POLICY] = policy.to_dict()
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_settings_changed()
        return policy

    def _record_user_action(
        self,
        job: JobInstance,
        action: str,
        now: datetime,
        source: str,
        user_id: str | None = None,
        *,
        recipient_id: str | None = None,
        actor_name: str | None = None,
    ) -> None:
        """Persist UI audit and append the same action to durable history."""
        job.record_user_action(
            action,
            now,
            source,
            user_id,
            recipient_id=recipient_id,
            actor_name=actor_name,
        )
        self.store.append_event(
            job,
            "user_action",
            now,
            details={
                "action": str(action),
                "source": str(source),
                "user_id": user_id,
                "recipient_id": recipient_id,
                "actor_name": actor_name,
            },
        )

    async def async_run_schedule_now(
        self, schedule_id: str, *, source: str = "frontend", user_id: str | None = None
    ) -> JobInstance:
        """Create an ad-hoc manual Job using the profile effective *today*.

        A separate manual run is not a moved scheduled occurrence. Therefore
        weekday corrections are resolved for the local calendar date on which
        the user actually requests the run, never from the already-materialized
        future Job shown in the UI.
        """
        schedule = self._schedule_by_id(schedule_id)
        if schedule.paused:
            raise ValueError("schedule_paused")
        now = self.clock.now()
        local_date = self._scheduler_local_date(now)
        effective_params = schedule.effective_cleaning_params_for_date(local_date)
        occurrence = Occurrence(
            occurrence_id=f"manual:{schedule.schedule_id}:{uuid4().hex}",
            schedule_id=schedule.schedule_id,
            schedule_revision=schedule.revision,
            schedule_name=schedule.name,
            planned_start=now,
            warning_at=now,
            deadline_at=instant_add(now, timedelta(minutes=schedule.execution_window_minutes)),
            next_planned_start=None,
            target_type=schedule.target_type,
            targets=tuple(schedule.targets),
            cleaning_params=dict(effective_params),
            zone_execution_policy=schedule.zone_execution_policy,
        )
        job = JobInstance.from_occurrence(occurrence, now)
        job.execution_mode = self.execution_mode
        job.simulation = job.execution_mode is ExecutionMode.DRY_RUN
        job.origin = JobOrigin.MANUAL
        job.manual_triggered_at = now
        job.requested_by = user_id
        job.metadata["manual_profile_date"] = local_date.isoformat()
        job.metadata["effective_profile_snapshot"] = dict(effective_params)
        job.metadata["notification_policy_snapshot"] = dict(schedule.notification_policy)
        async with self._lock:
            self.store.add_active(job)
            self._emit_job(job, "created")
            self._record_user_action(job, "run_schedule_now", now, source, user_id)
            await self.store.async_save()
        await self.async_reconcile()
        return job

    async def async_run_job_additional(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None
    ) -> JobInstance:
        """Create a separate MANUAL run for the selected Job's schedule.

        The selected Job is used only to identify the schedule. The new run
        resolves the schedule's current definition and today's weekday profile;
        it deliberately does *not* copy a future occurrence's profile.
        """
        async with self._lock:
            template = self.store.active.get(job_id)
            if template is None or template.terminal:
                raise ValueError("job_not_found")
            now = self.clock.now()
            schedule = self._schedule_by_id(template.schedule_id)
            if schedule.paused:
                raise ValueError("schedule_paused")
            local_date = self._scheduler_local_date(now)
            effective_params = schedule.effective_cleaning_params_for_date(local_date)
            occurrence = Occurrence(
                occurrence_id=f"manual:{template.schedule_id}:{uuid4().hex}",
                schedule_id=template.schedule_id,
                schedule_revision=schedule.revision,
                schedule_name=schedule.name,
                planned_start=now,
                warning_at=now,
                deadline_at=instant_add(now, timedelta(minutes=schedule.execution_window_minutes)),
                next_planned_start=None,
                target_type=schedule.target_type,
                targets=tuple(schedule.targets),
                cleaning_params=dict(effective_params),
                zone_execution_policy=schedule.zone_execution_policy,
            )
            job = JobInstance.from_occurrence(occurrence, now)
            job.execution_mode = self.execution_mode
            job.simulation = job.execution_mode is ExecutionMode.DRY_RUN
            job.origin = JobOrigin.MANUAL
            job.manual_triggered_at = now
            job.requested_by = user_id
            job.metadata["additional_from_job_id"] = template.job_id
            job.metadata["manual_profile_date"] = local_date.isoformat()
            job.metadata["effective_profile_snapshot"] = dict(effective_params)
            job.metadata["notification_policy_snapshot"] = dict(schedule.notification_policy)
            self.store.add_active(job)
            self._emit_job(job, "created")
            self._record_user_action(job, "run_job_additional", now, source, user_id)
            await self.store.async_save()
        await self.async_reconcile()
        return job

    async def async_run_schedule_early(
        self,
        schedule_id: str,
        *,
        source: str = "ha_action",
        user_id: str | None = None,
        ignore_busy_zones: bool = False,
    ) -> JobInstance:
        """Move the nearest future scheduled occurrence earlier and execute it.

        Crucially, this resolves to the existing materialized Job and therefore
        keeps the cleaning-parameter snapshot of the occurrence's *original*
        planned local date. No weekday correction is recalculated for today.
        """
        schedule = self._schedule_by_id(schedule_id)
        if schedule.paused:
            raise ValueError("schedule_paused")
        now = self.clock.now()
        job = self._pending_scheduled_job_for_schedule(
            schedule_id,
            now,
            future_only=True,
        )
        if job is None:
            raise ValueError("future_scheduled_job_not_found")
        return await self.async_start_job_now(
            job.job_id,
            source=source,
            user_id=user_id,
            ignore_busy_zones=ignore_busy_zones,
        )

    async def async_smart_run_schedule(
        self,
        schedule_id: str,
        *,
        source: str = "ha_action",
        user_id: str | None = None,
        ignore_busy_zones: bool = False,
    ) -> dict[str, Any]:
        """Run today's pending occurrence if it exists, otherwise add a new run.

        This is the automation-oriented action: if a not-yet-started scheduled
        Job for the current HA-local date still exists, it is released exactly
        like the UI's manual early execution and keeps its original occurrence
        profile. If today's occurrence has already started/finished/been
        consumed (or there was no occurrence today), a fully separate MANUAL
        run is created with today's effective weekday profile.
        """
        schedule = self._schedule_by_id(schedule_id)
        if schedule.paused:
            raise ValueError("schedule_paused")
        now = self.clock.now()
        today = self._scheduler_local_date(now)
        pending = self._pending_scheduled_job_for_schedule(
            schedule_id,
            now,
            local_date=today,
        )
        if pending is not None:
            job = await self.async_start_job_now(
                pending.job_id,
                source=source,
                user_id=user_id,
                ignore_busy_zones=ignore_busy_zones,
            )
            async with self._lock:
                job.metadata["smart_run_resolution"] = "scheduled_occurrence"
                job.metadata["smart_run_requested_at"] = now.isoformat()
                await self.store.async_save()
            return {"resolution": "scheduled_occurrence", "job": job}

        job = await self.async_run_schedule_now(
            schedule_id,
            source=source,
            user_id=user_id,
        )
        async with self._lock:
            job.metadata["smart_run_resolution"] = "additional_run"
            job.metadata["smart_run_requested_at"] = now.isoformat()
            await self.store.async_save()
        return {"resolution": "additional_run", "job": job}

    async def async_start_job_now(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None,
        recipient_id: str | None = None, actor_name: str | None = None,
        ignore_busy_zones: bool = False,
    ) -> JobInstance:
        """Release one materialized occurrence, optionally accepting occupancy.

        ``ignore_busy_zones`` is deliberately narrow: the server re-evaluates
        every unstarted zone and records only zones that are *currently* blocked
        by ``zone_busy``. No client-supplied zone IDs are trusted, and every
        other authoritative blocker remains in force. The accepted zone IDs are
        persisted in Job metadata so a crash/restart cannot lose the user's
        explicit one-occurrence decision.
        """
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.state not in (JobState.PLANNED, JobState.WAIT):
                raise ValueError("job_cannot_start_now")
            now = self.clock.now()
            if instant_ge(now, job.deadline_at):
                raise ValueError("job_deadline_expired")
            schedule = self._schedule_by_id(job.schedule_id)
            if schedule.paused:
                raise ValueError("schedule_paused")

            accepted_busy_zone_ids: list[str] = []
            if ignore_busy_zones:
                self._sync_zone_runs(job)
                for zone_id, zone_run in job.zone_runs.items():
                    if zone_run.terminal or zone_run.state in (ZoneJobState.STARTING, ZoneJobState.RUNNING):
                        continue
                    report = self.preflight.evaluate_zone(
                        job, zone_id, PreflightPhase.AUTHORITATIVE, now
                    )
                    if "zone_busy" in report.blocker_codes:
                        accepted_busy_zone_ids.append(zone_id)
                if accepted_busy_zone_ids:
                    overrides = job.metadata.get("manual_overrides")
                    if not isinstance(overrides, dict):
                        overrides = {}
                    existing = {str(item) for item in overrides.get("ignore_busy_zones", ())}
                    existing.update(accepted_busy_zone_ids)
                    overrides["ignore_busy_zones"] = sorted(existing)
                    overrides["occupancy_accepted_at"] = now.isoformat()
                    job.metadata["manual_overrides"] = overrides
                    self.store.append_event(
                        job,
                        "manual_occupancy_override",
                        now,
                        details={
                            "zone_ids": sorted(accepted_busy_zone_ids),
                            "source": source,
                            "user_id": user_id,
                        },
                    )

            # 0.12.6: starting a materialized scheduled occurrence early is no
            # longer a MANUAL release.  It is an explicit early execution of
            # this exact occurrence, so the occurrence is consumed only by the
            # dedicated "execute early" action.  Separate ad-hoc cleaning is
            # created through async_run_schedule_now() and never mutates this Job.
            if job.origin is JobOrigin.SCHEDULED and instant_lt(now, job.planned_start):
                job.metadata["force_execution"] = {
                    "committed": True,
                    "manual_request": True,
                    "selected_at": now.isoformat(),
                    "planned_start": job.planned_start.isoformat(),
                    "window_start": now.isoformat(),
                    "max_advance_minutes": max(0, int(instant_delta(job.planned_start, now).total_seconds() // 60)),
                    "priority": int((job.metadata.get("force_snapshot") or {}).get("priority", 0) or 0),
                    "preempts_scheduled": bool((job.metadata.get("force_snapshot") or {}).get("preempts_scheduled", False)),
                    "advance_seconds_at_selection": max(0, int(instant_delta(job.planned_start, now).total_seconds())),
                    "ready_zone_ids": list(job.zone_runs.keys()),
                    "condition_evaluation": {"manual_request": True},
                    "plan_protection": {"manual_request": True, "allowed": True},
                    "execution_mode": job.execution_mode.value,
                }
                self._emit_job(job, "force_execution_selected_manual")
            self._record_user_action(
                job,
                "start_job_now_ignore_busy" if accepted_busy_zone_ids else "start_job_now",
                now, source, user_id, recipient_id=recipient_id, actor_name=actor_name,
            )
            await self.store.async_save()
        await self.async_reconcile()
        return job

    async def async_set_job_occupancy_override(
        self,
        job_id: str,
        *,
        ignore: bool,
        source: str = "frontend",
        user_id: str | None = None,
        recipient_id: str | None = None,
        actor_name: str | None = None,
    ) -> JobInstance:
        """Accept or restore occupancy checks for one materialized occurrence.

        This is intentionally different from ``async_start_job_now``: changing
        the occupancy decision must not force, advance or otherwise release the
        occurrence.  It only removes/restores the ``zone_busy`` blocker for the
        current Job, after which the normal scheduler/arbiter decides when the
        robot may actually start.
        """
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.state not in (JobState.PLANNED, JobState.WAIT):
                raise ValueError("job_occupancy_override_unavailable")

            now = self.clock.now()
            self._sync_zone_runs(job)
            raw_overrides = job.metadata.get("manual_overrides")
            overrides = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}

            if ignore:
                accepted = {
                    str(item)
                    for item in overrides.get("ignore_busy_zones", ())
                    if str(item)
                }
                newly_accepted: list[str] = []
                for zone_id, zone_run in job.zone_runs.items():
                    if zone_run.terminal or zone_run.state in (
                        ZoneJobState.STARTING,
                        ZoneJobState.RUNNING,
                    ):
                        continue
                    report = self.preflight.evaluate_zone(
                        job, zone_id, PreflightPhase.AUTHORITATIVE, now
                    )
                    if "zone_busy" in report.blocker_codes and zone_id not in accepted:
                        accepted.add(zone_id)
                        newly_accepted.append(zone_id)

                if accepted:
                    overrides["ignore_busy_zones"] = sorted(accepted)
                    overrides["occupancy_accepted_at"] = now.isoformat()
                    job.metadata["manual_overrides"] = overrides
                if newly_accepted:
                    self.store.append_event(
                        job,
                        "manual_occupancy_override",
                        now,
                        details={
                            "zone_ids": sorted(newly_accepted),
                            "source": source,
                            "user_id": user_id,
                        },
                    )
                action = "ignore_occupancy"
            else:
                cleared = sorted(
                    {
                        str(item)
                        for item in overrides.get("ignore_busy_zones", ())
                        if str(item)
                    }
                )
                overrides.pop("ignore_busy_zones", None)
                overrides.pop("occupancy_accepted_at", None)
                if overrides:
                    job.metadata["manual_overrides"] = overrides
                else:
                    job.metadata.pop("manual_overrides", None)
                if cleared:
                    self.store.append_event(
                        job,
                        "manual_occupancy_override_cleared",
                        now,
                        details={
                            "zone_ids": cleared,
                            "source": source,
                            "user_id": user_id,
                        },
                    )
                action = "respect_occupancy"

            self._record_user_action(
                job,
                action,
                now,
                source,
                user_id,
                recipient_id=recipient_id,
                actor_name=actor_name,
            )
            await self.store.async_save()

        await self.async_recheck_jobs({job_id})
        return job

    async def async_skip_job(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None,
        recipient_id: str | None = None, actor_name: str | None = None,
    ) -> JobInstance:
        """Finish a not-yet-running occurrence as a user-requested failure."""
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.state not in (JobState.PLANNED, JobState.WAIT):
                raise ValueError("job_cannot_skip")
            now = self.clock.now()
            self._record_user_action(
                job, "skip", now, source, user_id,
                recipient_id=recipient_id, actor_name=actor_name,
            )
            self._sync_zone_runs(job)
            self._finish_remaining_zones(job, JobReason.USER_SKIPPED.value, now)
            self._aggregate_job_if_complete(job, now)
            await self.store.async_save()
        await self.notifications.async_process_job(job, now)
        await self.async_reconcile()
        return job

    async def async_cancel_job(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None,
        recipient_id: str | None = None, actor_name: str | None = None,
    ) -> JobInstance:
        """Request cancellation and wait for physical confirmation when REAL."""
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.state not in (JobState.STARTING, JobState.RUNNING):
                raise ValueError("job_cannot_cancel")
            now = self.clock.now()
            self._record_user_action(
                job, "cancel", now, source, user_id,
                recipient_id=recipient_id, actor_name=actor_name,
            )
            await self.execution.async_cancel_job(job, now)
            # User cancel applies to the whole occurrence. Work that never
            # started can be closed immediately, but a REAL running target is
            # not marked cancelled until the observer confirms it stopped.
            self._finish_unstarted_zones(job, JobReason.USER_CANCELLED.value, now)
            self._aggregate_job_if_complete(job, now)
            await self.store.async_save()
        await self.notifications.async_process_job(job, now)
        await self.async_reconcile()
        return job

    async def async_pause_job(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None
    ) -> JobInstance:
        """Pause the currently owned REAL execution attempt."""
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.execution_mode is not ExecutionMode.REAL or job.state is not JobState.RUNNING:
                raise ValueError("job_cannot_pause")
            now = self.clock.now()
            if not await self.execution.async_pause_job(job, now):
                raise ValueError("job_cannot_pause")
            self._record_user_action(job, "pause", now, source, user_id)
            await self.store.async_save()
        await self.async_reconcile()
        return job

    async def async_resume_job(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None
    ) -> JobInstance:
        """Resume a scheduler-owned REAL attempt only when it is observed PAUSED."""
        async with self._lock:
            job = self.store.active.get(job_id)
            if job is None or job.terminal:
                raise ValueError("job_not_found")
            if job.execution_mode is not ExecutionMode.REAL or job.state is not JobState.RUNNING:
                raise ValueError("job_cannot_resume")
            now = self.clock.now()
            try:
                if not await self.execution.async_resume_job(job, now):
                    raise ValueError("job_cannot_resume")
            except Exception as err:
                if isinstance(err, ValueError):
                    raise
                raise ValueError("job_cannot_resume") from err
            self._record_user_action(job, "resume", now, source, user_id)
            await self.store.async_save()
        await self.async_reconcile()
        return job

    async def async_recheck_job(
        self, job_id: str, *, source: str = "frontend", user_id: str | None = None,
        recipient_id: str | None = None, actor_name: str | None = None,
    ) -> JobInstance:
        """Run current pre-flight again without resetting stabilization timers."""
        job = self.store.active.get(job_id)
        if job is None or job.terminal:
            raise ValueError("job_not_found")
        now = self.clock.now()
        self._record_user_action(
            job, "recheck", now, source, user_id,
            recipient_id=recipient_id, actor_name=actor_name,
        )
        await self.store.async_save()
        await self.async_recheck_jobs({job_id})
        return job

    async def async_set_override(
        self, target: str, mode: str, value: Any = None, *, persistent: bool = False
    ) -> dict[str, Any] | None:
        """Set one normalized-input override; never effective in REAL mode."""
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        mode_value = OverrideMode(str(mode).upper())
        frozen = self.input_provider.get_live_value(target) if mode_value is OverrideMode.FREEZE else None
        item = self.overrides.set(
            target,
            mode_value,
            value=value,
            persistent=persistent,
            frozen_live_value=frozen,
        )
        await self.async_reconcile()
        await self._async_save_debug_state()
        return item.to_dict() if item else None

    async def async_clear_overrides(self, target: str | None = None) -> None:
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        self.overrides.clear(target)
        await self.async_reconcile()
        await self._async_save_debug_state()

    async def async_apply_override_preset(self, preset: str) -> None:
        """Apply one safe testing preset without changing any Home Assistant entity."""
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        preset = str(preset)
        mapping: dict[str, tuple[str, Any]] = {
            "battery_low": ("vacuum.battery_percent", 5),
            "vacuum_busy": ("vacuum.activity", "cleaning"),
            "clean_water_empty": ("dock.clean_water", False),
            "dirty_water_full": ("dock.dirty_water", False),
            "detergent_empty": ("dock.detergent", False),
            "vacuum_unavailable": ("vacuum.available", False),
        }
        if preset == "clear_all":
            await self.async_clear_overrides()
            return
        if preset == "dnd_active":
            now = self.clock.now()
            start = (now - timedelta(hours=1)).timetz().replace(tzinfo=None).isoformat(timespec="seconds")
            end = (now + timedelta(hours=1)).timetz().replace(tzinfo=None).isoformat(timespec="seconds")
            self.overrides.set("dnd.active", OverrideMode.FORCE, value=True, persistent=False)
            self.overrides.set("dnd.starts_at", OverrideMode.FORCE, value=start, persistent=False)
            self.overrides.set("dnd.ends_at", OverrideMode.FORCE, value=end, persistent=False)
            await self.async_reconcile()
            await self._async_save_debug_state()
            return
        if preset in {"room_busy", "path_blocked", "zone_busy"}:
            zones = self.input_provider.cleaning_zones
            if not zones:
                raise ValueError("no_cleaning_zones")
            suffix = "busy" if preset in {"room_busy", "zone_busy"} else "accessible"
            value = True if suffix == "busy" else False
            target = f"zone.{zones[0].zone_id}.{suffix}"
        elif preset in mapping:
            target, value = mapping[preset]
        else:
            raise ValueError("unknown_override_preset")
        self.overrides.set(target, OverrideMode.FORCE, value=value, persistent=False)
        await self.async_reconcile()
        await self._async_save_debug_state()

    async def async_advance_time(self, seconds: int) -> datetime:
        """Advance the dry-run test clock and process transitions now due."""
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        self.clock.advance(timedelta(seconds=int(seconds)))
        await self.async_reconcile()
        await self._async_save_debug_state()
        return self.clock.now()

    async def async_reset_time(self) -> datetime:
        """Reset Test Clock and start a fresh Dry-run timeline at real HA time."""
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        now = await self.async_reconcile_current_timeline(reset_test_clock=True)
        await self._async_save_debug_state()
        return now

    async def async_reset_dry_run(self) -> datetime:
        """Reset only DRY_RUN jobs/history and testing environment."""
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        async with self._lock:
            self._clear_timers()
            dry_job_ids = self.store.clear_dry_run_data()
            self.notifications.store.clear_simulation_jobs(dry_job_ids)
            self.overrides.clear()
            self.dependency_index.clear()
            self.clock.reset()
            self.execution.inject_fault_once(None)
            self._capture_debug_state()
            await self.store.async_save()
            await self.notifications.store.async_save()
        await self.async_reconcile()
        return self.clock.now()

    async def async_reset_simulation(self) -> datetime:
        """Compatibility alias for the pre-0.7 debug endpoint."""
        return await self.async_reset_dry_run()

    async def async_set_execution_fault(self, fault: str | None) -> None:
        if self.execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("dry_run_required")
        self.execution.inject_fault_once(fault)
        self._emit_scheduler()

    def next_transition_at(self) -> datetime | None:
        candidates: list[datetime] = []
        now = self.clock.now()
        for job in self.store.active.values():
            if job.state is JobState.PLANNED:
                candidates.extend([job.warning_at, job.effective_start])
            if self._job_has_unstarted_zone(job):
                candidates.append(job.deadline_at)
                if job.next_planned_start:
                    candidates.append(job.next_planned_start)
            if value := self._metadata_dt(job, "current_preflight_next_recheck_at"):
                candidates.append(value)
            if value := self.notifications.next_due_at(job):
                candidates.append(value)
            if value := self.execution.next_transition_at(job):
                candidates.append(value)
        if self._force_next_transition_at is not None:
            candidates.append(self._force_next_transition_at)
        planner = OccurrencePlanner(self.hass.config.time_zone)
        for schedule in self._schedules():
            if not schedule.enabled or self.store.active_for_schedule(schedule.schedule_id):
                continue
            occurrence = self._candidate_occurrence(planner, schedule, now)
            if occurrence is not None:
                candidates.append(occurrence.warning_at)
        future = [item for item in candidates if instant_gt(item, now)]
        return min(future, key=as_utc) if future else None

    async def async_advance_to_next_transition(self) -> datetime:
        target = self.next_transition_at()
        if target is None:
            return self.clock.now()
        seconds = max(1, math.ceil(instant_delta(target, self.clock.now()).total_seconds()))
        return await self.async_advance_time(seconds)

    def notification_job_context_payload(self, job_id: str) -> dict[str, Any]:
        """Return compact current Job state and authoritative UI action options.

        Notification messages are immutable historical records, while this
        context is deliberately live.  The frontend must render only the
        returned actions; every action is still revalidated by the normal
        jobs/action command when pressed.
        """
        key = str(job_id)
        job = self.store.active.get(key)
        active = job is not None
        if job is None:
            job = next((item for item in reversed(self.store.history) if item.job_id == key), None)
        if job is None:
            job = next((item for item in reversed(self.store.terminal_archive) if item.job_id == key), None)
        if job is None:
            return {"available": False, "job_id": key, "available_actions": []}

        actions: list[dict[str, Any]] = []
        busy_zones: list[str] = []
        active_attempt = next(
            (attempt for attempt in reversed(list(job.execution_attempts.values())) if not attempt.terminal),
            None,
        )
        if active and not job.terminal:
            if job.state is JobState.PLANNED:
                actions = [{"action": "start_now"}, {"action": "skip"}]
            elif job.state is JobState.WAIT:
                busy_zones = [
                    run.zone_name or zone_id
                    for zone_id, run in job.zone_runs.items()
                    if not run.terminal and "zone_busy" in run.blockers
                ]
                actions = [{"action": "recheck"}]
                actions.append({
                    "action": "start_now_ignore_busy" if busy_zones else "start_now",
                    "busy_zones": list(busy_zones),
                })
                actions.append({"action": "skip"})
            elif job.state is JobState.STARTING:
                actions = [{"action": "cancel"}]
            elif job.state is JobState.RUNNING:
                if job.execution_mode is ExecutionMode.REAL and active_attempt is not None:
                    if active_attempt.state is ExecutionAttemptState.PAUSED:
                        actions.append({"action": "resume"})
                    else:
                        actions.append({"action": "pause"})
                actions.append({"action": "cancel"})

        zone_runs = [
            {
                "zone_id": zone_id,
                "zone_name": run.zone_name or zone_id,
                "state": run.state.value,
                "result": run.result.value if run.result is not None else None,
                "reason_code": run.reason_code,
                "blockers": list(run.blockers),
            }
            for zone_id, run in sorted(job.zone_runs.items())
        ]
        return {
            "available": True,
            "active": active and not job.terminal,
            "job_id": job.job_id,
            "schedule_name": job.schedule_name,
            "state": job.state.value,
            "result": job.result.value if job.result is not None else None,
            "reason_code": job.reason_code,
            "execution_mode": job.execution_mode.value,
            "planned_start": job.planned_start.isoformat(),
            "deadline_at": job.deadline_at.isoformat(),
            "actual_start": job.actual_start.isoformat() if job.actual_start else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "current_blockers": list(job.current_blockers),
            "zone_runs": zone_runs,
            "execution_state": active_attempt.state.value if active_attempt is not None else None,
            "available_actions": actions,
        }

    def _force_evaluation_for_job(self, job_id: str) -> dict[str, Any] | None:
        for item in self._force_status.get("evaluations", []):
            if str(item.get("job_id")) == str(job_id):
                return dict(item)
        return None

    def _force_status_for_job(self, job: JobInstance) -> dict[str, Any] | None:
        """Return live Force evaluation or durable executed-Force history."""
        live = self._force_evaluation_for_job(job.job_id)
        raw_execution = job.metadata.get("force_execution")
        execution = dict(raw_execution) if isinstance(raw_execution, dict) else None
        if live is None and execution is None:
            return None
        force = dict(live or {})
        if execution is not None:
            force.setdefault("job_id", job.job_id)
            force.setdefault("occurrence_id", job.occurrence_id)
            force.setdefault("schedule_id", job.schedule_id)
            force.setdefault("schedule_name", job.schedule_name)
            force.setdefault("planned_start", job.planned_start.isoformat())
            force.setdefault("window_start", execution.get("window_start"))
            force.setdefault("max_advance_minutes", execution.get("max_advance_minutes", 0))
            force.setdefault("priority", execution.get("priority", 0))
            force.setdefault("conditions_matched", True)
            force.setdefault("eligible", True)
            force["selected"] = True
            force["execution_selected"] = True
            force["execution_committed"] = bool(execution.get("committed", True))
            force["execution"] = execution
            force["window_state"] = "EXECUTED"
            force["actual_start"] = job.actual_start.isoformat() if job.actual_start else None
            reference = job.actual_start or self._metadata_dt(job, "force_selected_at")
            if reference is None:
                try:
                    reference = datetime.fromisoformat(str(execution.get("selected_at")))
                except (TypeError, ValueError):
                    reference = None
            if reference is not None:
                force["actual_advance_seconds"] = max(
                    0, int(instant_delta(job.planned_start, reference).total_seconds())
                )
        return force

    @staticmethod
    def _normalized_live_area(attempt: ExecutionAttempt) -> float | None:
        """Return a stable current-run area for live UI.

        Roborock can briefly expose the previous run's terminal area at the
        start of a new run and can reset the per-run counter to zero while the
        Scheduler still owns post-clean dock service.  The UI must therefore
        never render the raw counter directly.  We prefer the current-session
        segment and retain the highest confirmed value across a terminal reset.
        """
        raw_samples = attempt.metadata.get("statistics_observations")
        samples = raw_samples if isinstance(raw_samples, list) else []
        values: list[float] = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            try:
                value = float(sample.get("cleaning_area_m2"))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(value)
        if not values:
            return None

        # A near-zero first sample is strong evidence that this is already the
        # current session counter.  Keep its maximum so 3.5 -> 0 at dock cannot
        # make the visible area jump backwards.
        if values[0] <= 1.0:
            return max(values)

        # If the stream begins with a stale previous-run value, wait for the
        # first strong reset and use only the new session segment.
        reset_at = None
        for idx in range(1, len(values)):
            if values[idx] <= 1.0 and values[idx - 1] - values[idx] >= 1.0:
                reset_at = idx
                break
        if reset_at is not None:
            return max(values[reset_at:])

        # Until a new-session boundary is proven, suppress the suspicious
        # absolute value rather than showing a bogus area larger than the zone.
        return None

    @staticmethod
    def _live_interpolation_rates(attempt: ExecutionAttempt) -> dict[str, float | str | None]:
        """Estimate current-run floor progress rates for presentation-only interpolation.

        Rates are derived from real HA observations only.  The frontend may use
        them briefly between anchors; scheduler decisions and stored statistics
        never consume interpolated values.
        """
        raw_samples = attempt.metadata.get("statistics_observations")
        samples = raw_samples if isinstance(raw_samples, list) else []
        current: list[dict[str, Any]] = []
        started = False
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            phase = str(sample.get("phase") or "").upper()
            task_kind = str(sample.get("task_kind") or "").lower()
            if not started and (phase == "CLEANING" or task_kind in {"segment", "zone"}):
                started = True
            if started:
                current.append(sample)

        # These are intentionally conservative presentation ceilings.  Real
        # robot observations are never capped; only the one-second animation
        # between observations is affected.
        area_rate = robust_observed_rate(current, "cleaning_area_m2", maximum=0.05)
        percent_rate = robust_observed_rate(current, "clean_percent", maximum=0.25)

        return {
            "area_m2_per_second": area_rate.per_second if area_rate else None,
            "area_anchor_at": area_rate.anchor_at.isoformat() if area_rate else None,
            "percent_per_second": percent_rate.per_second if percent_rate else None,
            "percent_anchor_at": percent_rate.anchor_at.isoformat() if percent_rate else None,
        }

    def _live_execution_status(self, job: JobInstance, now: datetime) -> dict[str, Any] | None:
        """Return lightweight live progress for the active-jobs table.

        This is presentation data only.  It never mutates the Job, the Forecast
        Model or scheduler decisions.  Robot-reported progress is kept distinct
        from time-model progress so the frontend can label estimates honestly.
        """
        if job.state not in {JobState.STARTING, JobState.RUNNING}:
            return None
        attempts = list(job.execution_attempts.values())
        attempt = next((item for item in reversed(attempts) if not item.terminal), None)
        if attempt is None:
            return None
        raw_obs = attempt.metadata.get("last_observation") or attempt.metadata.get("start_observation") or {}
        observation = dict(raw_obs) if isinstance(raw_obs, dict) else {}
        started_at = job.actual_start or attempt.start_confirmed_at or attempt.requested_at or job.starting_at
        elapsed_seconds = None
        if started_at is not None:
            elapsed_seconds = max(0.0, instant_delta(now, started_at).total_seconds())

        zone_ids = tuple(job.zone_runs.keys()) or tuple(job.targets)
        estimate = self.statistics.forecast_estimate(
            "time",
            zone_ids=zone_ids,
            cleaning_params=job.cleaning_params,
            minimum_samples=3,
            now=now,
            pending_terminal_jobs=tuple(self.store.terminal_archive),
        )
        forecast_seconds = None
        eta_at = None
        estimated_progress_percent = None
        if bool(estimate.get("available")):
            try:
                forecast_seconds = max(0.0, float(estimate.get("forecast_value")))
            except (TypeError, ValueError):
                forecast_seconds = None
        if started_at is not None and forecast_seconds and forecast_seconds > 0:
            eta_at = instant_add(started_at, timedelta(seconds=forecast_seconds))
            if elapsed_seconds is not None:
                # Never display an estimated 100% while the Job is still active.
                estimated_progress_percent = min(95.0, max(0.0, elapsed_seconds / forecast_seconds * 100.0))

        # The active physical attempt is the authoritative scope of what the
        # Scheduler asked the robot to clean.  Do not rely on exactly one
        # ZoneExecution being RUNNING: one merged robot command legitimately
        # marks several logical Cleaning Zones RUNNING at the same time.
        current_zone_ids: list[str] = []
        for raw_zone_id in attempt.zone_ids:
            zone_id = str(raw_zone_id)
            if zone_id and zone_id not in current_zone_ids:
                current_zone_ids.append(zone_id)

        # Recovery/legacy fallback: reconstruct the attempt scope from the
        # durable binding written on ZoneExecution.
        if not current_zone_ids:
            for zone_id, zone_run in job.zone_runs.items():
                if str(zone_run.metadata.get("execution_attempt_id") or "") == attempt.attempt_id:
                    current_zone_ids.append(str(zone_id))

        # A very old snapshot may have neither field.  Use active logical zones
        # only then; finally a one-zone Job is inherently unambiguous.
        if not current_zone_ids:
            current_zone_ids = [
                str(zone_id)
                for zone_id, zone_run in job.zone_runs.items()
                if str(getattr(zone_run.state, "value", zone_run.state)) in {"STARTING", "RUNNING"}
            ]
        if not current_zone_ids and len(job.zone_runs) == 1:
            current_zone_ids = [str(next(iter(job.zone_runs)))]
        if not current_zone_ids and len(job.targets) == 1:
            current_zone_ids = [str(job.targets[0])]

        configured_zone_names = {
            str(zone.zone_id): str(zone.name)
            for zone in self.input_provider.cleaning_zones
        }
        current_zone_names: list[str] = []
        for zone_id in current_zone_ids:
            zone_run = job.zone_runs.get(zone_id)
            zone_name = (
                str(zone_run.zone_name).strip()
                if zone_run is not None and str(zone_run.zone_name).strip()
                else str(configured_zone_names.get(zone_id) or zone_id).strip()
            )
            if zone_name and zone_name not in current_zone_names:
                current_zone_names.append(zone_name)

        current_zone_id = current_zone_ids[0] if len(current_zone_ids) == 1 else None
        current_zone_name = current_zone_names[0] if len(current_zone_names) == 1 else None

        start_obs = attempt.metadata.get("start_observation")
        start_battery = None
        if isinstance(start_obs, dict):
            try:
                start_battery = float(start_obs.get("battery_percent"))
            except (TypeError, ValueError):
                start_battery = None
        current_battery = None
        try:
            current_battery = float(observation.get("battery_percent"))
        except (TypeError, ValueError):
            current_battery = None

        completion_check_due_at = None
        if attempt.state is ExecutionAttemptState.COMPLETION_PENDING:
            completion_check_due_at = self.execution.backend_for(
                attempt.execution_mode
            ).next_transition_at(attempt)

        return {
            "attempt_state": attempt.state.value,
            "attempt_id": attempt.attempt_id,
            "completion_check_due_at": (
                completion_check_due_at.isoformat() if completion_check_due_at else None
            ),
            "started_at": started_at.isoformat() if started_at else None,
            "elapsed_seconds": elapsed_seconds,
            "observation": observation,
            "robot_clean_percent": attempt.stable_clean_percent(),
            "cleaning_time_seconds": observation.get("cleaning_time_seconds"),
            "cleaning_area_m2": self._normalized_live_area(attempt),
            "interpolation": {
                "anchor_at": observation.get("observed_at"),
                **self._live_interpolation_rates(attempt),
                "max_age_seconds": 12,
                "max_percent_increment": 2.0,
                "max_area_increment_m2": 0.2,
            },
            "battery_percent": current_battery,
            "battery_start_percent": start_battery,
            "battery_consumed_percent": (
                max(0.0, start_battery - current_battery)
                if start_battery is not None and current_battery is not None
                else None
            ),
            "current_zone_id": current_zone_id,
            "current_zone_name": current_zone_name,
            "current_zone_ids": current_zone_ids,
            "current_zone_names": current_zone_names,
            "forecast": {
                "available": bool(estimate.get("available")),
                "enabled": bool(estimate.get("enabled")),
                "basis": estimate.get("basis"),
                "sample_count": estimate.get("sample_count"),
                "percentile": estimate.get("percentile"),
                "delta_percent": estimate.get("delta_percent"),
                "raw_percentile_seconds": estimate.get("raw_percentile_value"),
                "expected_total_seconds": forecast_seconds,
                "eta_at": eta_at.isoformat() if eta_at else None,
                "estimated_progress_percent": estimated_progress_percent,
            },
        }

    def _time_forecast_duration_seconds(self, job: JobInstance, now: datetime) -> float | None:
        """Return the enabled trained time forecast for one materialized Job."""
        zone_ids = tuple(job.zone_runs.keys()) or tuple(job.targets)
        estimate = self.statistics.forecast_estimate(
            "time",
            zone_ids=zone_ids,
            cleaning_params=job.cleaning_params,
            minimum_samples=3,
            now=now,
            pending_terminal_jobs=tuple(self.store.terminal_archive),
        )
        if not bool(estimate.get("enabled")) or not bool(estimate.get("available")):
            return None
        try:
            value = float(estimate.get("forecast_value"))
        except (TypeError, ValueError):
            return None
        return max(0.0, value)

    def _waited_before_start_status(self, job: JobInstance) -> dict[str, Any]:
        """Classify a completed Job with a meaningful pre-start WAIT episode."""
        try:
            waited = max(0.0, float(getattr(job, "max_prestart_wait_seconds", 0.0) or 0.0))
        except (TypeError, ValueError):
            waited = 0.0
        try:
            policy, _ = self.notifications.policy_for_job(job)
            threshold = max(0, int(policy.wait_delay_seconds))
        except Exception:
            threshold = 0
        return {
            "waited_before_start": job.waited_before_start(threshold),
            "prestart_wait_seconds": waited,
            "wait_status_threshold_seconds": threshold,
        }

    def _job_status_dict(self, job: JobInstance, now: datetime | None = None) -> dict[str, Any]:
        payload = job.to_dict()
        current_now = now or self.clock.now()
        payload["start_forecast"] = readiness(job, current_now)
        force = self._force_status_for_job(job)
        runnable_job_ids = tuple(
            str(item)
            for item in self._last_arbiter_status.get("runnable_job_ids", ()) or ()
        )
        lease_owner = self.execution.lease_owner(list(self.store.active.values()))
        if lease_owner is not None and lease_owner[0] != job.job_id:
            owner_job = self.store.active.get(lease_owner[0])
            owner_attempt = (
                owner_job.execution_attempts.get(lease_owner[1])
                if owner_job is not None
                else None
            )
            apply_execution_lease_overlay(
                payload,
                job_id=job.job_id,
                lease_owner=lease_owner,
                runnable_job_ids=runnable_job_ids,
                force_status=force,
                owner_schedule_name=owner_job.schedule_name if owner_job is not None else None,
                owner_attempt_state=owner_attempt.state.value if owner_attempt is not None else None,
            )
        try:
            schedule = self._schedule_by_id(job.schedule_id)
        except ValueError:
            schedule = None
        payload["schedule_enabled"] = bool(schedule.enabled) if schedule is not None else False
        payload["schedule_paused"] = bool(schedule.paused) if schedule is not None else False
        if force is not None:
            payload["force"] = force
        live = self._live_execution_status(job, current_now)
        if live is not None:
            payload["live"] = live
            payload["forecast_duration_seconds"] = (live.get("forecast") or {}).get("expected_total_seconds")
        else:
            payload["forecast_duration_seconds"] = self._time_forecast_duration_seconds(job, current_now)
        return payload

    def job_details_payload(self, job_id: str) -> dict[str, Any] | None:
        """Return one JobInstance plus its durable execution/audit trace."""
        key = str(job_id)
        job = self.store.active.get(key)
        if job is None:
            job = next((item for item in reversed(self.store.history) if item.job_id == key), None)
        if job is None:
            job = next((item for item in reversed(self.store.terminal_archive) if item.job_id == key), None)
        if job is None:
            return None
        payload = job.to_dict()
        force = self._force_status_for_job(job)
        if force is not None:
            payload["force"] = force
        payload["lifecycle_events"] = self.store.events_for_job(key)
        payload["notification_history"] = self.notifications.store.for_job(key)
        payload.update(self._waited_before_start_status(job))
        return payload

    def _robot_status_payload(self, now: datetime) -> dict[str, Any]:
        """Return compact read-only physical robot/resource state for the status page."""
        try:
            observation = self.execution.real.status_observation(now)
        except Exception as err:
            observation = {
                "observed_at": now.isoformat(),
                "normalized_state": "unknown",
                "phase": "UNKNOWN",
                "vendor": self.execution.real.adapter.vendor,
                "observation_error": f"{type(err).__name__}: {err}",
            }

        resource_keys = (
            "vacuum.battery_percent",
            "vacuum.charging",
            "dock.robot_docked",
            "dock.clean_water",
            "dock.dirty_water",
            "dock.detergent",
            "mop.attached",
        )
        resources: dict[str, Any] = {}
        try:
            snapshot = self.input_provider.snapshot(now, allow_test_overrides=False)
            for key in resource_keys:
                item = snapshot.values.get(key)
                if item is not None:
                    resources[key] = item.to_dict()
        except Exception as err:
            resources["error"] = f"{type(err).__name__}: {err}"

        water: dict[str, Any] = {}
        try:
            water_payload = self.statistics.water.payload()
            water = {
                "profile": water_payload.get("profile"),
                "clean": water_payload.get("clean"),
                "dirty": water_payload.get("dirty"),
                "calibration": water_payload.get("calibration"),
            }
        except Exception as err:
            water = {"error": f"{type(err).__name__}: {err}"}

        return {
            "observation": observation,
            "resources": resources,
            "water": water,
        }

    def status_payload(self) -> dict[str, Any]:
        """Return frontend/diagnostic state without mutating the engine."""
        now = self.clock.now()
        active = sorted(self.store.active.values(), key=lambda item: item.planned_start)
        history = sorted(
            self.store.history, key=lambda item: item.finished_at or item.planned_start, reverse=True
        )[:20]
        next_transition = self.next_transition_at()
        return {
            "entry_id": self.entry_id,
            "mode": self.execution_mode.value,
            "execution_mode": self.execution_mode.value,
            "watchdog_seconds": int(WATCHDOG_INTERVAL.total_seconds()),
            "clock": self.clock.as_dict(),
            "next_transition": next_transition.isoformat() if next_transition else None,
            "robot_status": self._robot_status_payload(now),
            "active_jobs": [self._job_status_dict(item, now) for item in active],
            "history": [
                {**item.to_dict(), **self._waited_before_start_status(item)}
                for item in history
            ],
            "history_storage": {
                "operational_terminal_jobs": len(self.store.history),
                "durable_terminal_jobs": len(self.store.terminal_archive),
                "lifecycle_events": len(self.store.events),
            },
            "recovery": {
                "startup_in_progress": self._startup_recovery_in_progress,
                **dict(self._last_recovery_status),
            },
            "dry_run": {
                "active": self.execution_mode is ExecutionMode.DRY_RUN,
                "execution_duration_seconds": self.execution.dry_run.duration_seconds,
                "pending_fault": self.execution.pending_fault,
                "timeline_generation": self._dry_run_timeline_generation,
                "physical_commands_allowed": self.execution_mode is ExecutionMode.REAL,
            },
            "real_execution": {
                **self.real_execution_settings,
                "adapter_vendor": self.execution.real.adapter.vendor,
                "physical_commands_allowed": self.execution_mode is ExecutionMode.REAL,
            },
            "test_overrides": self.overrides.as_dict(),
            "dependency_index": self.dependency_index.as_dict(),
            "notifications": {
                "settings": self.notifications.settings.to_dict(),
                "history": self.notifications.store.recent(20),
            },
            "preflight": {
                "policy": self.input_provider.policy.to_dict(),
                "validation": self.preflight.validation_issues(),
                "inputs": self.input_provider.snapshot().to_dict(),
            },
            "arbiter": {
                **dict(self._last_arbiter_status),
                "current_lease_owner": (
                    list(self.execution.lease_owner(active))
                    if self.execution.lease_owner(active) is not None
                    else None
                ),
            },
            "force": {
                **dict(self._force_status),
                "next_transition_at": self._force_next_transition_at.isoformat() if self._force_next_transition_at else None,
            },
            "counts": {
                "active": len(active),
                "planned": sum(item.state is JobState.PLANNED for item in active),
                "waiting": sum(item.state is JobState.WAIT for item in active),
                "starting": sum(item.state is JobState.STARTING for item in active),
                "running": sum(item.state is JobState.RUNNING for item in active),
            },
        }

    def _emit_job(self, job: JobInstance, action: str) -> None:
        # Keep an append-only lifecycle trace for future statistics.  The UI
        # event bus remains transient, while JobStore.events survives restarts.
        self.store.append_event(
            job, action, self.clock.now(),
            details={"clock_offset_seconds": int(self.clock.offset.total_seconds())},
        )
        async_dispatcher_send(
            self.hass,
            SIGNAL_JOB_UPDATED,
            {
                "entry_id": self.entry_id,
                "action": action,
                "job_id": job.job_id,
            },
        )

    def _emit_scheduler(self) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_SCHEDULER_UPDATED,
            {
                "entry_id": self.entry_id,
                "reason": "scheduler_updated",
            },
        )
