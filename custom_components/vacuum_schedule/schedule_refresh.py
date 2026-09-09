"""Explicit future schedule refresh policy.

A refresh rebuilds the future projection from the current Schedule definitions.
Historical consumption is ignored except for a still-future occurrence that was
already completed through early/FORCE execution, which remains consumed unless
the operator explicitly asks to restore early-executed jobs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .job import JobExecutionSource, JobInstance
from .planner import OccurrencePlanner
from .schedule import Occurrence, ScheduleDefinition
from .time_utils import instant_gt


@dataclass(frozen=True, slots=True)
class ScheduleRefreshDecision:
    """How explicit schedule refresh should treat an existing occurrence."""

    materialize: bool
    rearm: bool = False
    restored_early: bool = False


def decide_schedule_refresh_consumption(
    consumed: JobInstance | None,
    *,
    planned_start: datetime,
    now: datetime,
    restore_early_executed: bool,
) -> ScheduleRefreshDecision:
    """Return the refresh decision for one canonical occurrence.

    Unstarted active projections are removed before this policy is evaluated.
    Any remaining non-terminal occurrence has already crossed a protected
    execution boundary and must remain authoritative.

    Terminal history does *not* act as a cursor during an explicit refresh.
    The only historical consumption preserved by default is a future occurrence
    completed via FORCE/early execution. The operator may explicitly re-arm that
    occurrence via ``restore_early_executed``.
    """
    if consumed is None:
        return ScheduleRefreshDecision(materialize=True)

    if not consumed.terminal:
        return ScheduleRefreshDecision(materialize=False)

    is_future_early_execution = (
        consumed.execution_source is JobExecutionSource.FORCE
        and instant_gt(planned_start, now)
    )
    if is_future_early_execution:
        if not restore_early_executed:
            return ScheduleRefreshDecision(materialize=False)
        return ScheduleRefreshDecision(
            materialize=True,
            rearm=True,
            restored_early=True,
        )

    # Explicit refresh is a rebuild from Schedule+now, not continuation of the
    # old durable cursor. A historical skip/failure/expiry therefore does not
    # suppress a still-future canonical slot. Use a fresh re-arm generation so
    # the old terminal record stays auditable without violating occurrence
    # namespace uniqueness if the rebuilt Job later becomes terminal too.
    return ScheduleRefreshDecision(materialize=True, rearm=True)


def select_schedule_refresh_occurrence(
    planner: OccurrencePlanner,
    schedule: ScheduleDefinition,
    now: datetime,
    *,
    consumed_lookup: Callable[[Occurrence], JobInstance | None],
    restore_early_executed: bool,
    max_candidates: int = 64,
) -> tuple[Occurrence | None, JobInstance | None, bool]:
    """Select the canonical occurrence for an explicit refresh.

    This is intentionally independent from the normal scheduler cursor. It
    starts from the current Schedule definition at ``now`` and only advances
    when the refresh policy explicitly says the candidate remains consumed.
    """
    occurrence = planner.next_for_schedule(schedule, now, inclusive=True)
    for _ in range(max(1, int(max_candidates))):
        if occurrence is None:
            return None, None, False
        consumed = consumed_lookup(occurrence)
        decision = decide_schedule_refresh_consumption(
            consumed,
            planned_start=occurrence.planned_start,
            now=now,
            restore_early_executed=restore_early_executed,
        )
        if decision.materialize:
            return (
                occurrence,
                consumed if decision.rearm else None,
                decision.restored_early,
            )
        occurrence = planner.next_for_schedule(
            schedule, occurrence.planned_start, inclusive=False
        )
    return None, None, False
