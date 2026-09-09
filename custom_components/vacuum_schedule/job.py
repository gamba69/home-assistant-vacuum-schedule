"""Persistent scheduler job model.

This module intentionally has no Home Assistant imports so the state model can
be tested independently from the Home Assistant runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from uuid import uuid4

try:
    from .execution_models import ExecutionAttempt, ExecutionMode
    from .schedule import Occurrence
except ImportError:  # pragma: no cover - isolated source-file testing
    from execution_models import ExecutionAttempt, ExecutionMode
    from schedule import Occurrence


class JobState(StrEnum):
    """Lifecycle states of one materialized scheduled occurrence."""

    PLANNED = "PLANNED"
    WAIT = "WAIT"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class JobOrigin(StrEnum):
    """How a job was created."""

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    EXTERNAL = "EXTERNAL"


class JobExecutionSource(StrEnum):
    """How physical execution of a Job was initiated."""

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    FORCE = "FORCE"
    EXTERNAL = "EXTERNAL"


class JobResult(StrEnum):
    """Terminal aggregate result of a finished occurrence."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class JobReason(StrEnum):
    """Stable machine-readable terminal reason codes used by stage 0.4."""

    SIMULATED_SUCCESS = "simulated_success"  # legacy pre-0.7 history only
    EXECUTION_SUCCESS = "execution_success"
    EXECUTION_START_REJECTED = "execution_start_rejected"
    EXECUTION_START_TIMEOUT = "execution_start_timeout"
    EXECUTION_FAILED = "execution_failed"
    ROBOT_ERROR_RECOVERY_TIMEOUT = "robot_error_recovery_timeout"
    EXECUTION_LOST = "execution_lost"
    EXECUTION_PREPARATION_FAILED = "execution_preparation_failed"
    EXECUTION_VOLUME_MUTE_FAILED = "execution_volume_mute_failed"
    EXECUTION_VOLUME_RESTORE_FAILED = "execution_volume_restore_failed"
    EXECUTION_PREEMPTED_EXTERNAL = "execution_preempted_external"
    RESOURCE_BLOCKED_UNTIL_DEADLINE = "resource_blocked_until_deadline"
    ROBOT_TARGET_MISSING = "robot_target_missing"
    TARGET_MAP_NOT_ACTIVE = "target_map_not_active"
    DEADLINE_EXPIRED = "deadline_expired"
    DISPLACED_BY_NEXT_OCCURRENCE = "displaced_by_next_occurrence"
    GLOBAL_DISABLED = "global_disabled"
    GLOBAL_DISABLED_UNTIL = "global_disabled_until"
    SCHEDULE_PAUSED = "schedule_paused"
    PREFLIGHT_FAILED = "preflight_failed"
    SCHEDULE_CHANGED_AFTER_WARNING = "schedule_changed_after_warning"
    SCHEDULE_REMOVED_AFTER_WARNING = "schedule_removed_after_warning"
    SIMULATED_EXECUTION_FAILED = "simulated_execution_failed"
    PARTIAL_SUCCESS = "partial_success"
    NO_ZONE_SUCCEEDED = "no_zone_succeeded"
    USER_SKIPPED = "user_skipped"
    USER_CANCELLED = "user_cancelled"
    SCHEDULE_DISABLED_BY_USER = "schedule_disabled_by_user"
    MANUAL_RUN_FAILED = "manual_run_failed"
    MISSED_WHILE_OFFLINE = "missed_while_offline"


TERMINAL_STATES = frozenset({JobState.FINISHED})


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class ZoneJobState(StrEnum):
    PLANNED = "PLANNED"
    WAIT = "WAIT"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class ZoneResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(slots=True)
class ZoneExecution:
    """Independent lifecycle/result of one Scheduler Cleaning Zone in a job."""

    zone_id: str
    zone_name: str = ""
    robot_target_type: str = ""
    robot_target_id: str = ""
    state: ZoneJobState = ZoneJobState.PLANNED
    result: ZoneResult | None = None
    reason_code: str | None = None
    blockers: tuple[str, ...] = ()
    first_wait_at: datetime | None = None
    wait_cycle: int = 0
    starting_at: datetime | None = None
    actual_start: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state is ZoneJobState.FINISHED

    def transition(self, state: ZoneJobState, now: datetime) -> bool:
        if self.terminal or self.state is state:
            return False
        previous = self.state
        self.state = state
        if state is ZoneJobState.WAIT:
            # ``first_wait_at`` describes the current continuous WAIT interval,
            # while ``wait_cycle`` survives later state changes so notification
            # dedupe can distinguish a genuine zone WAIT re-entry.
            self.wait_cycle += 1
            self.first_wait_at = now
        elif previous is ZoneJobState.WAIT:
            self.first_wait_at = None
        if state is ZoneJobState.STARTING and self.starting_at is None:
            self.starting_at = now
        elif state is ZoneJobState.RUNNING and self.actual_start is None:
            self.actual_start = now
        return True

    def finish(self, result: ZoneResult, reason_code: str, now: datetime) -> bool:
        if self.terminal:
            return False
        self.state = ZoneJobState.FINISHED
        self.result = result
        self.reason_code = str(reason_code)
        self.blockers = ()
        self.finished_at = now
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id, "zone_name": self.zone_name,
            "robot_target_type": self.robot_target_type, "robot_target_id": self.robot_target_id,
            "state": self.state.value, "result": self.result.value if self.result else None,
            "reason_code": self.reason_code, "blockers": list(self.blockers),
            "first_wait_at": self.first_wait_at.isoformat() if self.first_wait_at else None,
            "wait_cycle": self.wait_cycle,
            "starting_at": self.starting_at.isoformat() if self.starting_at else None,
            "actual_start": self.actual_start.isoformat() if self.actual_start else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ZoneExecution":
        result = data.get("result")
        return cls(
            zone_id=str(data.get("zone_id", "")), zone_name=str(data.get("zone_name", "")),
            robot_target_type=str(data.get("robot_target_type", "")),
            robot_target_id=str(data.get("robot_target_id", "")),
            state=ZoneJobState(str(data.get("state", ZoneJobState.PLANNED.value))),
            result=ZoneResult(str(result)) if result else None,
            reason_code=str(data["reason_code"]) if data.get("reason_code") else None,
            blockers=tuple(str(item) for item in data.get("blockers", ())),
            first_wait_at=_parse_dt(data.get("first_wait_at")),
            wait_cycle=max(0, int(data.get("wait_cycle", 0))),
            starting_at=_parse_dt(data.get("starting_at")), actual_start=_parse_dt(data.get("actual_start")),
            finished_at=_parse_dt(data.get("finished_at")), metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class JobInstance:
    """Mutable runtime/persistent state for exactly one occurrence."""

    job_id: str
    occurrence_id: str
    schedule_id: str
    schedule_revision: int
    schedule_name: str
    created_at: datetime
    warning_at: datetime
    planned_start: datetime
    deadline_at: datetime
    next_planned_start: datetime | None
    target_type: str
    targets: tuple[str, ...]
    cleaning_params: dict[str, Any]
    origin: JobOrigin = JobOrigin.SCHEDULED
    manual_triggered_at: datetime | None = None
    manual_release_at: datetime | None = None
    requested_by: str | None = None
    user_action_history: list[dict[str, Any]] = field(default_factory=list)
    state: JobState = JobState.PLANNED
    result: JobResult | None = None
    reason_code: str | None = None
    blockers: tuple[str, ...] = ()
    current_blockers: tuple[str, ...] = ()
    current_preflight_decision: str | None = None
    advisory_blockers: tuple[str, ...] = ()
    advisory_preflight_decision: str | None = None
    advisory_checked_at: datetime | None = None
    first_wait_at: datetime | None = None
    wait_cycle: int = 0
    max_prestart_wait_seconds: float = 0.0
    last_state_change_at: datetime | None = None
    starting_at: datetime | None = None
    actual_start: datetime | None = None
    finished_at: datetime | None = None
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    # Compatibility flag retained in serialized pre-0.7 snapshots only. New code
    # uses execution_mode and never derives safety decisions from this boolean.
    simulation: bool = True
    transition_generation: int = 0
    zone_runs: dict[str, ZoneExecution] = field(default_factory=dict)
    execution_attempts: dict[str, ExecutionAttempt] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_occurrence(cls, occurrence: Occurrence, now: datetime) -> "JobInstance":
        """Materialize a new job from an occurrence snapshot."""
        return cls(
            job_id=uuid4().hex,
            occurrence_id=occurrence.occurrence_id,
            schedule_id=occurrence.schedule_id,
            schedule_revision=occurrence.schedule_revision,
            schedule_name=occurrence.schedule_name,
            created_at=now,
            warning_at=occurrence.warning_at,
            planned_start=occurrence.planned_start,
            deadline_at=occurrence.deadline_at,
            next_planned_start=occurrence.next_planned_start,
            target_type=occurrence.target_type,
            targets=tuple(occurrence.targets),
            cleaning_params=dict(occurrence.cleaning_params),
            origin=JobOrigin.SCHEDULED,
            last_state_change_at=now,
            execution_mode=ExecutionMode.DRY_RUN,
            simulation=True,
            zone_runs={zone_id: ZoneExecution(zone_id=zone_id) for zone_id in occurrence.targets},
            metadata={
                "zone_execution_policy": occurrence.zone_execution_policy,
                "force_snapshot": dict(occurrence.force_config),
            },
        )

    @property
    def execution_source(self) -> JobExecutionSource:
        """Return the user-facing source of the actual execution request.

        ``origin`` deliberately keeps occurrence provenance.  A scheduled
        occurrence released with Start now is still a scheduled occurrence,
        but its execution source is MANUAL.  Likewise Force keeps the original
        occurrence while recording a FORCE execution source.
        """
        if self.origin is JobOrigin.EXTERNAL:
            return JobExecutionSource.EXTERNAL
        if (
            self.origin is JobOrigin.MANUAL
            or self.manual_triggered_at is not None
            or self.manual_release_at is not None
        ):
            return JobExecutionSource.MANUAL
        force = self.metadata.get("force_execution")
        if isinstance(force, Mapping) and bool(force.get("committed", True)) and (
            force.get("selected_at") or force.get("start_requested_at") or force.get("attempt_ids")
        ):
            return JobExecutionSource.FORCE
        return JobExecutionSource.SCHEDULED

    @property
    def effective_start(self) -> datetime:
        """Return the earliest committed execution release for this occurrence.

        Manual release remains the strongest explicit override. A Force probe is
        read-only, but once an early ExecutionAttempt is committed its durable
        ``force_execution.selected_at`` releases the same Job for continued
        progressive execution before the original ``planned_start``.
        """
        if self.manual_release_at is not None:
            return self.manual_release_at
        force = self.metadata.get("force_execution")
        if isinstance(force, Mapping) and bool(force.get("committed", True)):
            released = _parse_dt(force.get("selected_at"))
            if released is not None and released < self.planned_start:
                return released
        return self.planned_start

    def record_user_action(
        self,
        action: str,
        now: datetime,
        source: str,
        user_id: str | None = None,
        *,
        recipient_id: str | None = None,
        actor_name: str | None = None,
    ) -> None:
        self.user_action_history.append({
            "action": str(action),
            "at": now.isoformat(),
            "source": str(source),
            "user_id": user_id,
            "recipient_id": recipient_id,
            "actor_name": actor_name,
        })
        self.user_action_history = self.user_action_history[-50:]

    @property
    def terminal(self) -> bool:
        """Return whether the lifecycle is immutable/terminal."""
        return self.state in TERMINAL_STATES

    def waited_before_start(self, threshold_seconds: int | float = 0) -> bool:
        """Return whether pre-start WAIT reached the configured significance threshold."""
        try:
            threshold = max(0.0, float(threshold_seconds or 0))
            waited = max(0.0, float(self.max_prestart_wait_seconds or 0.0))
        except (TypeError, ValueError):
            return False
        return self.actual_start is not None and waited > 0.0 and waited >= threshold

    def transition(self, state: JobState, now: datetime) -> None:
        """Apply a non-terminal lifecycle transition.

        ``first_wait_at`` is deliberately the start of the *current continuous*
        WAIT interval.  ``wait_cycle`` is the durable occurrence-level history
        used by notifications/audit to distinguish a later re-entry into WAIT.
        """
        if self.terminal:
            return
        if state is JobState.FINISHED:
            raise ValueError("finish_requires_result")
        if self.state is state:
            return
        previous = self.state
        self.state = state
        self.last_state_change_at = now
        self.transition_generation += 1
        if state is JobState.WAIT:
            self.wait_cycle += 1
            self.first_wait_at = now
        elif previous is JobState.WAIT:
            if self.first_wait_at is not None and self.actual_start is None:
                try:
                    waited = max(0.0, (now - self.first_wait_at).total_seconds())
                except TypeError:
                    waited = 0.0
                self.max_prestart_wait_seconds = max(self.max_prestart_wait_seconds, waited)
            self.first_wait_at = None
        if state is JobState.STARTING and self.starting_at is None:
            self.starting_at = now
        elif state is JobState.RUNNING and self.actual_start is None:
            self.actual_start = now

    def finish_unstarted_zones(self, reason_code: str, now: datetime) -> bool:
        """Fail only zone work that has not reached STARTING/RUNNING.

        Execution-window and successor boundaries limit permission to start;
        they are not cancellation signals for already-started work.
        """
        changed = False
        for zone_run in self.zone_runs.values():
            if zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT):
                changed |= zone_run.finish(ZoneResult.FAILED, reason_code, now)
        return changed

    def finish(
        self,
        result: JobResult,
        reason_code: str,
        now: datetime,
    ) -> bool:
        """Finish once and return whether this call changed the job."""
        if self.terminal:
            return False
        self.state = JobState.FINISHED
        self.result = result
        self.reason_code = str(reason_code)
        self.finished_at = now
        self.last_state_change_at = now
        self.blockers = ()
        self.transition_generation += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to Home Assistant storage-safe data."""
        return {
            "job_id": self.job_id,
            "occurrence_id": self.occurrence_id,
            "schedule_id": self.schedule_id,
            "schedule_revision": self.schedule_revision,
            "schedule_name": self.schedule_name,
            "created_at": self.created_at.isoformat(),
            "warning_at": self.warning_at.isoformat(),
            "planned_start": self.planned_start.isoformat(),
            "deadline_at": self.deadline_at.isoformat(),
            "next_planned_start": self.next_planned_start.isoformat() if self.next_planned_start else None,
            "target_type": self.target_type,
            "targets": list(self.targets),
            "cleaning_params": dict(self.cleaning_params),
            "origin": self.origin.value,
            "execution_source": self.execution_source.value,
            "manual_triggered_at": self.manual_triggered_at.isoformat() if self.manual_triggered_at else None,
            "manual_release_at": self.manual_release_at.isoformat() if self.manual_release_at else None,
            "requested_by": self.requested_by,
            "user_action_history": list(self.user_action_history),
            "state": self.state.value,
            "result": self.result.value if self.result else None,
            "reason_code": self.reason_code,
            "blockers": list(self.blockers),
            "current_blockers": list(self.current_blockers),
            "current_preflight_decision": self.current_preflight_decision,
            "advisory_blockers": list(self.advisory_blockers),
            "advisory_preflight_decision": self.advisory_preflight_decision,
            "advisory_checked_at": self.advisory_checked_at.isoformat() if self.advisory_checked_at else None,
            "first_wait_at": self.first_wait_at.isoformat() if self.first_wait_at else None,
            "wait_cycle": self.wait_cycle,
            "max_prestart_wait_seconds": self.max_prestart_wait_seconds,
            "last_state_change_at": self.last_state_change_at.isoformat() if self.last_state_change_at else None,
            "starting_at": self.starting_at.isoformat() if self.starting_at else None,
            "actual_start": self.actual_start.isoformat() if self.actual_start else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "execution_mode": self.execution_mode.value,
            "simulation": self.execution_mode is ExecutionMode.DRY_RUN,
            "transition_generation": self.transition_generation,
            "zone_runs": {key: value.to_dict() for key, value in self.zone_runs.items()},
            "execution_attempts": {key: value.to_dict() for key, value in self.execution_attempts.items()},
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobInstance":
        """Deserialize a previously persisted job."""
        result_raw = data.get("result")
        return cls(
            job_id=str(data["job_id"]),
            occurrence_id=str(data["occurrence_id"]),
            schedule_id=str(data["schedule_id"]),
            schedule_revision=int(data["schedule_revision"]),
            schedule_name=str(data.get("schedule_name", "")),
            created_at=_parse_dt(data["created_at"]),  # type: ignore[arg-type]
            warning_at=_parse_dt(data["warning_at"]),  # type: ignore[arg-type]
            planned_start=_parse_dt(data["planned_start"]),  # type: ignore[arg-type]
            deadline_at=_parse_dt(data["deadline_at"]),  # type: ignore[arg-type]
            next_planned_start=_parse_dt(data.get("next_planned_start")),
            target_type=str(data.get("target_type", "areas")),
            targets=tuple(str(item) for item in data.get("targets", ())),
            cleaning_params=dict(data.get("cleaning_params", {})),
            origin=JobOrigin(str(data.get("origin", JobOrigin.SCHEDULED.value))),
            manual_triggered_at=_parse_dt(data.get("manual_triggered_at")),
            manual_release_at=_parse_dt(data.get("manual_release_at")),
            requested_by=(str(data["requested_by"]) if data.get("requested_by") else None),
            user_action_history=[dict(item) for item in data.get("user_action_history", []) if isinstance(item, Mapping)],
            state=JobState(str(data.get("state", JobState.PLANNED.value))),
            result=JobResult(str(result_raw)) if result_raw else None,
            reason_code=str(data["reason_code"]) if data.get("reason_code") else None,
            blockers=tuple(str(item) for item in data.get("blockers", ())),
            current_blockers=tuple(str(item) for item in data.get("current_blockers", data.get("blockers", ()))),
            current_preflight_decision=(
                str(data["current_preflight_decision"])
                if data.get("current_preflight_decision")
                else None
            ),
            advisory_blockers=tuple(str(item) for item in data.get("advisory_blockers", ())),
            advisory_preflight_decision=(
                str(data["advisory_preflight_decision"])
                if data.get("advisory_preflight_decision")
                else None
            ),
            advisory_checked_at=_parse_dt(data.get("advisory_checked_at")),
            first_wait_at=(
                _parse_dt(data.get("first_wait_at"))
                if JobState(str(data.get("state", JobState.PLANNED.value))) is JobState.WAIT
                else None
            ),
            wait_cycle=int(
                data.get(
                    "wait_cycle",
                    1 if data.get("first_wait_at") else 0,
                )
            ),
            max_prestart_wait_seconds=max(0.0, float(data.get("max_prestart_wait_seconds", 0.0) or 0.0)),
            last_state_change_at=_parse_dt(data.get("last_state_change_at")),
            starting_at=_parse_dt(data.get("starting_at")),
            actual_start=_parse_dt(data.get("actual_start")),
            finished_at=_parse_dt(data.get("finished_at")),
            execution_mode=ExecutionMode(str(data.get("execution_mode", ExecutionMode.DRY_RUN.value if data.get("simulation", True) else ExecutionMode.REAL.value))),
            simulation=(str(data.get("execution_mode", ExecutionMode.DRY_RUN.value if data.get("simulation", True) else ExecutionMode.REAL.value)) == ExecutionMode.DRY_RUN.value),
            transition_generation=int(data.get("transition_generation", 0)),
            zone_runs={
                str(key): ZoneExecution.from_dict(value)
                for key, value in dict(data.get("zone_runs", {})).items()
                if isinstance(value, Mapping)
            },
            execution_attempts={
                str(key): ExecutionAttempt.from_dict(value)
                for key, value in dict(data.get("execution_attempts", {})).items()
                if isinstance(value, Mapping)
            },
            metadata=dict(data.get("metadata", {})),
        )
