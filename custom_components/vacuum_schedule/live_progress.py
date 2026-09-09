"""Conservative live-progress interpolation from robot observations.

The values produced here are presentation hints only.  A rate is accepted only
when the newest counter value has genuinely advanced and that advance can be
measured across a sufficiently long observation span.  This deliberately
rejects the clustered Home Assistant updates that used to turn a one-percent
counter change into several percent per second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from statistics import median
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ObservedProgressRate:
    """One validated rate and the real observation anchoring it."""

    per_second: float
    anchor_at: datetime


def robust_observed_rate(
    samples: Iterable[Mapping[str, Any]],
    key: str,
    *,
    maximum: float,
    minimum_span_seconds: float = 10.0,
    window_seconds: float = 120.0,
) -> ObservedProgressRate | None:
    """Return a robust recent counter rate, or ``None`` when it is unsafe.

    The last two usable observations must show a forward-moving counter.  An
    unchanged latest value is a real plateau, so an older positive spike must
    not be re-anchored to a newer timestamp.  Candidate rates compare the
    newest point with every older point inside a bounded window; sub-second and
    other short bursts are excluded, and the median dampens one noisy sample.
    """

    points: list[tuple[datetime, float]] = []
    for sample in samples:
        try:
            value = float(sample.get(key))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        raw_stamp = sample.get("observed_at")
        if not raw_stamp:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw_stamp))
        except ValueError:
            continue
        points.append((stamp, value))

    if len(points) < 2:
        return None
    latest_at, latest = points[-1]
    previous_at, previous = points[-2]
    try:
        newest_span = (latest_at - previous_at).total_seconds()
    except TypeError:
        return None
    if newest_span <= 0 or latest <= previous:
        return None

    candidates: list[float] = []
    for older_at, older in points[:-1]:
        try:
            seconds = (latest_at - older_at).total_seconds()
        except TypeError:
            continue
        if seconds < minimum_span_seconds or seconds > window_seconds:
            continue
        delta = latest - older
        if delta <= 0:
            continue
        rate = delta / seconds
        if math.isfinite(rate) and 0 < rate <= maximum:
            candidates.append(rate)

    if not candidates:
        return None
    return ObservedProgressRate(float(median(candidates)), latest_at)
