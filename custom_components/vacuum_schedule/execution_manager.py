"""Execution planning/orchestration for Vacuum Schedule."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .execution_backend import DryRunExecutionBackend, REAL_START_TIMEOUT, RealExecutionBackend
from .execution_models import (
    ExecutionAttempt,
    ExecutionAttemptState,
    ExecutionMode,
    RepetitionMode,
)
from .job import JobInstance, JobReason, JobState, ZoneExecution, ZoneJobState, ZoneResult
from .time_utils import instant_add, instant_ge


_ACTIVE_RUNNING_STATES = {
    ExecutionAttemptState.RUNNING,
    ExecutionAttemptState.PAUSED,
    ExecutionAttemptState.ROBOT_SERVICE,
    ExecutionAttemptState.ROBOT_SERVICE_BLOCKED,
    ExecutionAttemptState.ROBOT_ERROR_BLOCKED,
    ExecutionAttemptState.COMPLETION_PENDING,
    ExecutionAttemptState.RECONCILING,
    ExecutionAttemptState.CANCEL_REQUESTED,
}
_STARTING_STATES = {
    ExecutionAttemptState.CREATED,
    ExecutionAttemptState.PREPARING,
    ExecutionAttemptState.COMMAND_INTENT,
    ExecutionAttemptState.START_REQUESTED,
    ExecutionAttemptState.START_CONFIRMED,
}

# These reasons are authoritative pre-flight WAIT conditions.  If one appears
# in the real backend's final guard *before* COMMAND_INTENT, no robot command
# has been emitted and the occurrence has not been physically attempted.  A
# late state change here must therefore defer the Job, never consume it as a
# failed execution.
_RETRYABLE_PRECOMMAND_WAIT_REASONS = frozenset({
    "vacuum_busy",
    "vacuum_unavailable",
    "clean_water_insufficient",
    "dirty_water_full",
    "detergent_unavailable",
})


class ExecutionManager:
    """Bridge business zone lifecycles to one of the execution backends."""

    def __init__(
        self,
        hass: Any,
        executor: Any,
        *,
        clock: Any | None = None,
        dry_run_duration_seconds: int = 10,
        persist_callback: Callable[[], Awaitable[None]] | None = None,
        restore_previous_settings: bool = True,
        input_provider: Any | None = None,
        runtime_error_recovery_minutes: int = 30,
    ) -> None:
        self.dry_run = DryRunExecutionBackend(duration_seconds=dry_run_duration_seconds)
        self.real = RealExecutionBackend(
            hass,
            executor,
            clock,
            persist_callback=persist_callback,
            restore_previous_settings=restore_previous_settings,
            input_provider=input_provider,
            runtime_error_recovery_minutes=runtime_error_recovery_minutes,
        )
        self._pending_fault: str | None = None

    def backend_for(self, mode: ExecutionMode):
        return self.real if mode is ExecutionMode.REAL else self.dry_run

    def set_dry_run_duration(self, seconds: int) -> None:
        self.dry_run.duration_seconds = max(1, min(300, int(seconds)))

    def set_restore_previous_settings(self, enabled: bool) -> None:
        self.real.set_restore_previous_settings(enabled)

    def set_runtime_error_recovery_minutes(self, minutes: int) -> None:
        self.real.set_runtime_error_recovery_minutes(minutes)

    def inject_fault_once(self, fault: str | None) -> None:
        allowed = {None, "start_rejected", "start_timeout", "execution_failed", "execution_lost"}
        if fault not in allowed:
            raise ValueError("invalid_execution_fault")
        self._pending_fault = fault

    @property
    def pending_fault(self) -> str | None:
        return self._pending_fault

    @staticmethod
    def active_attempts(job: JobInstance) -> list[ExecutionAttempt]:
        return [attempt for attempt in job.execution_attempts.values() if not attempt.terminal]

    def lease_owner(self, jobs: list[JobInstance]) -> tuple[str, str] | None:
        for job in jobs:
            for attempt in self.active_attempts(job):
                return job.job_id, attempt.attempt_id
        return None

    @staticmethod
    def shared_target_blocked_zone_ids(
        job: JobInstance, ready: list[ZoneExecution]
    ) -> set[str]:
        """Block a physical target until every active logical owner is ready.

        A target referenced by two Cleaning Zones must not be cleaned while one
        owner says PASS and another still says WAIT. Terminal SKIPPED/FAILED
        memberships no longer constrain the target.
        """
        ready_ids = {zone.zone_id for zone in ready}
        blocked: set[str] = set()
        owners: dict[tuple[str, str], list[ZoneExecution]] = {}
        for zone in job.zone_runs.values():
            if zone.terminal or not zone.robot_target_id:
                continue
            owners.setdefault((zone.robot_target_type, zone.robot_target_id), []).append(zone)
        for group in owners.values():
            group_ready = [zone for zone in group if zone.zone_id in ready_ids]
            if not group_ready:
                continue
            if any(zone.zone_id not in ready_ids for zone in group):
                blocked.update(zone.zone_id for zone in group_ready)
        return blocked

    def build_attempt(
        self,
        job: JobInstance,
        ready: list[ZoneExecution],
        now: datetime,
        *,
        pass_index: int = 1,
        pass_total: int | None = None,
    ) -> ExecutionAttempt | None:
        if not ready:
            return None
        target_type = ready[0].robot_target_type
        selected = [zone for zone in ready if zone.robot_target_type == target_type]
        targets = tuple(dict.fromkeys(zone.robot_target_id for zone in selected if zone.robot_target_id))
        if not targets:
            return None
        try:
            requested_passes = max(1, int(job.cleaning_params.get("passes", 1) or 1))
        except (TypeError, ValueError):
            requested_passes = 1

        # passes belongs to one merged cleaning command. Separate attempts are
        # only a capability fallback when the concrete adapter cannot encode
        # repetitions natively. Explicit pass_total means we are continuing a
        # persisted emulated sequence (including legacy 0.7.5-0.8.1 jobs).
        if pass_total is not None and max(1, int(pass_total)) > 1:
            repetition_mode = RepetitionMode.EMULATED
            physical_attempt_total = max(1, int(pass_total))
        elif requested_passes <= 1:
            repetition_mode = RepetitionMode.SINGLE
            physical_attempt_total = 1
        elif self.real.adapter.native_repetitions_supported(target_type, targets):
            repetition_mode = RepetitionMode.NATIVE
            physical_attempt_total = 1
        else:
            repetition_mode = RepetitionMode.EMULATED
            physical_attempt_total = requested_passes

        attempt = ExecutionAttempt.create(
            job_id=job.job_id,
            execution_mode=job.execution_mode,
            zone_ids=tuple(zone.zone_id for zone in selected),
            target_type=target_type,
            targets=targets,
            cleaning_params=job.cleaning_params,
            now=now,
            requested_passes=requested_passes,
            repetition_mode=repetition_mode,
            pass_index=pass_index,
            pass_total=physical_attempt_total,
        )
        if attempt.execution_mode is ExecutionMode.DRY_RUN and self._pending_fault:
            attempt.fault_injection = self._pending_fault
            self._pending_fault = None
        return attempt

    @staticmethod
    def _is_retryable_precommand_wait(attempt: ExecutionAttempt) -> bool:
        """Return whether a failed REAL start is only a late recoverable blocker.

        ``command_intent_at is None`` is the safety boundary: it proves the
        backend had not crossed the durable at-most-once barrier and therefore
        could not have emitted the physical start command.
        """
        return (
            attempt.execution_mode is ExecutionMode.REAL
            and attempt.state is ExecutionAttemptState.FAILED
            and attempt.command_intent_at is None
            and attempt.failure_reason in _RETRYABLE_PRECOMMAND_WAIT_REASONS
        )

    @staticmethod
    def _defer_precommand_attempt(
        job: JobInstance, attempt: ExecutionAttempt, now: datetime
    ) -> bool:
        """Roll a late pre-command WAIT condition back into normal Job WAIT.

        The backend has already restored any parameters changed during PREPARING.
        Remove the technical attempt so statistics/history do not count a
        command that was never sent, retain a bounded audit note, and return all
        affected logical zones to WAIT with the concrete blocker.
        """
        reason = str(attempt.failure_reason or "vacuum_busy")
        history = job.metadata.setdefault("precommand_deferred_starts", [])
        if not isinstance(history, list):
            history = []
            job.metadata["precommand_deferred_starts"] = history
        history.append({
            "at": now.isoformat(),
            "attempt_id": attempt.attempt_id,
            "reason": reason,
            "zone_ids": list(attempt.zone_ids),
            "detail": attempt.metadata.get("start_error"),
        })
        del history[:-20]

        job.execution_attempts.pop(attempt.attempt_id, None)
        changed = True
        for zone_id in attempt.zone_ids:
            zone = job.zone_runs.get(zone_id)
            if zone is None or zone.terminal:
                continue
            if zone.metadata.get("execution_attempt_id") == attempt.attempt_id:
                zone.metadata.pop("execution_attempt_id", None)
                zone.metadata.pop("execution_substate", None)
            zone.blockers = (reason,)
            zone.transition(ZoneJobState.WAIT, now)
            # STARTING here was only an internal pre-command reservation.  It
            # must not pollute execution timing or make the Job look started.
            if zone.actual_start is None:
                zone.starting_at = None

        if job.actual_start is None and not job.execution_attempts:
            job.starting_at = None
        job.transition(JobState.WAIT, now)
        job.blockers = tuple(
            sorted({blocker for zone in job.zone_runs.values() for blocker in zone.blockers})
        )
        job.current_blockers = job.blockers
        job.current_preflight_decision = "WAIT"
        return changed

    async def _start_attempt(self, job: JobInstance, attempt: ExecutionAttempt, now: datetime) -> bool:
        job.execution_attempts[attempt.attempt_id] = attempt
        for zone_id in attempt.zone_ids:
            zone = job.zone_runs[zone_id]
            zone.blockers = ()
            zone.transition(ZoneJobState.STARTING, now)
            zone.metadata["execution_attempt_id"] = attempt.attempt_id
            zone.metadata["requested_passes"] = attempt.requested_passes
            zone.metadata["repetition_mode"] = attempt.repetition_mode.value
            zone.metadata["pass_index"] = attempt.pass_index
            zone.metadata["pass_total"] = attempt.pass_total
        await self.backend_for(attempt.execution_mode).async_start(attempt, now)
        if self._is_retryable_precommand_wait(attempt):
            return self._defer_precommand_attempt(job, attempt, now)
        return self._sync_attempt_to_zones(job, attempt, now) or True

    async def async_start_ready_zones(
        self,
        job: JobInstance,
        ready: list[ZoneExecution],
        now: datetime,
        *,
        lease_available: bool,
    ) -> bool:
        if not ready or not lease_available:
            return False
        attempt = self.build_attempt(job, ready, now)
        if attempt is None:
            return False
        return await self._start_attempt(job, attempt, now)

    def _sync_attempt_to_zones(self, job: JobInstance, attempt: ExecutionAttempt, now: datetime) -> bool:
        changed = False
        for zone_id in attempt.zone_ids:
            zone = job.zone_runs.get(zone_id)
            if zone is None or zone.terminal:
                continue
            zone.metadata["execution_attempt_id"] = attempt.attempt_id
            zone.metadata["requested_passes"] = attempt.requested_passes
            zone.metadata["repetition_mode"] = attempt.repetition_mode.value
            zone.metadata["pass_index"] = attempt.pass_index
            zone.metadata["pass_total"] = attempt.pass_total
            zone.metadata["execution_substate"] = attempt.state.value
            if attempt.state in _ACTIVE_RUNNING_STATES:
                if attempt.start_confirmed_at is not None:
                    changed |= zone.transition(ZoneJobState.RUNNING, attempt.start_confirmed_at or now)
            elif attempt.state in _STARTING_STATES:
                changed |= zone.transition(ZoneJobState.STARTING, attempt.preparing_at or attempt.requested_at or now)
            elif attempt.state is ExecutionAttemptState.COMPLETED:
                if attempt.final_pass:
                    changed |= zone.finish(
                        ZoneResult.SUCCESS,
                        JobReason.EXECUTION_SUCCESS.value,
                        attempt.completed_at or now,
                    )
                else:
                    # Emulated repetitions keep the logical zone active while
                    # the next fallback physical command is created. Native
                    # repetitions always complete in this single attempt.
                    zone.metadata["completed_passes"] = attempt.pass_index
                    if zone.state is not ZoneJobState.RUNNING:
                        changed |= zone.transition(ZoneJobState.RUNNING, attempt.start_confirmed_at or now)
            elif attempt.state is ExecutionAttemptState.CANCELLED:
                changed |= zone.finish(
                    ZoneResult.FAILED,
                    JobReason.USER_CANCELLED.value,
                    attempt.completed_at or now,
                )
            elif attempt.state is ExecutionAttemptState.FAILED:
                changed |= zone.finish(
                    ZoneResult.FAILED,
                    attempt.failure_reason or JobReason.EXECUTION_FAILED.value,
                    attempt.completed_at or now,
                )
        return changed

    async def _async_start_next_pass(
        self, job: JobInstance, previous: ExecutionAttempt, now: datetime
    ) -> bool:
        if not previous.terminal or previous.state is not ExecutionAttemptState.COMPLETED:
            return False
        if previous.final_pass or previous.metadata.get("next_pass_attempt_id"):
            return False
        zones = [
            job.zone_runs[zone_id]
            for zone_id in previous.zone_ids
            if zone_id in job.zone_runs and not job.zone_runs[zone_id].terminal
        ]
        if not zones:
            return False
        next_attempt = self.build_attempt(
            job,
            zones,
            now,
            pass_index=previous.pass_index + 1,
            pass_total=previous.pass_total,
        )
        if next_attempt is None:
            return False
        # Carry the original pre-job parameter snapshot across emulated passes.
        # Only the final fallback command may restore the values observed before
        # the first command. Native repetitions never enter this path.
        for key in ("parameter_snapshot_before", "parameters_applied"):
            value = previous.metadata.get(key)
            if isinstance(value, dict):
                next_attempt.metadata[key] = dict(value)
        previous.metadata["next_pass_attempt_id"] = next_attempt.attempt_id
        await self._start_attempt(job, next_attempt, now)
        return True

    def _repair_stale_next_pass_marker(self, job: JobInstance) -> bool:
        """Clear a pass-link that was persisted without its successor attempt."""
        changed = False
        for attempt in job.execution_attempts.values():
            if attempt.state is not ExecutionAttemptState.COMPLETED or attempt.final_pass:
                continue
            next_id = attempt.metadata.get("next_pass_attempt_id")
            if next_id and str(next_id) not in job.execution_attempts:
                attempt.metadata.pop("next_pass_attempt_id", None)
                attempt.metadata["recovered_stale_next_pass_marker"] = str(next_id)
                changed = True
        return changed

    async def async_recover_job(
        self, job: JobInstance, now: datetime, *, precommand_abort_reason: str | None = None
    ) -> bool:
        """Reconcile persisted attempts without blindly reissuing commands.

        CREATED/PREPARING are the only states proving that the durable
        COMMAND_INTENT barrier was not crossed, so they may safely resume the
        normal start protocol. COMMAND_INTENT and every later non-terminal state
        are observation-only during recovery.
        """
        changed = self._repair_stale_next_pass_marker(job)
        for attempt in list(job.execution_attempts.values()):
            before = attempt.state
            if attempt.terminal:
                if attempt.execution_mode is ExecutionMode.REAL:
                    # The volume mute barrier may persist a terminal attempt before
                    # compare-and-restore has finished. Retry that idempotent restore
                    # after a restart, then restore any durable speaker snapshot.
                    if (
                        self.real.restore_previous_settings
                        and not attempt.metadata.get("parameter_restore_done")
                    ):
                        try:
                            await self.real.adapter.async_restore_parameters(attempt)
                        except Exception as err:
                            attempt.metadata["parameter_restore_error"] = f"{type(err).__name__}: {err}"
                        attempt.metadata["parameter_restore_done"] = True
                        changed = True
                    if attempt.metadata.get("pending_volume_restore"):
                        try:
                            await self.real.adapter.async_restore_pending_volume(attempt)
                            attempt.metadata.pop("volume_restore_error", None)
                        except Exception as err:
                            attempt.metadata["volume_restore_error"] = f"{type(err).__name__}: {err}"
                        changed = True
                changed |= self._sync_attempt_to_zones(job, attempt, now)
                continue

            backend = self.backend_for(attempt.execution_mode)
            if attempt.state in {
                ExecutionAttemptState.CREATED,
                ExecutionAttemptState.PREPARING,
            }:
                if precommand_abort_reason:
                    attempt.metadata["startup_precommand_aborted"] = precommand_abort_reason
                    if attempt.execution_mode is ExecutionMode.REAL:
                        await self.real._finish(
                            attempt, ExecutionAttemptState.FAILED, now, precommand_abort_reason
                        )
                    else:
                        attempt.state = ExecutionAttemptState.FAILED
                        attempt.failure_reason = precommand_abort_reason
                        attempt.completed_at = now
                    changed = True
                else:
                    attempt.metadata["startup_recovered_before_command"] = now.isoformat()
                    await backend.async_start(attempt, now)
                    changed = True
            else:
                # A legacy/corrupt snapshot can lose its confirmation timeout.
                # Rebuild only the deadline; never issue another physical start.
                if (
                    attempt.execution_mode is ExecutionMode.REAL
                    and attempt.start_confirmed_at is None
                    and attempt.state in {
                        ExecutionAttemptState.COMMAND_INTENT,
                        ExecutionAttemptState.START_REQUESTED,
                        ExecutionAttemptState.RECONCILING,
                    }
                    and attempt.expected_start_confirm_at is None
                ):
                    attempt.expected_start_confirm_at = instant_add(now, REAL_START_TIMEOUT)
                    attempt.metadata["startup_rebuilt_start_deadline"] = True
                    changed = True
                changed |= await backend.async_progress(attempt, now)

            changed |= before is not attempt.state
            # Always synchronize, including terminal attempts loaded from disk.
            changed |= self._sync_attempt_to_zones(job, attempt, now)

        # Recover the tiny crash window between completed emulated passes. Native
        # repetitions are one attempt and therefore have no successor command.
        for attempt in list(job.execution_attempts.values()):
            if attempt.state is ExecutionAttemptState.COMPLETED and not attempt.final_pass:
                changed |= await self._async_start_next_pass(job, attempt, now)
        return changed

    async def async_progress_job(self, job: JobInstance, now: datetime) -> bool:
        changed = self._repair_stale_next_pass_marker(job)
        # List snapshot because progression can materialize a subsequent pass.
        for attempt in list(job.execution_attempts.values()):
            if not attempt.terminal:
                backend = self.backend_for(attempt.execution_mode)
                changed |= await backend.async_progress(attempt, now)
                if (
                    attempt.execution_mode is ExecutionMode.REAL
                    and attempt.state is ExecutionAttemptState.ROBOT_SERVICE_BLOCKED
                    and instant_ge(now, job.deadline_at)
                ):
                    attempt.metadata["resource_blocked_deadline_at"] = job.deadline_at.isoformat()
                    await self.real._finish(
                        attempt,
                        ExecutionAttemptState.FAILED,
                        now,
                        JobReason.RESOURCE_BLOCKED_UNTIL_DEADLINE.value,
                    )
                    changed = True
            # Synchronize terminal attempts too. This repairs a crash between
            # technical completion and logical zone persistence.
            changed |= self._sync_attempt_to_zones(job, attempt, now)
            if attempt.state is ExecutionAttemptState.COMPLETED and not attempt.final_pass:
                changed |= await self._async_start_next_pass(job, attempt, now)
        return changed

    async def async_cancel_job(self, job: JobInstance, now: datetime) -> bool:
        changed = False
        for attempt in job.execution_attempts.values():
            if attempt.terminal:
                continue
            before = attempt.state
            await self.backend_for(attempt.execution_mode).async_cancel(attempt, now)
            changed |= before is not attempt.state
            changed |= self._sync_attempt_to_zones(job, attempt, now)
        return changed

    async def async_pause_job(self, job: JobInstance, now: datetime) -> bool:
        changed = False
        for attempt in self.active_attempts(job):
            if attempt.execution_mode is not ExecutionMode.REAL:
                continue
            await self.real.async_pause(attempt, now)
            changed = True
        return changed

    async def async_resume_job(self, job: JobInstance, now: datetime) -> bool:
        changed = False
        for attempt in self.active_attempts(job):
            if attempt.execution_mode is not ExecutionMode.REAL:
                continue
            await self.real.async_resume(attempt, now)
            changed = True
        return changed

    def next_transition_at(self, job: JobInstance) -> datetime | None:
        values = []
        for attempt in job.execution_attempts.values():
            if attempt.terminal:
                continue
            value = self.backend_for(attempt.execution_mode).next_transition_at(attempt)
            if value is not None:
                values.append(value)
        return min(values) if values else None
