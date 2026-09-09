"""DST-safe occurrence planning for Vacuum Schedule."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import heapq
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

try:
    from .schedule import Occurrence, ScheduleDefinition, make_occurrence_id
except ImportError:  # pragma: no cover - isolated source-file testing
    from schedule import Occurrence, ScheduleDefinition, make_occurrence_id

try:
    from .time_utils import as_utc, instant_add, instant_delta, instant_eq, instant_ge, instant_gt, instant_le, instant_lt
except ImportError:  # pragma: no cover - isolated source-file testing
    from time_utils import as_utc, instant_add, instant_delta, instant_eq, instant_ge, instant_gt, instant_le, instant_lt

_MAX_SCAN_DAYS = 366 * 20


def _ensure_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime_must_be_timezone_aware")


def _roundtrip_local(candidate: datetime, zone: ZoneInfo) -> datetime:
    return candidate.astimezone(timezone.utc).astimezone(zone)


def resolve_local_wall_datetime(
    local_date: date,
    local_time,
    zone: ZoneInfo,
) -> datetime:
    """Resolve a local wall-clock date/time deterministically across DST.

    Policy:
    - normal time: keep the requested wall clock;
    - ambiguous fall-back time: use the first fold (fold=0), yielding exactly
      one scheduled occurrence;
    - nonexistent spring-forward time: shift forward by the DST gap, preserving
      minute/second position within the skipped interval.
    """
    naive = datetime.combine(local_date, local_time)
    fold0 = naive.replace(tzinfo=zone, fold=0)
    fold1 = naive.replace(tzinfo=zone, fold=1)

    valid0 = _roundtrip_local(fold0, zone).replace(tzinfo=None) == naive
    valid1 = _roundtrip_local(fold1, zone).replace(tzinfo=None) == naive

    if valid0:
        return fold0
    if valid1:
        return fold1

    offset0 = fold0.utcoffset()
    offset1 = fold1.utcoffset()
    if offset0 is None or offset1 is None:
        raise ValueError("cannot_resolve_local_time")

    gap = offset1 - offset0
    if gap <= timedelta(0):
        gap = offset0 - offset1
    if gap <= timedelta(0):
        gap = timedelta(hours=1)

    shifted_naive = naive + gap
    shifted = shifted_naive.replace(tzinfo=zone, fold=0)
    if _roundtrip_local(shifted, zone).replace(tzinfo=None) != shifted_naive:
        # Defensive fallback for unusual transitions: advance minute-by-minute
        # to the first valid wall-clock instant.
        probe = naive
        for _ in range(24 * 60):
            probe += timedelta(minutes=1)
            candidate = probe.replace(tzinfo=zone, fold=0)
            if _roundtrip_local(candidate, zone).replace(tzinfo=None) == probe:
                return candidate
        raise ValueError("cannot_resolve_nonexistent_local_time")
    return shifted


class OccurrencePlanner:
    """Pure calendar planner for one Home Assistant timezone."""

    def __init__(self, timezone_name: str | ZoneInfo) -> None:
        self.zone = (
            timezone_name
            if isinstance(timezone_name, ZoneInfo)
            else ZoneInfo(timezone_name)
        )

    def _planned_start_on_date(
        self,
        schedule: ScheduleDefinition,
        local_date: date,
    ) -> datetime | None:
        if not schedule.enabled or not schedule.matches_date(local_date):
            return None
        return resolve_local_wall_datetime(
            local_date,
            schedule.local_time_for_date(local_date),
            self.zone,
        )

    def _next_planned_start(
        self,
        schedule: ScheduleDefinition,
        after: datetime,
        *,
        inclusive: bool = False,
    ) -> datetime | None:
        _ensure_aware(after)
        if not schedule.enabled:
            return None
        local_after = after.astimezone(self.zone)
        start_date = local_after.date()
        for offset in range(_MAX_SCAN_DAYS + 1):
            candidate = self._planned_start_on_date(
                schedule,
                start_date + timedelta(days=offset),
            )
            if candidate is None:
                continue
            if instant_gt(candidate, after) or (inclusive and instant_eq(candidate, after)):
                return candidate
        return None

    def _previous_planned_start(
        self,
        schedule: ScheduleDefinition,
        before: datetime,
        *,
        inclusive: bool = False,
    ) -> datetime | None:
        _ensure_aware(before)
        if not schedule.enabled:
            return None
        local_before = before.astimezone(self.zone)
        start_date = local_before.date()
        for offset in range(_MAX_SCAN_DAYS + 1):
            candidate = self._planned_start_on_date(
                schedule,
                start_date - timedelta(days=offset),
            )
            if candidate is None:
                continue
            if instant_lt(candidate, before) or (inclusive and instant_eq(candidate, before)):
                return candidate
        return None

    def _build_occurrence(
        self,
        schedule: ScheduleDefinition,
        planned_start: datetime,
    ) -> Occurrence:
        successor = self._next_planned_start(schedule, planned_start, inclusive=False)
        # Execution/pre-warning windows are elapsed durations, not wall-clock
        # arithmetic.  Calculate them through UTC so a DST transition never
        # shortens or lengthens the real allowed interval.
        deadline = instant_add(
            planned_start, timedelta(minutes=schedule.execution_window_minutes)
        )
        if successor is not None and instant_lt(successor, deadline):
            deadline = successor
        warning = instant_add(
            planned_start, -timedelta(minutes=schedule.prewarning_minutes)
        )
        return Occurrence(
            occurrence_id=make_occurrence_id(
                schedule.schedule_id,
                schedule.revision,
                planned_start,
            ),
            schedule_id=schedule.schedule_id,
            schedule_revision=schedule.revision,
            schedule_name=schedule.name,
            planned_start=planned_start,
            warning_at=warning,
            deadline_at=deadline,
            next_planned_start=successor,
            target_type=schedule.target_type,
            targets=schedule.targets,
            # Snapshot the effective parameters for the occurrence's own local
            # calendar date. An early/manual execution later must never switch
            # to the parameters of the day on which it physically starts.
            cleaning_params=schedule.effective_cleaning_params_for_date(
                planned_start.astimezone(self.zone).date()
            ),
            zone_execution_policy=schedule.zone_execution_policy,
            force_config=schedule.force_config(),
        )

    def next_for_schedule(
        self,
        schedule: ScheduleDefinition,
        after: datetime,
        *,
        inclusive: bool = False,
    ) -> Occurrence | None:
        """Return the next occurrence for one schedule."""
        planned = self._next_planned_start(schedule, after, inclusive=inclusive)
        if planned is None:
            return None
        return self._build_occurrence(schedule, planned)

    def previous_for_schedule(
        self,
        schedule: ScheduleDefinition,
        before: datetime,
        *,
        inclusive: bool = False,
    ) -> Occurrence | None:
        """Return the previous occurrence for one schedule."""
        planned = self._previous_planned_start(schedule, before, inclusive=inclusive)
        if planned is None:
            return None
        return self._build_occurrence(schedule, planned)

    def current_relevant_for_schedule(
        self,
        schedule: ScheduleDefinition,
        now: datetime,
    ) -> Occurrence | None:
        """Return the occurrence whose warning/deadline window contains now."""
        _ensure_aware(now)
        candidates: list[Occurrence] = []
        previous = self.previous_for_schedule(schedule, now, inclusive=True)
        upcoming = self.next_for_schedule(schedule, now, inclusive=True)
        for item in (previous, upcoming):
            if item is None:
                continue
            if instant_le(item.warning_at, now) and instant_le(now, item.deadline_at):
                candidates.append(item)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (abs(instant_delta(item.planned_start, now).total_seconds()), item.occurrence_id),
        )

    def between(
        self,
        schedules: Sequence[ScheduleDefinition],
        start: datetime,
        end: datetime,
    ) -> list[Occurrence]:
        """Return planned occurrences in the half-open interval [start, end)."""
        _ensure_aware(start)
        _ensure_aware(end)
        if instant_le(end, start):
            return []
        results: list[Occurrence] = []
        for schedule in schedules:
            if not schedule.enabled:
                continue
            cursor = start
            inclusive = True
            while True:
                occurrence = self.next_for_schedule(
                    schedule,
                    cursor,
                    inclusive=inclusive,
                )
                if occurrence is None or instant_ge(occurrence.planned_start, end):
                    break
                results.append(occurrence)
                cursor = occurrence.planned_start
                inclusive = False
        return sorted(
            results,
            key=lambda item: (
                as_utc(item.planned_start),
                item.schedule_id,
                item.schedule_revision,
            ),
        )

    def next_occurrence(
        self,
        schedules: Sequence[ScheduleDefinition],
        after: datetime,
        *,
        inclusive: bool = False,
    ) -> Occurrence | None:
        """Return the next occurrence across all schedules."""
        candidates = [
            item
            for schedule in schedules
            if (
                item := self.next_for_schedule(
                    schedule,
                    after,
                    inclusive=inclusive,
                )
            )
            is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda item: (as_utc(item.planned_start), item.schedule_id))

    def previous_occurrence(
        self,
        schedules: Sequence[ScheduleDefinition],
        before: datetime,
        *,
        inclusive: bool = False,
    ) -> Occurrence | None:
        """Return the previous occurrence across all schedules."""
        candidates = [
            item
            for schedule in schedules
            if (
                item := self.previous_for_schedule(
                    schedule,
                    before,
                    inclusive=inclusive,
                )
            )
            is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (as_utc(item.planned_start), item.schedule_id))

    def current_relevant(
        self,
        schedules: Sequence[ScheduleDefinition],
        now: datetime,
    ) -> Occurrence | None:
        """Return the most relevant active warning/validity window."""
        candidates = [
            item
            for schedule in schedules
            if (item := self.current_relevant_for_schedule(schedule, now)) is not None
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                abs(instant_delta(item.planned_start, now).total_seconds()),
                as_utc(item.planned_start),
                item.schedule_id,
            ),
        )

    def next_n(
        self,
        schedules: Sequence[ScheduleDefinition],
        after: datetime,
        count: int,
        *,
        inclusive: bool = False,
    ) -> list[Occurrence]:
        """Return the next N occurrences across all enabled schedules."""
        _ensure_aware(after)
        if count <= 0:
            return []

        schedule_by_id = {
            schedule.schedule_id: schedule
            for schedule in schedules
            if schedule.enabled
        }
        heap: list[tuple[datetime, str, Occurrence]] = []
        for schedule in schedule_by_id.values():
            occurrence = self.next_for_schedule(
                schedule,
                after,
                inclusive=inclusive,
            )
            if occurrence is not None:
                heapq.heappush(
                    heap,
                    (as_utc(occurrence.planned_start), schedule.schedule_id, occurrence),
                )

        result: list[Occurrence] = []
        while heap and len(result) < count:
            _, schedule_id, occurrence = heapq.heappop(heap)
            result.append(occurrence)
            schedule = schedule_by_id[schedule_id]
            successor = self.next_for_schedule(
                schedule,
                occurrence.planned_start,
                inclusive=False,
            )
            if successor is not None:
                heapq.heappush(
                    heap,
                    (as_utc(successor.planned_start), schedule_id, successor),
                )
        return result

    def preview(
        self,
        schedules: Sequence[ScheduleDefinition],
        now: datetime,
        count: int = 5,
    ) -> dict[str, object]:
        """Return the stage-0.3 calendar diagnostic preview."""
        _ensure_aware(now)
        previous = self.previous_occurrence(schedules, now, inclusive=False)
        current = self.current_relevant(schedules, now)
        upcoming = self.next_n(schedules, now, count, inclusive=True)
        next_item = upcoming[0] if upcoming else None
        return {
            "timezone": self.zone.key,
            "previous": previous.to_dict() if previous else None,
            "current_relevant": current.to_dict() if current else None,
            "next": next_item.to_dict() if next_item else None,
            "next_occurrences": [item.to_dict() for item in upcoming],
        }
