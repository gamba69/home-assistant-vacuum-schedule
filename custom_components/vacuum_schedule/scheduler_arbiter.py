"""Deterministic runnable-job arbitration for Vacuum Schedule.

The arbiter is intentionally independent from Home Assistant and from pre-flight.
It receives only Jobs that are already runnable. WAIT Jobs with real blockers are
therefore absent from the candidate set and can never block another Job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

try:
    from .job import JobInstance, JobOrigin
except ImportError:  # pragma: no cover - isolated source-file testing
    from job import JobInstance, JobOrigin


def _epoch(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    return value.timestamp()


@dataclass(frozen=True, slots=True)
class ArbiterCandidate:
    """One pre-flight-PASS Job together with the logical zones ready to start."""

    job_id: str
    origin: JobOrigin
    planned_start: datetime
    manual_triggered_at: datetime | None
    manual_release_at: datetime | None
    ready_zone_ids: tuple[str, ...]
    candidate_class: str = "scheduled"
    force_priority: int = 0
    preempts_scheduled: bool = False

    @classmethod
    def from_job(
        cls,
        job: JobInstance,
        ready_zone_ids: Iterable[str],
        *,
        candidate_class: str | None = None,
        force_priority: int = 0,
        preempts_scheduled: bool = False,
    ) -> "ArbiterCandidate":
        if candidate_class is None:
            candidate_class = (
                "manual"
                if job.origin is JobOrigin.MANUAL
                or job.manual_triggered_at is not None
                or job.manual_release_at is not None
                else "scheduled"
            )
        return cls(
            job_id=job.job_id,
            origin=job.origin,
            planned_start=job.planned_start,
            manual_triggered_at=job.manual_triggered_at,
            manual_release_at=job.manual_release_at,
            ready_zone_ids=tuple(ready_zone_ids),
            candidate_class=str(candidate_class),
            force_priority=int(force_priority),
            preempts_scheduled=bool(preempts_scheduled),
        )


class SchedulerArbiter:
    """Choose exactly one runnable Job for the single execution lease.

    Ordering is deliberately global and deterministic:

    1. runnable Manual Jobs;
    2. if due Scheduled work exists, only Forced candidates with
       ``preempts_scheduled`` are allowed to compete ahead of it; among those
       candidates the highest numeric ``force_priority`` wins;
    3. due Scheduled work follows;
    4. non-preemptive Forced candidates follow Scheduled work, again ordered by
       descending numeric ``force_priority``;
    5. when no due Scheduled work exists, all Forced candidates compete
       together and the highest numeric ``force_priority`` wins regardless of
       ``preempts_scheduled``.

    The flag is therefore an eligibility boundary only when a runnable
    Scheduled Job is present.  Numeric priority is always evaluated inside the
    set of candidates that are allowed to compete in the current context.
    """

    @staticmethod
    def _manual_key(candidate: ArbiterCandidate) -> tuple[float, float, str]:
        manual_priority_at = candidate.manual_triggered_at or candidate.manual_release_at
        return (
            _epoch(manual_priority_at or candidate.planned_start),
            _epoch(candidate.planned_start),
            candidate.job_id,
        )

    @staticmethod
    def _scheduled_key(candidate: ArbiterCandidate) -> tuple[float, str]:
        return (_epoch(candidate.planned_start), candidate.job_id)

    @staticmethod
    def _forced_key(candidate: ArbiterCandidate) -> tuple[int, float, str]:
        return (
            -int(candidate.force_priority),
            _epoch(candidate.planned_start),
            candidate.job_id,
        )

    @classmethod
    def _is_manual(cls, candidate: ArbiterCandidate) -> bool:
        return bool(
            candidate.candidate_class == "manual"
            or candidate.origin is JobOrigin.MANUAL
            or candidate.manual_triggered_at is not None
            or candidate.manual_release_at is not None
        )

    def ordered(self, candidates: Iterable[ArbiterCandidate]) -> list[ArbiterCandidate]:
        """Return candidates in the same effective order used for selection."""
        items = list(candidates)
        manual = sorted((item for item in items if self._is_manual(item)), key=self._manual_key)
        forced = sorted(
            (item for item in items if not self._is_manual(item) and item.candidate_class == "forced"),
            key=self._forced_key,
        )
        scheduled = sorted(
            (
                item
                for item in items
                if not self._is_manual(item) and item.candidate_class != "forced"
            ),
            key=self._scheduled_key,
        )

        if manual:
            # Manual always wins globally.  Keep the remaining order useful for
            # diagnostics using the same Force-vs-Scheduled rule below.
            tail = self._ordered_non_manual(forced, scheduled)
            return [*manual, *tail]
        return self._ordered_non_manual(forced, scheduled)

    @staticmethod
    def _ordered_non_manual(
        forced: list[ArbiterCandidate],
        scheduled: list[ArbiterCandidate],
    ) -> list[ArbiterCandidate]:
        if not scheduled:
            # With no due Scheduled work, every eligible Force competes in one
            # numeric-priority pool regardless of the override flag.
            return list(forced)
        if not forced:
            return list(scheduled)

        # When due Scheduled work exists, ``preempts_scheduled`` is an
        # admission rule, not a numeric-priority bonus.  A lower-priority
        # preemptive Force may therefore run before a higher-priority normal
        # Force, because the normal Force is not allowed to compete ahead of
        # Scheduled work in this context.
        preemptive = [item for item in forced if item.preempts_scheduled]
        normal = [item for item in forced if not item.preempts_scheduled]
        return [*preemptive, *scheduled, *normal]

    def select(
        self,
        candidates: Iterable[ArbiterCandidate],
        *,
        lease_owner: tuple[str, str] | None,
    ) -> ArbiterCandidate | None:
        """Return the highest-priority runnable candidate if the lease is free."""
        if lease_owner is not None:
            return None
        ordered = self.ordered(candidates)
        return ordered[0] if ordered else None
