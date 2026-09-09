"""Timezone-safe instant arithmetic for Vacuum Schedule.

Python's aware ``datetime`` uses wall-clock arithmetic when both operands share
one ``tzinfo`` object.  That is useful for calendar rules but wrong for elapsed
execution windows around DST transitions.  Scheduler runtime windows are
therefore always calculated and compared through UTC instants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def ensure_aware(value: datetime) -> None:
    """Raise when ``value`` does not identify an absolute instant."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime_must_be_timezone_aware")


def as_utc(value: datetime) -> datetime:
    """Return ``value`` represented as UTC without changing the instant."""
    ensure_aware(value)
    return value.astimezone(timezone.utc)


def instant_add(value: datetime, delta: timedelta) -> datetime:
    """Add elapsed time and return the result in the original timezone."""
    ensure_aware(value)
    zone = value.tzinfo
    return (as_utc(value) + delta).astimezone(zone)


def instant_delta(later: datetime, earlier: datetime) -> timedelta:
    """Return elapsed time between two aware datetimes."""
    return as_utc(later) - as_utc(earlier)


def instant_lt(left: datetime, right: datetime) -> bool:
    return as_utc(left) < as_utc(right)


def instant_le(left: datetime, right: datetime) -> bool:
    return as_utc(left) <= as_utc(right)


def instant_gt(left: datetime, right: datetime) -> bool:
    return as_utc(left) > as_utc(right)


def instant_ge(left: datetime, right: datetime) -> bool:
    return as_utc(left) >= as_utc(right)


def instant_eq(left: datetime, right: datetime) -> bool:
    return as_utc(left) == as_utc(right)
