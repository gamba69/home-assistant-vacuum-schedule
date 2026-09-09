"""Execution-layer models for Vacuum Schedule.

The scheduler owns business lifecycle (Job/ZoneExecution). ExecutionAttempt is
one persisted physical or dry-run command lifecycle. REAL attempts deliberately
persist COMMAND_INTENT before a device command can be emitted, making restart
reconciliation at-most-once with respect to physical start commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


class ExecutionMode(StrEnum):
    REAL = "REAL"
    DRY_RUN = "DRY_RUN"


class RepetitionMode(StrEnum):
    """How the requested cleaning passes are executed physically."""

    SINGLE = "SINGLE"
    NATIVE = "NATIVE"
    EMULATED = "EMULATED"


class ExecutionAttemptState(StrEnum):
    """Technical backend lifecycle; UI may aggregate these states."""

    CREATED = "CREATED"
    PREPARING = "PREPARING"
    COMMAND_INTENT = "COMMAND_INTENT"
    START_REQUESTED = "START_REQUESTED"
    START_CONFIRMED = "START_CONFIRMED"  # compatibility/transient alias
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ROBOT_SERVICE = "ROBOT_SERVICE"
    ROBOT_SERVICE_BLOCKED = "ROBOT_SERVICE_BLOCKED"
    ROBOT_ERROR_BLOCKED = "ROBOT_ERROR_BLOCKED"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptState.COMPLETED,
        ExecutionAttemptState.FAILED,
        ExecutionAttemptState.CANCELLED,
    }
)


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass(slots=True)
class ExecutionAttempt:
    """One persisted backend execution request."""

    attempt_id: str
    job_id: str
    execution_mode: ExecutionMode
    zone_ids: tuple[str, ...]
    target_type: str
    targets: tuple[str, ...]
    cleaning_params: dict[str, Any] = field(default_factory=dict)
    requested_passes: int = 1
    repetition_mode: RepetitionMode = RepetitionMode.SINGLE
    # pass_index/pass_total describe separate physical commands only when
    # repetitions have to be emulated. Native repetitions use one attempt.
    pass_index: int = 1
    pass_total: int = 1
    state: ExecutionAttemptState = ExecutionAttemptState.CREATED
    created_at: datetime | None = None
    preparing_at: datetime | None = None
    command_intent_at: datetime | None = None
    requested_at: datetime | None = None
    start_confirmed_at: datetime | None = None
    paused_at: datetime | None = None
    completion_candidate_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    completed_at: datetime | None = None
    last_observed_at: datetime | None = None
    runtime_error_at: datetime | None = None
    error_recovery_deadline_at: datetime | None = None
    expected_start_confirm_at: datetime | None = None
    expected_complete_at: datetime | None = None
    failure_reason: str | None = None
    fault_injection: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        execution_mode: ExecutionMode,
        zone_ids: tuple[str, ...],
        target_type: str,
        targets: tuple[str, ...],
        cleaning_params: Mapping[str, Any],
        now: datetime,
        requested_passes: int | None = None,
        repetition_mode: RepetitionMode | str | None = None,
        pass_index: int = 1,
        pass_total: int = 1,
    ) -> "ExecutionAttempt":
        requested = requested_passes
        if requested is None:
            try:
                requested = max(1, int(cleaning_params.get("passes", 1) or 1))
            except (TypeError, ValueError):
                requested = 1
        try:
            mode = RepetitionMode(str(repetition_mode)) if repetition_mode is not None else None
        except ValueError:
            mode = None
        total = max(1, int(pass_total))
        index = max(1, int(pass_index))
        if mode is None:
            # Callers that do not provide an explicit transport plan retain the
            # pre-0.8.2 safe behavior. Only ExecutionManager may opt into a
            # verified native repetition path.
            if requested > 1 or total > 1:
                mode = RepetitionMode.EMULATED
                total = max(total, int(requested))
            else:
                mode = RepetitionMode.SINGLE
        return cls(
            attempt_id=uuid4().hex,
            job_id=job_id,
            execution_mode=execution_mode,
            zone_ids=tuple(zone_ids),
            target_type=str(target_type),
            targets=tuple(targets),
            cleaning_params=dict(cleaning_params),
            requested_passes=max(1, int(requested)),
            repetition_mode=mode,
            pass_index=index,
            pass_total=total,
            created_at=now,
        )

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_ATTEMPT_STATES

    @property
    def final_pass(self) -> bool:
        return self.pass_index >= self.pass_total

    def stable_clean_percent(self) -> float | None:
        """Return monotonic robot progress for this physical attempt.

        Roborock can expose stale progress before the new cleaning starts and
        reset ``clean_percent`` to zero/null while post-clean dock service is
        still owned by the same attempt. Start at the first observed CLEANING
        sample and retain the highest valid percentage for this attempt.
        """
        raw_samples = self.metadata.get("statistics_observations")
        samples = raw_samples if isinstance(raw_samples, list) else []
        start_index = None
        for idx, sample in enumerate(samples):
            if not isinstance(sample, dict):
                continue
            if str(sample.get("phase") or "").upper() == "CLEANING" or str(sample.get("task_kind") or "").lower() in {"segment", "zone"}:
                start_index = idx
                break
        if start_index is None:
            return None
        values: list[float] = []
        for sample in samples[start_index:]:
            if not isinstance(sample, dict):
                continue
            try:
                value = float(sample.get("clean_percent"))
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 100.0:
                values.append(value)
        return max(values) if values else None

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "attempt_id": self.attempt_id,
            "job_id": self.job_id,
            "execution_mode": self.execution_mode.value,
            "zone_ids": list(self.zone_ids),
            "target_type": self.target_type,
            "targets": list(self.targets),
            "cleaning_params": dict(self.cleaning_params),
            "requested_passes": self.requested_passes,
            "repetition_mode": self.repetition_mode.value,
            "pass_index": self.pass_index,
            "pass_total": self.pass_total,
            "state": self.state.value,
            "created_at": iso(self.created_at),
            "preparing_at": iso(self.preparing_at),
            "command_intent_at": iso(self.command_intent_at),
            "requested_at": iso(self.requested_at),
            "start_confirmed_at": iso(self.start_confirmed_at),
            "paused_at": iso(self.paused_at),
            "completion_candidate_at": iso(self.completion_candidate_at),
            "cancel_requested_at": iso(self.cancel_requested_at),
            "completed_at": iso(self.completed_at),
            "last_observed_at": iso(self.last_observed_at),
            "runtime_error_at": iso(self.runtime_error_at),
            "error_recovery_deadline_at": iso(self.error_recovery_deadline_at),
            "expected_start_confirm_at": iso(self.expected_start_confirm_at),
            "expected_complete_at": iso(self.expected_complete_at),
            "failure_reason": self.failure_reason,
            "fault_injection": self.fault_injection,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionAttempt":
        raw_state = str(data.get("state", ExecutionAttemptState.CREATED.value))
        try:
            state = ExecutionAttemptState(raw_state)
        except ValueError:
            state = ExecutionAttemptState.RECONCILING
        cleaning_params = dict(data.get("cleaning_params", {}))
        requested_passes = max(
            1,
            int(data.get("requested_passes", cleaning_params.get("passes", 1)) or 1),
        )
        pass_index = max(1, int(data.get("pass_index", 1) or 1))
        pass_total = max(1, int(data.get("pass_total", cleaning_params.get("passes", 1)) or 1))
        raw_repetition_mode = data.get("repetition_mode")
        try:
            repetition_mode = RepetitionMode(str(raw_repetition_mode))
        except (TypeError, ValueError):
            # 0.7.5-0.8.1 represented repetitions as separate attempts. Keep
            # those persisted jobs emulated after upgrade; never reinterpret a
            # half-finished legacy sequence as a new native command.
            repetition_mode = (
                RepetitionMode.EMULATED
                if pass_total > 1
                else (RepetitionMode.NATIVE if requested_passes > 1 else RepetitionMode.SINGLE)
            )
        return cls(
            attempt_id=str(data.get("attempt_id", "")),
            job_id=str(data.get("job_id", "")),
            execution_mode=ExecutionMode(str(data.get("execution_mode", ExecutionMode.DRY_RUN.value))),
            zone_ids=tuple(str(item) for item in data.get("zone_ids", ())),
            target_type=str(data.get("target_type", "")),
            targets=tuple(str(item) for item in data.get("targets", ())),
            cleaning_params=cleaning_params,
            requested_passes=requested_passes,
            repetition_mode=repetition_mode,
            pass_index=pass_index,
            pass_total=pass_total,
            state=state,
            created_at=_dt(data.get("created_at")),
            preparing_at=_dt(data.get("preparing_at")),
            command_intent_at=_dt(data.get("command_intent_at")),
            requested_at=_dt(data.get("requested_at")),
            start_confirmed_at=_dt(data.get("start_confirmed_at")),
            paused_at=_dt(data.get("paused_at")),
            completion_candidate_at=_dt(data.get("completion_candidate_at")),
            cancel_requested_at=_dt(data.get("cancel_requested_at")),
            completed_at=_dt(data.get("completed_at")),
            last_observed_at=_dt(data.get("last_observed_at")),
            runtime_error_at=_dt(data.get("runtime_error_at")),
            error_recovery_deadline_at=_dt(data.get("error_recovery_deadline_at")),
            expected_start_confirm_at=_dt(data.get("expected_start_confirm_at")),
            expected_complete_at=_dt(data.get("expected_complete_at")),
            failure_reason=str(data["failure_reason"]) if data.get("failure_reason") else None,
            fault_injection=str(data["fault_injection"]) if data.get("fault_injection") else None,
            metadata=dict(data.get("metadata", {})),
        )
