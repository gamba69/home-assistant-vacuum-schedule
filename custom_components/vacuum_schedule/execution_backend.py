"""Execution backends for Vacuum Schedule."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from .execution_adapter import PhysicalExecutionError, VacuumExecutionAdapter
from .execution_models import ExecutionAttempt, ExecutionAttemptState, ExecutionMode
from .execution_observer import RobotExecutionObservation, RobotExecutionPhase
from .models import NormalizedVacuumState
from .time_utils import instant_add, instant_delta, instant_ge

DRY_RUN_START_DELAY = timedelta(seconds=1)
DEFAULT_DRY_RUN_DURATION_SECONDS = 10
REAL_START_TIMEOUT = timedelta(seconds=120)
GENERIC_COMPLETION_SETTLE = timedelta(seconds=60)
ROBOROCK_COMPLETION_SETTLE = timedelta(seconds=120)
ROBOROCK_POST_AUTO_EMPTY_SETTLE = timedelta(seconds=10)
DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES = 30


class ExecutionBackend(Protocol):
    mode: ExecutionMode

    async def async_start(self, attempt: ExecutionAttempt, now: datetime) -> None: ...
    async def async_cancel(self, attempt: ExecutionAttempt, now: datetime) -> None: ...
    async def async_progress(self, attempt: ExecutionAttempt, now: datetime) -> bool: ...
    def next_transition_at(self, attempt: ExecutionAttempt) -> datetime | None: ...


class DryRunExecutionBackend:
    """Timer-based executor with no capability to call the robot.

    DryRunExecutionBackend has no reference to Home Assistant services, the
    CommandExecutor, or the physical vacuum adapter.
    """

    mode = ExecutionMode.DRY_RUN

    def __init__(self, *, duration_seconds: int = DEFAULT_DRY_RUN_DURATION_SECONDS) -> None:
        self.duration_seconds = max(1, min(300, int(duration_seconds)))

    async def async_start(self, attempt: ExecutionAttempt, now: datetime) -> None:
        fault = attempt.fault_injection
        attempt.requested_at = now
        attempt.state = ExecutionAttemptState.START_REQUESTED
        if fault == "start_rejected":
            attempt.state = ExecutionAttemptState.FAILED
            attempt.failure_reason = "execution_start_rejected"
            attempt.completed_at = now
            return
        attempt.expected_start_confirm_at = instant_add(now, DRY_RUN_START_DELAY)
        if fault == "start_timeout":
            attempt.expected_start_confirm_at = instant_add(now, REAL_START_TIMEOUT)

    async def async_cancel(self, attempt: ExecutionAttempt, now: datetime) -> None:
        if attempt.terminal:
            return
        attempt.state = ExecutionAttemptState.CANCELLED
        attempt.completed_at = now
        attempt.failure_reason = "user_cancelled"

    async def async_progress(self, attempt: ExecutionAttempt, now: datetime) -> bool:
        if attempt.terminal:
            return False
        changed = False
        if attempt.state is ExecutionAttemptState.START_REQUESTED:
            due = attempt.expected_start_confirm_at
            if due is not None and instant_ge(now, due):
                if attempt.fault_injection == "start_timeout":
                    attempt.state = ExecutionAttemptState.FAILED
                    attempt.failure_reason = "execution_start_timeout"
                    attempt.completed_at = now
                else:
                    attempt.state = ExecutionAttemptState.RUNNING
                    attempt.start_confirmed_at = now
                    attempt.expected_complete_at = instant_add(
                        now, timedelta(seconds=self.duration_seconds)
                    )
                changed = True
        if attempt.state is ExecutionAttemptState.RUNNING:
            due = attempt.expected_complete_at
            if due is not None and instant_ge(now, due):
                if attempt.fault_injection in {"execution_failed", "execution_lost"}:
                    attempt.state = ExecutionAttemptState.FAILED
                    attempt.failure_reason = (
                        "execution_lost"
                        if attempt.fault_injection == "execution_lost"
                        else "execution_failed"
                    )
                else:
                    attempt.state = ExecutionAttemptState.COMPLETED
                attempt.completed_at = now
                changed = True
        return changed

    def next_transition_at(self, attempt: ExecutionAttempt) -> datetime | None:
        if attempt.state is ExecutionAttemptState.START_REQUESTED:
            return attempt.expected_start_confirm_at
        if attempt.state is ExecutionAttemptState.RUNNING:
            return attempt.expected_complete_at
        return None


class RealExecutionBackend:
    """Restart-safe physical executor driven by observed robot state."""

    mode = ExecutionMode.REAL

    def __init__(
        self,
        hass: Any,
        executor: Any,
        clock: Any | None = None,
        *,
        persist_callback: Callable[[], Awaitable[None]] | None = None,
        restore_previous_settings: bool = True,
        input_provider: Any | None = None,
        runtime_error_recovery_minutes: int = DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES,
    ) -> None:
        self.hass = hass
        self.executor = executor
        self.clock = clock
        self._persist_callback = persist_callback
        self.adapter = VacuumExecutionAdapter(
            hass, executor, persist_callback=persist_callback
        )
        self.restore_previous_settings = bool(restore_previous_settings)
        self.input_provider = input_provider
        self.set_runtime_error_recovery_minutes(runtime_error_recovery_minutes)

    def set_runtime_error_recovery_minutes(self, minutes: int) -> None:
        self.runtime_error_recovery_minutes = max(1, min(1440, int(minutes)))
        self.runtime_error_recovery = timedelta(minutes=self.runtime_error_recovery_minutes)

    def set_restore_previous_settings(self, enabled: bool) -> None:
        self.restore_previous_settings = bool(enabled)

    async def _persist_barrier(self) -> None:
        if self._persist_callback is not None:
            await self._persist_callback()

    @staticmethod
    def _percent(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if 0.0 <= number <= 100.0 else None

    def _input_battery(self, now: datetime) -> tuple[float | None, str | None]:
        """Return the authoritative live battery percentage from pre-flight inputs.

        Modern Home Assistant vacuum integrations commonly expose battery as a
        dedicated ``sensor`` instead of a ``vacuum.*`` attribute.  Pre-flight
        already resolves that entity correctly; execution statistics must use
        the same physical source and must never use Dry-Run overrides.
        """
        if self.input_provider is None:
            return None, None
        try:
            snapshot = self.input_provider.snapshot(now, allow_test_overrides=False)
            item = snapshot.values.get("vacuum.battery_percent")
        except Exception:
            item = None
        if item is not None and bool(getattr(item, "effective_available", False)):
            value = self._percent(getattr(item, "effective_value", None))
            if value is not None:
                return value, getattr(item, "source_entity_id", None)

        # Battery collection is observational and must not disappear merely
        # because the user disabled battery as a pre-flight gate. Reuse the
        # same autodiscovery evidence directly as a measurement-only fallback.
        try:
            candidate = self.input_provider.auto_candidate_info("vacuum.battery_percent")
        except Exception:
            return None, None
        if not bool(candidate.get("available")):
            return None, None
        value = self._percent(candidate.get("raw"))
        if value is None:
            return None, None
        return value, candidate.get("entity_id")

    def status_observation(self, now: datetime) -> dict[str, Any]:
        """Return a read-only physical robot observation for status UI/diagnostics."""
        return self._observe(now).to_dict()

    def _observe(self, now: datetime) -> RobotExecutionObservation:
        """Observe the robot and enrich it with the configured battery source."""
        observation = self.adapter.observe(now)
        battery, source = self._input_battery(now)
        if battery is None:
            return observation
        sources = dict(observation.source_entities)
        if source:
            sources["battery"] = str(source)
        if observation.battery_percent == battery and sources == observation.source_entities:
            return observation
        return replace(observation, battery_percent=battery, source_entities=sources)

    def observation(self, now: datetime) -> RobotExecutionObservation:
        return self._observe(now)

    def observation_dependencies(self) -> tuple[str, ...]:
        """Return HA entities that must wake an active REAL observation loop."""
        return self.adapter.observation_dependencies()

    @staticmethod
    def _observation_state(observation: RobotExecutionObservation) -> ExecutionAttemptState:
        if observation.phase is RobotExecutionPhase.PAUSED:
            return ExecutionAttemptState.PAUSED
        if observation.phase is RobotExecutionPhase.SERVICE_BLOCKED:
            return ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
        if observation.phase is RobotExecutionPhase.SERVICE:
            return ExecutionAttemptState.ROBOT_SERVICE
        return ExecutionAttemptState.RUNNING

    @staticmethod
    def _last_clean_finished_after_start(
        attempt: ExecutionAttempt, observation: RobotExecutionObservation
    ) -> bool:
        if observation.last_clean_end is None or attempt.start_confirmed_at is None:
            return False
        try:
            return observation.last_clean_end >= attempt.start_confirmed_at
        except TypeError:
            return False

    def _settle_duration(
        self,
        attempt: ExecutionAttempt,
        observation: RobotExecutionObservation | None = None,
    ) -> timedelta:
        # Once post-clean auto-emptying was explicitly observed, it is already
        # the late service event the long Roborock fallback was designed to
        # catch. Only a short quiet debounce is needed after it ends.
        if attempt.metadata.get("post_clean_auto_empty_observed_at"):
            return ROBOROCK_POST_AUTO_EMPTY_SETTLE

        last = attempt.metadata.get("last_observation")
        last_observation = last if isinstance(last, dict) else {}
        vendor = observation.vendor if observation is not None else str(last_observation.get("vendor", "generic"))
        in_cleaning = observation.in_cleaning if observation is not None else last_observation.get("in_cleaning")
        if vendor == "roborock":
            floor_finished = self._floor_cleaning_finished_recorded(attempt)
            if observation is not None:
                floor_finished |= self._last_clean_finished_after_start(attempt, observation)
            if in_cleaning == 0 or floor_finished:
                return ROBOROCK_COMPLETION_SETTLE
        return GENERIC_COMPLETION_SETTLE

    @classmethod
    def _physical_cleaning_finished(
        cls, attempt: ExecutionAttempt, observation: RobotExecutionObservation
    ) -> bool:
        """Return whether the physical floor-cleaning command already finished.

        ``last_clean_end`` is authoritative when available. Roborock
        ``in_cleaning == 0`` is accepted only after the observer has previously
        seen actual floor-cleaning activity for this attempt. This prevents a
        pre-clean dock resource problem from being mistaken for a successfully
        completed cleaning merely because the robot is currently not cleaning.
        """
        if cls._last_clean_finished_after_start(attempt, observation):
            return True
        return (
            attempt.start_confirmed_at is not None
            and observation.vendor == "roborock"
            and observation.in_cleaning == 0
            and bool(attempt.metadata.get("cleaning_observed_at"))
        )

    @classmethod
    def _mark_floor_cleaning_finished(
        cls, attempt: ExecutionAttempt, observation: RobotExecutionObservation, now: datetime
    ) -> bool:
        """Persist strong evidence that floor cleaning has ended.

        ``last_clean_end`` closes only the floor-cleaning phase. It deliberately
        does *not* complete the ExecutionAttempt because Roborock may still be
        returning, washing the mop, emptying the bin, or settling on the dock.
        """
        if not cls._last_clean_finished_after_start(attempt, observation):
            return False
        finished_at = observation.last_clean_end or now
        value = finished_at.isoformat()
        changed = attempt.metadata.get("floor_cleaning_finished_at") != value
        attempt.metadata["floor_cleaning_finished_at"] = value
        attempt.metadata["floor_cleaning_finish_evidence"] = "last_clean_end"
        attempt.metadata.setdefault("floor_cleaning_finish_observed_at", now.isoformat())

        # ``last_clean_end`` is a separate diagnostic entity and may update after
        # the final dock SERVICE event. Promote a previously observed service only
        # when its observation time is not earlier than the actual floor-finish
        # timestamp. This excludes mid-clean mop washes.
        latest_service = cls._metadata_datetime(attempt, "latest_service_observed_at")
        if latest_service is not None:
            try:
                service_is_post_clean = latest_service >= finished_at
            except TypeError:
                service_is_post_clean = False
            if service_is_post_clean and not attempt.metadata.get("post_clean_service_observed_at"):
                attempt.metadata["post_clean_service_observed_at"] = latest_service.isoformat()
                changed = True
        latest_auto_empty = cls._metadata_datetime(attempt, "latest_auto_empty_observed_at")
        if latest_auto_empty is not None:
            try:
                auto_empty_is_post_clean = latest_auto_empty >= finished_at
            except TypeError:
                auto_empty_is_post_clean = False
            if auto_empty_is_post_clean and not attempt.metadata.get("post_clean_auto_empty_observed_at"):
                attempt.metadata["post_clean_auto_empty_observed_at"] = latest_auto_empty.isoformat()
                changed = True
        return changed

    @staticmethod
    def _floor_cleaning_finished_recorded(attempt: ExecutionAttempt) -> bool:
        return bool(attempt.metadata.get("floor_cleaning_finished_at"))

    @staticmethod
    def _metadata_datetime(attempt: ExecutionAttempt, key: str) -> datetime | None:
        value = attempt.metadata.get(key)
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _mark_post_floor_activity(
        cls, attempt: ExecutionAttempt, observation: RobotExecutionObservation, now: datetime
    ) -> bool:
        """Track return/service and identify service that truly follows FLOOR_DONE."""
        if not attempt.metadata.get("cleaning_observed_at"):
            return False
        changed = False
        if observation.phase is RobotExecutionPhase.RETURNING:
            if not attempt.metadata.get("post_clean_returning_observed_at"):
                attempt.metadata["post_clean_returning_observed_at"] = now.isoformat()
                changed = True
        if observation.service_activity or observation.phase in {
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
        }:
            latest = now.isoformat()
            if attempt.metadata.get("latest_service_observed_at") != latest:
                attempt.metadata["latest_service_observed_at"] = latest
                changed = True
            if observation.auto_empty_activity:
                if attempt.metadata.get("latest_auto_empty_observed_at") != latest:
                    attempt.metadata["latest_auto_empty_observed_at"] = latest
                    changed = True
            floor_done = cls._metadata_datetime(attempt, "floor_cleaning_finished_at")
            if floor_done is not None:
                try:
                    service_is_post_clean = now >= floor_done
                except TypeError:
                    service_is_post_clean = False
                if service_is_post_clean and not attempt.metadata.get("post_clean_service_observed_at"):
                    attempt.metadata["post_clean_service_observed_at"] = latest
                    attempt.metadata["post_clean_service_phase"] = observation.phase.value
                    changed = True
                if (
                    service_is_post_clean
                    and observation.auto_empty_activity
                    and not attempt.metadata.get("post_clean_auto_empty_observed_at")
                ):
                    attempt.metadata["post_clean_auto_empty_observed_at"] = latest
                    changed = True
        return changed

    @classmethod
    def _strong_terminal_after_floor_done(
        cls, attempt: ExecutionAttempt, observation: RobotExecutionObservation
    ) -> bool:
        """Return whether post-clean dock service is observably finished.

        A first DOCKED/IDLE event is intentionally *not* enough: Roborock may
        dock, then start mop washing or auto-emptying a moment later. Immediate
        completion eligibility therefore requires this attempt to have shown a
        post-clean SERVICE phase and then returned to a quiet IDLE/DOCKED
        observation. The caller still requires a bounded quiet interval before
        terminalizing, so a later auto-empty phase can reclaim the same lease.
        If service telemetry is absent, the same settle timer is the fallback.
        """
        if not cls._floor_cleaning_finished_recorded(attempt):
            return False
        if not attempt.metadata.get("post_clean_service_observed_at"):
            return False
        if observation.phase is not RobotExecutionPhase.IDLE:
            return False
        if observation.session_active or observation.service_activity:
            return False
        return observation.normalized_state in {
            NormalizedVacuumState.DOCKED,
            NormalizedVacuumState.IDLE,
        }

    def _configured_resource_blockers(
        self, attempt: ExecutionAttempt, now: datetime
    ) -> tuple[str, ...]:
        """Return live physical resource blockers using REAL input semantics.

        The normal pre-flight input layer remains the authoritative source for
        user-configured tank/consumable sensors. Test overrides are explicitly
        disabled here because this is a physical REAL execution.
        """
        if self.input_provider is None:
            return ()
        try:
            snapshot = self.input_provider.snapshot(now, allow_test_overrides=False)
            bindings = self.input_provider.bindings
        except Exception:
            return ()

        checks = (
            ("dock.clean_water", "clean_water_insufficient", True),
            ("dock.dirty_water", "dirty_water_full", True),
            ("dock.detergent", "detergent_unavailable", True),
            (
                "mop.attached",
                "mop_not_attached",
                attempt.cleaning_params.get("mop_mode") not in {None, "", "__none__", "none", "off"},
            ),
        )
        blockers: list[str] = []
        for key, code, applicable in checks:
            if not applicable:
                continue
            binding = bindings.get(key)
            item = snapshot.values.get(key)
            if binding is None or item is None:
                continue
            mode = getattr(getattr(binding, "binding_mode", None), "value", str(getattr(binding, "binding_mode", "")))
            if mode == "disabled":
                continue
            if mode == "auto" and not item.source_entity_id:
                continue
            # Invalid/unavailable sources are handled by the normal scheduler
            # pre-flight path. During an owned physical attempt only a fresh,
            # semantically valid False value becomes a service blocker.
            if item.effective_available and item.status == "ready" and item.effective_value is False:
                blockers.append(code)
        return tuple(dict.fromkeys(blockers))

    @staticmethod
    def _mark_cleaning_observed(
        attempt: ExecutionAttempt, observation: RobotExecutionObservation, now: datetime
    ) -> None:
        if observation.phase is RobotExecutionPhase.CLEANING or observation.task_kind in {
            "segment",
            "zone",
        }:
            attempt.metadata.setdefault("cleaning_observed_at", now.isoformat())

    @staticmethod
    def _record_statistics_observation(
        attempt: ExecutionAttempt, observation: RobotExecutionObservation
    ) -> None:
        """Persist a compact physical observation timeline for statistics.

        Samples are deliberately stored with the execution attempt so a crash
        between physical completion and terminal Job archival does not lose the
        measurements. Consecutive identical samples are coalesced.
        """
        sample = {
            key: value
            for key, value in observation.to_dict().items()
            if key in {
                "observed_at", "phase", "vendor", "vendor_status", "task_kind",
                "battery_percent", "cleaning_time_seconds", "cleaning_area_m2",
                "clean_percent", "wash_mode", "smart_wash", "wash_interval",
                "wash_status", "wash_phase", "dry_status", "dust_collection_status",
                "charge_status", "back_type", "service_activity", "resource_blockers",
            }
        }
        samples = attempt.metadata.setdefault("statistics_observations", [])
        if not isinstance(samples, list):
            samples = []
            attempt.metadata["statistics_observations"] = samples
        if samples:
            previous = samples[-1]
            comparable_keys = tuple(key for key in sample if key != "observed_at")
            if all(previous.get(key) == sample.get(key) for key in comparable_keys):
                return
        samples.append(sample)
        if len(samples) > 1000:
            del samples[:-1000]

    @staticmethod
    def _runtime_error_payload(observation: RobotExecutionObservation) -> dict[str, Any]:
        return {
            "vacuum_error": observation.vacuum_error,
            "dock_error": observation.dock_error,
            "raw_vacuum_error": observation.raw_vacuum_error,
            "raw_dock_error": observation.raw_dock_error,
            "vendor_status": observation.vendor_status,
            "raw_state": observation.raw_state,
        }

    @staticmethod
    def _active_runtime_error_incident(attempt: ExecutionAttempt) -> dict[str, Any] | None:
        incidents = attempt.metadata.get("runtime_incidents")
        if not isinstance(incidents, list) or not incidents:
            return None
        latest = incidents[-1]
        if isinstance(latest, dict) and latest.get("kind") == "robot_error" and latest.get("status") == "active":
            return latest
        return None

    def _open_runtime_error_incident(
        self, attempt: ExecutionAttempt, observation: RobotExecutionObservation, now: datetime
    ) -> bool:
        payload = self._runtime_error_payload(observation)
        attempt.metadata["physical_error"] = payload
        incident = self._active_runtime_error_incident(attempt)
        changed = False
        if incident is None:
            incidents = attempt.metadata.setdefault("runtime_incidents", [])
            if not isinstance(incidents, list):
                incidents = []
                attempt.metadata["runtime_incidents"] = incidents
            incident = {
                "kind": "robot_error",
                "status": "active",
                "started_at": now.isoformat(),
                "initial_error": payload,
                "last_error": payload,
            }
            incidents.append(incident)
            attempt.runtime_error_at = now
            attempt.error_recovery_deadline_at = instant_add(now, self.runtime_error_recovery)
            attempt.metadata["runtime_error_recovery_minutes"] = self.runtime_error_recovery_minutes
            changed = True
        else:
            if attempt.runtime_error_at is None:
                try:
                    attempt.runtime_error_at = datetime.fromisoformat(str(incident.get("started_at")))
                except (TypeError, ValueError):
                    attempt.runtime_error_at = now
                changed = True
            if attempt.error_recovery_deadline_at is None:
                attempt.error_recovery_deadline_at = instant_add(
                    attempt.runtime_error_at, self.runtime_error_recovery
                )
                changed = True
            if incident.get("last_error") != payload:
                incident["last_error"] = payload
                changed = True
        if attempt.state is not ExecutionAttemptState.ROBOT_ERROR_BLOCKED:
            attempt.state = ExecutionAttemptState.ROBOT_ERROR_BLOCKED
            changed = True
        attempt.completion_candidate_at = None
        return changed

    @staticmethod
    def _close_runtime_error_incident(
        attempt: ExecutionAttempt, now: datetime, status: str, observation: RobotExecutionObservation | None = None
    ) -> bool:
        incidents = attempt.metadata.get("runtime_incidents")
        if not isinstance(incidents, list) or not incidents:
            attempt.runtime_error_at = None
            attempt.error_recovery_deadline_at = None
            return False
        incident = incidents[-1]
        if not isinstance(incident, dict) or incident.get("kind") != "robot_error" or incident.get("status") != "active":
            attempt.runtime_error_at = None
            attempt.error_recovery_deadline_at = None
            return False
        incident["status"] = str(status)
        incident["ended_at"] = now.isoformat()
        if attempt.runtime_error_at is not None:
            try:
                incident["duration_seconds"] = max(0, int(instant_delta(now, attempt.runtime_error_at).total_seconds()))
            except ValueError:
                pass
        if observation is not None:
            incident["recovery_observation"] = observation.to_dict()
        attempt.runtime_error_at = None
        attempt.error_recovery_deadline_at = None
        return True

    @staticmethod
    def _runtime_error_timeout_due(attempt: ExecutionAttempt, now: datetime) -> bool:
        deadline = attempt.error_recovery_deadline_at
        return deadline is not None and instant_ge(now, deadline)

    async def _finish(
        self,
        attempt: ExecutionAttempt,
        state: ExecutionAttemptState,
        now: datetime,
        reason: str | None = None,
    ) -> None:
        if self._active_runtime_error_incident(attempt) is not None:
            incident_status = (
                "completed" if state is ExecutionAttemptState.COMPLETED
                else "cancelled" if state is ExecutionAttemptState.CANCELLED
                else "failed"
            )
            self._close_runtime_error_incident(attempt, now, incident_status)
        attempt.state = state
        attempt.completed_at = now
        attempt.failure_reason = reason
        if (state is not ExecutionAttemptState.COMPLETED or attempt.final_pass) and self.restore_previous_settings and not attempt.metadata.get("parameter_restore_done"):
            try:
                await self.adapter.async_restore_parameters(attempt)
            except Exception as err:  # restoration must never rewrite execution result
                attempt.metadata["parameter_restore_error"] = f"{type(err).__name__}: {err}"
            attempt.metadata["parameter_restore_done"] = True
        # Speaker muting is an operational guard, not a user restore-setting.
        # Always retry a pending volume restoration even when parameter restore
        # itself is disabled or failed.
        try:
            await self.adapter.async_restore_pending_volume(attempt)
        except Exception as err:
            attempt.metadata["volume_restore_error"] = f"{type(err).__name__}: {err}"

    async def async_start(self, attempt: ExecutionAttempt, now: datetime) -> None:
        if attempt.execution_mode is not ExecutionMode.REAL:
            raise RuntimeError("physical_command_rejected_non_real_attempt")
        if self.clock is not None and getattr(self.clock, "offset", timedelta()).total_seconds():
            raise RuntimeError("physical_command_rejected_test_clock_active")

        attempt.state = ExecutionAttemptState.PREPARING
        attempt.preparing_at = now
        try:
            self._record_statistics_observation(attempt, self._observe(now))
            # Recovery may resume a PREPARING attempt after HA restarted while
            # the robot was temporarily muted. Restore that durable snapshot
            # before starting a fresh preparation transaction.
            await self.adapter.async_restore_pending_volume(attempt)
            validation = await self.adapter.async_validate_targets(attempt)
            attempt.metadata["target_validation"] = validation.metadata
            await self.adapter.async_final_guard(attempt, now)
            if self.adapter.vendor == "roborock" and attempt.target_type == "segment":
                # The current Roborock HA entity silently ignores segments from
                # another map. Validate against its loaded current-map trait when
                # that information is available.
                observation = self._observe(now)
                if observation.current_map is not None:
                    wrong = [
                        target for target in attempt.targets
                        if "_" in target and target.split("_", 1)[0] != str(observation.current_map)
                    ]
                    if wrong:
                        raise PhysicalExecutionError("target_map_not_active", ",".join(wrong))
            await self.adapter.async_prepare(attempt)
            # Re-check immediately after parameter writes: user/app activity may
            # have started while preparation was in flight.
            await self.adapter.async_final_guard(attempt, now)
        except PhysicalExecutionError as err:
            await self._finish(attempt, ExecutionAttemptState.FAILED, now, err.code)
            attempt.metadata["start_error"] = err.detail
            return
        except Exception as err:
            await self._finish(attempt, ExecutionAttemptState.FAILED, now, "execution_preparation_failed")
            attempt.metadata["start_error"] = f"{type(err).__name__}: {err}"
            return

        # Critical at-most-once barrier: persist that a command MAY be emitted
        # before making a physical call. Recovery from COMMAND_INTENT never
        # blindly sends the start command again.
        attempt.state = ExecutionAttemptState.COMMAND_INTENT
        attempt.command_intent_at = now
        attempt.expected_start_confirm_at = instant_add(now, REAL_START_TIMEOUT)
        await self._persist_barrier()

        try:
            await self.adapter.async_start_cleaning(attempt)
        except Exception as err:
            # A transport/service exception can be ambiguous: the robot may have
            # accepted the command before the response was lost. Reconcile by
            # observation instead of retrying automatically.
            attempt.state = ExecutionAttemptState.RECONCILING
            attempt.metadata["command_error"] = f"{type(err).__name__}: {err}"
            attempt.metadata["reconcile_reason"] = "ambiguous_start_command"
            return

        attempt.requested_at = now
        attempt.state = ExecutionAttemptState.START_REQUESTED

    async def async_cancel(self, attempt: ExecutionAttempt, now: datetime) -> None:
        if attempt.terminal or attempt.state is ExecutionAttemptState.CANCEL_REQUESTED:
            return
        attempt.state = ExecutionAttemptState.CANCEL_REQUESTED
        attempt.cancel_requested_at = now
        await self._persist_barrier()
        try:
            await self.adapter.async_stop()
        except Exception as err:
            attempt.metadata["cancel_error"] = f"{type(err).__name__}: {err}"
        # Confirmation is observation-driven in async_progress.

    async def async_pause(self, attempt: ExecutionAttempt, now: datetime) -> None:
        if attempt.terminal:
            return
        await self.adapter.async_pause()
        attempt.metadata["pause_requested_at"] = now.isoformat()

    async def async_resume(self, attempt: ExecutionAttempt, now: datetime) -> None:
        if attempt.terminal:
            return
        observation = self._observe(now)
        if observation.phase is not RobotExecutionPhase.PAUSED:
            raise PhysicalExecutionError("execution_not_paused")
        await self.adapter.async_resume()
        attempt.metadata["resume_requested_at"] = now.isoformat()

    async def _progress_cancel(
        self, attempt: ExecutionAttempt, observation: RobotExecutionObservation, now: datetime
    ) -> bool:
        if observation.phase is RobotExecutionPhase.UNAVAILABLE:
            return False
        if observation.session_active or observation.phase in {
            RobotExecutionPhase.CLEANING,
            RobotExecutionPhase.PAUSED,
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
        }:
            return False
        await self._finish(attempt, ExecutionAttemptState.CANCELLED, now, "user_cancelled")
        if (
            observation.phase is not RobotExecutionPhase.RETURNING
            and not attempt.metadata.get("return_home_requested")
        ):
            try:
                await self.adapter.async_return_home()
                attempt.metadata["return_home_requested"] = True
            except Exception as err:
                attempt.metadata["return_home_error"] = f"{type(err).__name__}: {err}"
        return True

    async def async_progress(self, attempt: ExecutionAttempt, now: datetime) -> bool:
        if attempt.terminal:
            return False
        observation = self._observe(now)
        attempt.last_observed_at = now
        attempt.metadata["last_observation"] = observation.to_dict()
        self._record_statistics_observation(attempt, observation)
        self._mark_cleaning_observed(attempt, observation, now)
        observation_metadata_changed = self._mark_floor_cleaning_finished(
            attempt, observation, now
        )
        observation_metadata_changed |= self._mark_post_floor_activity(
            attempt, observation, now
        )

        configured_resource_blockers = self._configured_resource_blockers(attempt, now)
        merged_resource_blockers = tuple(
            dict.fromkeys((*observation.resource_blockers, *configured_resource_blockers))
        )
        # Physical tank/consumable sensors are authoritative resource facts.
        # If Roborock simultaneously collapses into a generic ERROR without a
        # specific non-resource diagnostic, prefer the known actionable resource
        # condition over opening the generic robot-error recovery timer. A real
        # hard error becomes visible as soon as the resource condition clears.
        resource_correlated_error = (
            bool(merged_resource_blockers)
            and observation.phase is RobotExecutionPhase.ERROR
            and not observation.vacuum_error
            and not observation.dock_error
        )
        service_resource_blocked = bool(merged_resource_blockers) and (
            observation.phase is RobotExecutionPhase.SERVICE_BLOCKED
            or observation.phase is RobotExecutionPhase.SERVICE
            or observation.service_activity
            or resource_correlated_error
        )
        if service_resource_blocked:
            attempt.metadata["resource_blockers"] = list(merged_resource_blockers)
            # A Roborock dock status may be opaque to the generic classifier. If
            # an explicitly configured resource sensor confirms the blocker while
            # the robot is in a known service phase, keep the raw dock status for
            # diagnostics but do not turn the owned cleaning session into ERROR.
            if observation.phase is RobotExecutionPhase.ERROR:
                attempt.metadata["suppressed_service_error"] = {
                    "vacuum_error": observation.vacuum_error,
                    "dock_error": observation.dock_error,
                    "raw_vacuum_error": observation.raw_vacuum_error,
                    "raw_dock_error": observation.raw_dock_error,
                }

        if attempt.state is ExecutionAttemptState.CANCEL_REQUESTED:
            return await self._progress_cancel(attempt, observation, now)

        if observation.phase is RobotExecutionPhase.ERROR and not service_resource_blocked:
            # Before physical start confirmation an ERROR still means that the
            # start could not be established safely. After confirmation, however,
            # the robot owns a resumable cleaning session: keep the ExecutionLease
            # and wait for the same session to recover instead of archiving the Job.
            if attempt.start_confirmed_at is None:
                attempt.metadata["physical_error"] = self._runtime_error_payload(observation)
                await self._finish(attempt, ExecutionAttemptState.FAILED, now, "execution_failed")
                return True
            changed = self._open_runtime_error_incident(attempt, observation, now)
            if self._runtime_error_timeout_due(attempt, now):
                self._close_runtime_error_incident(attempt, now, "timeout", observation)
                attempt.metadata["runtime_error_timeout_observation"] = observation.to_dict()
                await self._finish(
                    attempt, ExecutionAttemptState.FAILED, now, "robot_error_recovery_timeout"
                )
                return True
            return changed or observation_metadata_changed

        if (
            service_resource_blocked
            and attempt.start_confirmed_at is not None
        ):
            if attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED:
                self._close_runtime_error_incident(attempt, now, "recovered", observation)
                attempt.metadata["runtime_error_recovered_at"] = now.isoformat()
            blockers = list(merged_resource_blockers)
            attempt.metadata["resource_blockers"] = blockers
            attempt.metadata.setdefault("resource_blocked_since", now.isoformat())
            attempt.metadata["resource_blocked_observation"] = observation.to_dict()
            attempt.completion_candidate_at = None

            # A dock resource condition after the final floor pass must not turn
            # a successfully cleaned Job into a robot failure. The resource will
            # independently block *future* wet Jobs through normal Pre-flight.
            # Final mop washing / emptying telemetry is still recorded when it is
            # available, but an impossible dock-service step is not part of the
            # success criterion for the already completed floor-cleaning result.
            if self._physical_cleaning_finished(attempt, observation) and attempt.final_pass:
                attempt.metadata["post_clean_resource_blockers"] = blockers
                attempt.metadata["post_clean_service_incomplete"] = True
                if not self._floor_cleaning_finished_recorded(attempt):
                    attempt.metadata["floor_cleaning_finished_at"] = now.isoformat()
                    attempt.metadata["floor_cleaning_finish_evidence"] = "in_cleaning_zero_after_cleaning"
                    attempt.metadata.setdefault("floor_cleaning_finish_observed_at", now.isoformat())
                attempt.metadata["completion_evidence"] = "floor_done_resource_service_unavailable"
                await self._finish(attempt, ExecutionAttemptState.COMPLETED, now)
                return True

            if attempt.state is not ExecutionAttemptState.ROBOT_SERVICE_BLOCKED:
                attempt.state = ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
                attempt.metadata["service_blocked_before_completion"] = True
                return True
            return observation_metadata_changed

        if attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED:
            # The error may clear without a Scheduler command. Resume the same
            # physical ownership. A quiet IDLE with no completion evidence still
            # remains error-blocked, but explicit FLOOR_DONE is allowed to leave
            # error recovery and continue through the normal dock/service finish
            # protocol below.
            if self._runtime_error_timeout_due(attempt, now):
                self._close_runtime_error_incident(attempt, now, "timeout", observation)
                attempt.metadata["runtime_error_timeout_observation"] = observation.to_dict()
                await self._finish(
                    attempt, ExecutionAttemptState.FAILED, now, "robot_error_recovery_timeout"
                )
                return True
            if observation.session_active:
                self._close_runtime_error_incident(attempt, now, "recovered", observation)
                attempt.metadata["runtime_error_recovered_at"] = now.isoformat()
                attempt.state = self._observation_state(observation)
                if attempt.state is ExecutionAttemptState.PAUSED:
                    attempt.paused_at = now
                attempt.completion_candidate_at = None
                return True
            if self._floor_cleaning_finished_recorded(attempt):
                self._close_runtime_error_incident(attempt, now, "recovered", observation)
                attempt.metadata["runtime_error_recovered_at"] = now.isoformat()
                attempt.state = ExecutionAttemptState.RUNNING
                observation_metadata_changed = True
            else:
                return observation_metadata_changed

        prestart_states = {
            ExecutionAttemptState.COMMAND_INTENT,
            ExecutionAttemptState.START_REQUESTED,
        }
        if service_resource_blocked and attempt.start_confirmed_at is None:
            # The command may have been accepted but dock service was blocked
            # before floor-cleaning activity became observable. Preserve the
            # at-most-once ownership barrier and wait; do not claim success.
            if observation.session_active or observation.service_activity:
                attempt.state = ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
                attempt.start_confirmed_at = attempt.start_confirmed_at or now
                attempt.expected_start_confirm_at = None
                attempt.metadata.setdefault("resource_blocked_since", now.isoformat())
                attempt.metadata["service_blocked_before_completion"] = True
                attempt.metadata["start_observation"] = observation.to_dict()
                return True
        if attempt.state in prestart_states or (
            attempt.state is ExecutionAttemptState.RECONCILING
            and attempt.start_confirmed_at is None
        ):
            if observation.matches_target_type(attempt.target_type) or (
                observation.session_active and observation.task_kind in (None, "unknown")
            ):
                attempt.state = self._observation_state(observation)
                attempt.start_confirmed_at = attempt.start_confirmed_at or now
                attempt.expected_start_confirm_at = None
                attempt.metadata.pop("reconcile_reason", None)
                attempt.metadata["start_observation"] = observation.to_dict()
                return True
            if observation.session_active and observation.task_kind not in (None, "unknown"):
                await self._finish(attempt, ExecutionAttemptState.FAILED, now, "execution_preempted_external")
                return True
            if observation.phase is RobotExecutionPhase.UNAVAILABLE:
                attempt.state = ExecutionAttemptState.RECONCILING
                attempt.metadata.setdefault("unavailable_since", now.isoformat())
                return True
            if attempt.expected_start_confirm_at and instant_ge(now, attempt.expected_start_confirm_at):
                await self._finish(attempt, ExecutionAttemptState.FAILED, now, "execution_start_timeout")
                return True
            return False

        # A confirmed execution remains physically owned while the robot cleans,
        # pauses, returns for mop service/charging, or temporarily disappears.
        if observation.phase is RobotExecutionPhase.UNAVAILABLE:
            if attempt.state is not ExecutionAttemptState.RECONCILING:
                attempt.metadata["reconcile_resume_state"] = attempt.state.value
                attempt.state = ExecutionAttemptState.RECONCILING
                attempt.metadata.setdefault("unavailable_since", now.isoformat())
                return True
            return False
        attempt.metadata.pop("unavailable_since", None)

        if observation.session_active:
            new_state = self._observation_state(observation)
            if attempt.state is not new_state:
                attempt.state = new_state
                if new_state is ExecutionAttemptState.PAUSED:
                    attempt.paused_at = now
                attempt.completion_candidate_at = None
                if new_state is not ExecutionAttemptState.ROBOT_SERVICE_BLOCKED:
                    attempt.metadata.pop("resource_blockers", None)
                    attempt.metadata.pop("resource_blocked_since", None)
                    attempt.metadata.pop("service_blocked_before_completion", None)
                return True
            return observation_metadata_changed

        if observation.phase is RobotExecutionPhase.RETURNING:
            # Returning is not completion. Wait until a stable terminal state.
            if attempt.state is not ExecutionAttemptState.ROBOT_SERVICE:
                attempt.state = ExecutionAttemptState.ROBOT_SERVICE
                return True
            return observation_metadata_changed

        if observation.phase in {RobotExecutionPhase.IDLE, RobotExecutionPhase.UNKNOWN}:
            if self._strong_terminal_after_floor_done(attempt, observation):
                # Roborock dock service is not necessarily a single contiguous
                # phase. Mop washing may end, the dock can look idle briefly,
                # and auto-emptying can start afterwards. Do not archive the Job
                # on that first quiet sample. Start (or continue) a quiescence
                # timer; any subsequent SERVICE observation clears the candidate
                # through the normal session_active branch above.
                if (
                    attempt.state is not ExecutionAttemptState.COMPLETION_PENDING
                    or attempt.completion_candidate_at is None
                ):
                    attempt.state = ExecutionAttemptState.COMPLETION_PENDING
                    attempt.completion_candidate_at = now
                    attempt.metadata["post_clean_quiet_started_at"] = now.isoformat()
                    return True
                candidate = attempt.completion_candidate_at
                if instant_ge(now, instant_add(candidate, self._settle_duration(attempt, observation))):
                    attempt.metadata.pop("resource_blockers", None)
                    attempt.metadata.pop("resource_blocked_since", None)
                    attempt.metadata.pop("service_blocked_before_completion", None)
                    attempt.metadata["completion_evidence"] = "floor_done_and_dock_quiescent"
                    await self._finish(attempt, ExecutionAttemptState.COMPLETED, now)
                    return True
                return observation_metadata_changed

            if attempt.metadata.get("service_blocked_before_completion"):
                # If floor cleaning is known (explicitly or through the legacy
                # in_cleaning transition) but final dock telemetry is incomplete,
                # leave the blocked state and use the bounded settle fallback. A
                # pre-clean service block still retains ownership until cleaning
                # actually resumes or the Job deadline expires.
                if self._physical_cleaning_finished(attempt, observation):
                    attempt.metadata.pop("resource_blockers", None)
                    attempt.metadata.pop("resource_blocked_since", None)
                    attempt.metadata.pop("service_blocked_before_completion", None)
                    attempt.state = ExecutionAttemptState.COMPLETION_PENDING
                    attempt.completion_candidate_at = now
                    return True
                if attempt.state is not ExecutionAttemptState.ROBOT_SERVICE_BLOCKED:
                    attempt.state = ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
                    return True
                return observation_metadata_changed

            if attempt.state is not ExecutionAttemptState.COMPLETION_PENDING:
                attempt.state = ExecutionAttemptState.COMPLETION_PENDING
                attempt.completion_candidate_at = now
                return True
            candidate = attempt.completion_candidate_at or now
            if instant_ge(now, instant_add(candidate, self._settle_duration(attempt, observation))):
                attempt.metadata["completion_evidence"] = "settle_timeout_fallback"
                await self._finish(attempt, ExecutionAttemptState.COMPLETED, now)
                return True
            return observation_metadata_changed

        return observation_metadata_changed

    def next_transition_at(self, attempt: ExecutionAttempt) -> datetime | None:
        if attempt.state is ExecutionAttemptState.ROBOT_ERROR_BLOCKED:
            return attempt.error_recovery_deadline_at
        if attempt.state in {
            ExecutionAttemptState.COMMAND_INTENT,
            ExecutionAttemptState.START_REQUESTED,
            ExecutionAttemptState.RECONCILING,
        } and attempt.start_confirmed_at is None:
            return attempt.expected_start_confirm_at
        if attempt.state is ExecutionAttemptState.COMPLETION_PENDING and attempt.completion_candidate_at:
            return instant_add(
                attempt.completion_candidate_at,
                self._settle_duration(attempt),
            )
        return None
