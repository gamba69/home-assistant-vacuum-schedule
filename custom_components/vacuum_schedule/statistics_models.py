"""Long-term statistics models and aggregation helpers for Vacuum Schedule.

The statistics layer is deliberately independent from Home Assistant storage.
It consumes immutable terminal Job snapshots plus their lifecycle trace and
produces JSON-friendly ledger records. Aggregates are always derived from the
ledger and may be rebuilt at any time.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

try:
    from .cleaning_scope import cleaning_scope
except ImportError:  # pragma: no cover - direct-module test imports
    from cleaning_scope import cleaning_scope


STATISTICS_SCHEMA_VERSION = 2

def execution_source_for_job(job: Mapping[str, Any]) -> str:
    """Derive actual execution source without changing occurrence provenance."""
    explicit = str(job.get("execution_source") or "").upper()
    if explicit in {"SCHEDULED", "MANUAL", "FORCE", "EXTERNAL"}:
        return explicit
    origin = str(job.get("origin") or "SCHEDULED").upper()
    if origin == "EXTERNAL":
        return "EXTERNAL"
    if origin == "MANUAL" or job.get("manual_triggered_at") or job.get("manual_release_at"):
        return "MANUAL"
    metadata = dict(job.get("metadata") or {})
    force = metadata.get("force_execution")
    if isinstance(force, Mapping) and bool(force.get("committed", True)) and (
        force.get("selected_at") or force.get("start_requested_at") or force.get("attempt_ids")
    ):
        return "FORCE"
    return "SCHEDULED"

_DURATION_PARAMETER_KEYS = ("cleaning_mode", "cleaning_route", "mop_mode", "fan_mode", "water_mode", "passes")
_PROFILE_PARAMETER_KEYS = _DURATION_PARAMETER_KEYS


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _seconds(start: Any, end: Any) -> float | None:
    left, right = _dt(start), _dt(end)
    if left is None or right is None:
        return None
    try:
        return max(0.0, (right - left).total_seconds())
    except TypeError:
        return None


def _number(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * max(0.0, min(1.0, percentile))
    low = int(rank)
    high = min(len(ordered) - 1, low + 1)
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def distribution(
    values: Iterable[float | int | None],
    percentile_number: int | float = 90,
) -> dict[str, Any]:
    """Return a distribution plus the user-selected presentation percentile.

    Fixed P50/P90/P95 values remain for backwards compatibility and historical
    diagnostics, while ``percentile``/``percentile_number`` are the values the
    current UI should present for the relevant metric policy.
    """
    clean = [float(value) for value in values if value is not None]
    try:
        selected = max(0, min(100, int(percentile_number)))
    except (TypeError, ValueError):
        selected = 90
    selected_value = _percentile(clean, selected / 100.0) if clean else None
    if not clean:
        return {
            "count": 0, "mean": None, "median": None,
            "p50": None, "p90": None, "p95": None,
            "percentile": None, "percentile_number": selected,
        }
    return {
        "count": len(clean),
        "mean": sum(clean) / len(clean),
        "median": median(clean),
        "p50": _percentile(clean, 0.50),
        "p90": _percentile(clean, 0.90),
        "p95": _percentile(clean, 0.95),
        "percentile": selected_value,
        "percentile_number": selected,
    }


def _duration_zone_signature(zone_ids: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in zone_ids if str(value)}))


def _duration_parameter_signature(params: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    values = dict(params or {})
    return tuple(
        (key, str(values.get(key)))
        for key in _DURATION_PARAMETER_KEYS
        if values.get(key) not in (None, "", "__none__")
    )


def build_force_duration_profiles(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an in-memory REAL-only duration model for Force admission.

    Only successful REAL Jobs are allowed to train whole-job profiles.  Zone
    profiles may also use successful zones from otherwise partial Jobs, because
    those zone durations represent completed physical work.  Failed or Dry-run
    data must never make a safety decision look more optimistic.
    """
    exact: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    zones_only: dict[tuple[str, ...], list[float]] = defaultdict(list)
    zone_exact: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    zone_any: dict[str, list[float]] = defaultdict(list)
    real_records = 0

    for record in records:
        if str(record.get("execution_mode") or "").upper() != "REAL":
            continue
        real_records += 1
        params = _duration_parameter_signature(record.get("cleaning_params_snapshot") or {})
        zones = [dict(item) for item in record.get("zones") or [] if isinstance(item, Mapping)]
        zone_ids = _duration_zone_signature(
            item.get("zone_id") for item in zones if str(item.get("result") or "") == "SUCCESS"
        )
        duration = _number((record.get("time") or {}).get("physical_execution_seconds"))
        if str(record.get("result") or "") == "SUCCESS" and zone_ids and duration is not None and duration > 0:
            exact[(zone_ids, params)].append(duration)
            zones_only[zone_ids].append(duration)

        for zone in zones:
            if str(zone.get("result") or "") != "SUCCESS":
                continue
            zone_id = str(zone.get("zone_id") or "")
            zone_duration = _number(zone.get("attributed_cleaning_seconds"))
            if not zone_id or zone_duration is None or zone_duration <= 0:
                continue
            zone_exact[(zone_id, params)].append(zone_duration)
            zone_any[zone_id].append(zone_duration)

    return {
        "real_record_count": real_records,
        "exact": exact,
        "zones_only": zones_only,
        "zone_exact": zone_exact,
        "zone_any": zone_any,
    }


def estimate_force_duration(
    profiles: Mapping[str, Any] | None,
    *,
    zone_ids: Iterable[Any],
    cleaning_params: Mapping[str, Any] | None,
    minimum_samples: int = 3,
) -> dict[str, Any]:
    """Return a conservative P90 estimate for the physical Force work.

    Preference order is exact zone-set + parameters, then a sum of per-zone
    P90 values with the same parameters, then broader same-zone historical
    data.  A result with fewer than ``minimum_samples`` is deliberately treated
    as unavailable so Scheduler can fall back to the agreed 30-minute gap.
    """
    data = dict(profiles or {})
    zones = _duration_zone_signature(zone_ids)
    params = _duration_parameter_signature(cleaning_params)
    minimum = max(1, int(minimum_samples))

    def from_values(values: Sequence[float], basis: str) -> dict[str, Any] | None:
        if len(values) < minimum:
            return None
        dist = distribution(values)
        p90 = _number(dist.get("p90"))
        if p90 is None or p90 <= 0:
            return None
        return {
            "available": True,
            "basis": basis,
            "sample_count": len(values),
            "minimum_samples": minimum,
            "p90_seconds": p90,
            "distribution": dist,
        }

    exact_values = list((data.get("exact") or {}).get((zones, params), ()))
    result = from_values(exact_values, "zones_and_parameters")
    if result is not None:
        return result

    if zones:
        per_zone_exact: list[tuple[str, list[float]]] = [
            (zone_id, list((data.get("zone_exact") or {}).get((zone_id, params), ())))
            for zone_id in zones
        ]
        if all(len(values) >= minimum for _, values in per_zone_exact):
            parts = [distribution(values) for _, values in per_zone_exact]
            p90 = sum(float(part.get("p90") or 0.0) for part in parts)
            if p90 > 0:
                return {
                    "available": True,
                    "basis": "zone_composite_and_parameters",
                    "sample_count": min(len(values) for _, values in per_zone_exact),
                    "minimum_samples": minimum,
                    "p90_seconds": p90,
                    "zone_samples": {
                        zone_id: {"count": len(values), "p90_seconds": distribution(values).get("p90")}
                        for zone_id, values in per_zone_exact
                    },
                }

    zones_values = list((data.get("zones_only") or {}).get(zones, ()))
    result = from_values(zones_values, "zones")
    if result is not None:
        return result

    if zones:
        per_zone_any: list[tuple[str, list[float]]] = [
            (zone_id, list((data.get("zone_any") or {}).get(zone_id, ())))
            for zone_id in zones
        ]
        if all(len(values) >= minimum for _, values in per_zone_any):
            parts = [distribution(values) for _, values in per_zone_any]
            p90 = sum(float(part.get("p90") or 0.0) for part in parts)
            if p90 > 0:
                return {
                    "available": True,
                    "basis": "zone_composite",
                    "sample_count": min(len(values) for _, values in per_zone_any),
                    "minimum_samples": minimum,
                    "p90_seconds": p90,
                    "zone_samples": {
                        zone_id: {"count": len(values), "p90_seconds": distribution(values).get("p90")}
                        for zone_id, values in per_zone_any
                    },
                }

    return {
        "available": False,
        "basis": "insufficient_real_history",
        "sample_count": len(exact_values),
        "minimum_samples": minimum,
        "p90_seconds": None,
    }


def _observation_samples(attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = dict(attempt.get("metadata") or {})
    raw = metadata.get("statistics_observations") or []
    samples = [dict(item) for item in raw if isinstance(item, Mapping)]
    if not samples:
        for key in ("start_observation", "last_observation"):
            item = metadata.get(key)
            if isinstance(item, Mapping):
                samples.append(dict(item))
    samples.sort(key=lambda item: str(item.get("observed_at") or ""))
    # De-duplicate exact observation timestamps produced by restart recovery.
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample in samples:
        marker = str(sample.get("observed_at") or "")
        if marker and marker in seen:
            continue
        if marker:
            seen.add(marker)
        result.append(sample)
    return result


def _area_counter_hint(samples: Sequence[Mapping[str, Any]]) -> str:
    """Classify historical area telemetry by the entity that supplied it.

    Version 0.10.10 exposed an older discovery bug where the Roborock lifetime
    ``total_cleaning_area`` entity could be recorded under the logical
    ``cleaning_area`` key.  Durable observations retain the source entity id,
    allowing most affected history to be repaired deterministically.  Renamed
    entity ids remain ``unknown`` and are handled conservatively by delta logic.
    """
    hints: set[str] = set()
    for sample in samples:
        sources = sample.get("source_entities")
        if not isinstance(sources, Mapping):
            continue
        entity_id = str(sources.get("cleaning_area") or "").lower()
        if not entity_id:
            continue
        if "total_cleaning_area" in entity_id:
            hints.add("lifetime")
        elif "cleaning_area" in entity_id:
            hints.add("session")
    if len(hints) == 1:
        return next(iter(hints))
    if len(hints) > 1:
        return "mixed"
    return "unknown"


def _positive_counter_delta(values: Sequence[float], baseline: float | None) -> float | None:
    """Return conservative observed growth of a cumulative counter."""
    if not values:
        return None
    origin = baseline if baseline is not None else values[0]
    moved = any(abs(value - origin) > 0.05 for value in values)
    if not moved:
        return 0.0 if max(values) <= 0.05 else None
    # Use the highest observed value relative to the known/preferred origin,
    # rather than summing every positive tick.  Small telemetry jitter therefore
    # cannot double-count the same lifetime-counter growth.
    return max(0.0, max(values) - origin)


def _session_area_metric(
    samples: Sequence[Mapping[str, Any]], started_at: Any
) -> tuple[float | None, dict[str, Any]]:
    """Return cleaned area without confusing session and lifetime counters.

    There are two materially different counter shapes in Roborock telemetry:

    * ``cleaning_area`` is cumulative only for the current/last run and normally
      resets near the start of a new run;
    * ``total_cleaning_area`` is a lifetime cumulative counter and never resets
      for a normal cleaning run.

    An old entity-discovery suffix collision could feed the second counter into
    observations labelled ``cleaning_area_m2``.  Therefore a no-reset stream is
    never interpreted as "area equals the largest absolute value" unless its
    durable source proves it is the per-session sensor.  Unknown historical
    sources use positive deltas, which may conservatively undercount a missed
    first sample but cannot turn a lifetime total into thousands of m² per Job.
    """
    epsilon = 0.05
    reset_threshold = 1.0
    start = _dt(started_at)
    source_hint = _area_counter_hint(samples)
    numeric: list[tuple[datetime | None, float]] = []
    for sample in samples:
        value = _number(sample.get("cleaning_area_m2"))
        if value is None:
            continue
        numeric.append((_dt(sample.get("observed_at")), max(0.0, value)))
    if not numeric:
        return None, {
            "method": "unavailable",
            "sample_count": 0,
            "counter_hint": source_hint,
        }

    baseline: float | None = None
    session_values: list[float] = []
    split_supported = start is not None and any(at is not None for at, _value in numeric)
    if split_supported:
        try:
            for at, value in numeric:
                if at is None:
                    continue
                if at < start:
                    baseline = value
                else:
                    session_values.append(value)
        except TypeError:
            # Old snapshots can mix naive and aware timestamps.  Lose the exact
            # boundary rather than making an unsafe absolute-counter inference.
            baseline = None
            session_values = [value for _at, value in numeric]
    else:
        session_values = [value for _at, value in numeric]

    if not session_values:
        return None, {
            "method": "no_session_samples",
            "sample_count": len(numeric),
            "baseline_m2": baseline,
            "counter_hint": source_hint,
        }

    # A lifetime counter must always be interpreted by increments, irrespective
    # of its absolute magnitude.  This is the direct repair path for 0.10.10's
    # 178k-m² style corruption.
    if source_hint == "lifetime":
        area = _positive_counter_delta(session_values, baseline)
        return area, {
            "method": "lifetime_counter_delta",
            "sample_count": len(session_values),
            "baseline_m2": baseline,
            "counter_hint": source_hint,
        }

    boundary_reset = baseline is not None and session_values[0] < baseline - reset_threshold
    first_drop: int | None = None
    for index, (left, right) in enumerate(zip(session_values, session_values[1:])):
        if right < left - reset_threshold:
            first_drop = index + 1
            break

    values = list(session_values)
    discarded_stale_prefix = False
    if not boundary_reset and first_drop is not None:
        # A post-start drop proves the prefix still belonged to the previous
        # session.  Start accounting at the new counter segment.
        values = values[first_drop:]
        discarded_stale_prefix = True

    if not values:
        return None, {
            "method": "unavailable",
            "sample_count": len(session_values),
            "counter_hint": source_hint,
        }

    moved = any(abs(right - left) > epsilon for left, right in zip(values, values[1:]))
    if len(values) == 1 or not moved:
        area = 0.0 if max(values) <= epsilon else None
        return area, {
            "method": "flat_zero" if area == 0.0 else "flat_nonzero_ambiguous",
            "sample_count": len(session_values),
            "baseline_m2": baseline,
            "boundary_reset": boundary_reset,
            "discarded_stale_prefix": discarded_stale_prefix,
            "counter_hint": source_hint,
        }

    # A reset proves session-counter semantics.  A source id that explicitly
    # names the current cleaning-area entity is also safe to interpret as a
    # session counter even when polling missed the exact zero sample.
    if boundary_reset or discarded_stale_prefix or source_hint == "session":
        segment_maxima: list[float] = []
        current_max = values[0]
        previous = values[0]
        for value in values[1:]:
            if value < previous - reset_threshold:
                segment_maxima.append(current_max)
                current_max = value
            else:
                current_max = max(current_max, value)
            previous = value
        segment_maxima.append(current_max)
        area = sum(segment_maxima)
        if area <= epsilon:
            area = 0.0
        return area, {
            "method": "session_cumulative_segments",
            "sample_count": len(session_values),
            "baseline_m2": baseline,
            "boundary_reset": boundary_reset,
            "discarded_stale_prefix": discarded_stale_prefix,
            "segment_count": len(segment_maxima),
            "segment_maxima_m2": segment_maxima,
            "counter_hint": source_hint,
        }

    # Unknown/mixed historical sources with no visible reset are deliberately
    # delta-only.  This is conservative for a session sensor whose zero sample
    # was missed, but it prevents a lifetime value such as 3,800 m² from being
    # charged wholesale to one cleaning Job.
    area = _positive_counter_delta(session_values, baseline)
    return area, {
        "method": "unknown_counter_positive_delta",
        "sample_count": len(session_values),
        "baseline_m2": baseline,
        "boundary_reset": boundary_reset,
        "discarded_stale_prefix": discarded_stale_prefix,
        "counter_hint": source_hint,
    }


def attempt_metrics(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Extract observed time/area/battery/service facts from one attempt."""
    samples = _observation_samples(attempt)
    batteries = [
        (str(sample.get("observed_at") or ""), _number(sample.get("battery_percent")))
        for sample in samples
    ]
    batteries = [(at, value) for at, value in batteries if value is not None]

    battery_start = batteries[0][1] if batteries else None
    battery_end = batteries[-1][1] if batteries else None
    battery_drop = None
    if battery_start is not None and battery_end is not None:
        battery_drop = max(0.0, battery_start - battery_end)

    charge_gain = 0.0
    charge_seconds = 0.0
    for (left_at, left), (right_at, right) in zip(batteries, batteries[1:]):
        if right > left:
            charge_gain += right - left
            duration = _seconds(left_at, right_at)
            if duration is not None:
                charge_seconds += duration

    started_at = attempt.get("start_confirmed_at") or attempt.get("requested_at")
    area, area_diagnostics = _session_area_metric(samples, started_at)

    pause_seconds = 0.0
    pause_count = 0
    service_seconds = 0.0
    wash_events: list[dict[str, Any]] = []
    previous_phase = None
    previous_status = None
    for index, sample in enumerate(samples):
        phase = str(sample.get("phase") or "")
        status = str(sample.get("vendor_status") or "")
        if phase == "PAUSED" and previous_phase != "PAUSED":
            pause_count += 1
        if status == "washing_the_mop" and previous_status != "washing_the_mop":
            wash_events.append(
                {
                    "at": sample.get("observed_at"),
                    "wash_mode": sample.get("wash_mode"),
                    "smart_wash": sample.get("smart_wash"),
                    "wash_interval": sample.get("wash_interval"),
                    "observation": "direct",
                }
            )
        if index + 1 < len(samples):
            duration = _seconds(sample.get("observed_at"), samples[index + 1].get("observed_at")) or 0.0
            if phase == "PAUSED":
                pause_seconds += duration
            if phase in {"SERVICE", "SERVICE_BLOCKED"}:
                service_seconds += duration
        previous_phase = phase
        previous_status = status

    completed_at = attempt.get("completed_at")
    return {
        "duration_seconds": _seconds(started_at, completed_at),
        "pause_seconds": pause_seconds,
        "pause_count": pause_count,
        "service_seconds": service_seconds,
        "battery_start_percent": battery_start,
        "battery_end_percent": battery_end,
        "battery_drop_percent": battery_drop,
        "charge_gain_percent": charge_gain or 0.0,
        "charge_seconds": charge_seconds,
        "area_m2": area,
        "area_source": "robot_session_counter" if area is not None else "unavailable",
        "area_diagnostics": area_diagnostics,
        "wash_events": wash_events,
        "observation_count": len(samples),
    }


def wait_metrics(events: Sequence[Mapping[str, Any]], finished_at: Any) -> dict[str, Any]:
    """Reconstruct Job WAIT intervals from semantic lifecycle events."""
    ordered = sorted(
        (dict(event) for event in events),
        key=lambda item: str(item.get("at") or item.get("scheduler_at") or ""),
    )
    wait_start: datetime | None = None
    wait_blockers: tuple[str, ...] = ()
    intervals: list[dict[str, Any]] = []
    previous_state: str | None = None
    for event in ordered:
        state = str(event.get("state") or "")
        at = _dt(event.get("at") or event.get("scheduler_at"))
        if at is None:
            continue
        if state == "WAIT" and previous_state != "WAIT":
            wait_start = at
            wait_blockers = tuple(str(item) for item in (event.get("current_blockers") or event.get("blockers") or []))
        elif previous_state == "WAIT" and state != "WAIT" and wait_start is not None:
            intervals.append(
                {
                    "start": wait_start.isoformat(),
                    "end": at.isoformat(),
                    "duration_seconds": max(0.0, (at - wait_start).total_seconds()),
                    "blockers": list(wait_blockers),
                }
            )
            wait_start = None
            wait_blockers = ()
        previous_state = state
    end = _dt(finished_at)
    if wait_start is not None and end is not None:
        intervals.append(
            {
                "start": wait_start.isoformat(),
                "end": end.isoformat(),
                "duration_seconds": max(0.0, (end - wait_start).total_seconds()),
                "blockers": list(wait_blockers),
            }
        )
    by_reason: dict[str, float] = defaultdict(float)
    for interval in intervals:
        blockers = interval["blockers"] or ["unknown_wait"]
        share = float(interval["duration_seconds"]) / max(1, len(blockers))
        for blocker in blockers:
            by_reason[str(blocker)] += share
    return {
        "count": len(intervals),
        "total_seconds": sum(float(item["duration_seconds"]) for item in intervals),
        "by_reason_seconds": dict(sorted(by_reason.items())),
        "intervals": intervals,
    }


def _target_groups(attempt: Mapping[str, Any], zones: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for zone_id in attempt.get("zone_ids") or []:
        zone = zones.get(str(zone_id), {})
        key = (
            str(zone.get("robot_target_type") or attempt.get("target_type") or ""),
            str(zone.get("robot_target_id") or ""),
        )
        if not key[1]:
            # Older snapshots may not contain zone mapping. Preserve the logical
            # relationship but never fabricate a target id.
            key = (key[0], f"unknown:{zone_id}")
        groups[key].append(str(zone_id))
    return [
        {
            "target_type": key[0],
            "target_id": key[1],
            "logical_zone_ids": owner_ids,
        }
        for key, owner_ids in groups.items()
    ]


def build_statistics_record(
    job: Mapping[str, Any],
    lifecycle_events: Sequence[Mapping[str, Any]],
    *,
    recorded_at: datetime,
) -> dict[str, Any]:
    """Create one immutable ledger row from a terminal Job snapshot."""
    zone_map = {
        str(zone_id): dict(value)
        for zone_id, value in dict(job.get("zone_runs") or {}).items()
        if isinstance(value, Mapping)
    }
    attempts = [
        dict(value)
        for value in dict(job.get("execution_attempts") or {}).values()
        if isinstance(value, Mapping)
    ]
    attempts.sort(key=lambda item: str(item.get("created_at") or ""))

    execution_rows: list[dict[str, Any]] = []
    total_physical_area = 0.0
    total_cleaning_seconds = 0.0
    total_pause_seconds = 0.0
    total_pause_count = 0
    all_washes: list[dict[str, Any]] = []
    battery_first: float | None = None
    battery_last: float | None = None
    charge_gain = 0.0
    charge_seconds = 0.0

    for attempt in attempts:
        metrics = attempt_metrics(attempt)
        if metrics["area_m2"] is not None:
            total_physical_area += float(metrics["area_m2"])
        if metrics["duration_seconds"] is not None:
            total_cleaning_seconds += float(metrics["duration_seconds"])
        total_pause_seconds += float(metrics["pause_seconds"] or 0.0)
        total_pause_count += int(metrics["pause_count"] or 0)
        all_washes.extend(metrics["wash_events"])
        if battery_first is None and metrics["battery_start_percent"] is not None:
            battery_first = float(metrics["battery_start_percent"])
        if metrics["battery_end_percent"] is not None:
            battery_last = float(metrics["battery_end_percent"])
        charge_gain += float(metrics["charge_gain_percent"] or 0.0)
        charge_seconds += float(metrics["charge_seconds"] or 0.0)
        execution_rows.append(
            {
                "attempt_id": attempt.get("attempt_id"),
                "state": attempt.get("state"),
                "zone_ids": list(attempt.get("zone_ids") or []),
                "target_type": attempt.get("target_type"),
                "targets": list(attempt.get("targets") or []),
                "requested_passes": attempt.get("requested_passes", 1),
                "repetition_mode": attempt.get("repetition_mode"),
                "pass_index": attempt.get("pass_index", 1),
                "pass_total": attempt.get("pass_total", 1),
                "cleaning_params_snapshot": dict(attempt.get("cleaning_params") or {}),
                "started_at": attempt.get("start_confirmed_at") or attempt.get("requested_at"),
                "finished_at": attempt.get("completed_at"),
                "metrics": metrics,
                "physical_targets": _target_groups(attempt, zone_map),
            }
        )

    # Robot area telemetry is physical coverage.  A second pass increases
    # processed coverage, but does not create a second copy of the floor.
    # Emulated repetitions are separate attempts, so unique floor coverage is
    # the maximum observed coverage for the same target group across passes.
    # Native repetitions are one robot command and expose only combined
    # coverage, therefore the best defensible unique-area estimate is the
    # observed coverage divided by the requested repetitions.
    configured_passes = max(1.0, _number((job.get("cleaning_params") or {}).get("passes")) or 1.0)
    total_processed_area = total_physical_area

    def execution_identity(execution: Mapping[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        return (
            str(execution.get("target_type") or ""),
            tuple(sorted(str(value) for value in execution.get("targets") or [])),
            tuple(sorted(str(value) for value in execution.get("zone_ids") or [])),
        )

    def execution_floor_area(execution: Mapping[str, Any], processed_area: float) -> float:
        mode = str(execution.get("repetition_mode") or "SINGLE").upper()
        requested = max(1.0, _number(execution.get("requested_passes")) or 1.0)
        if mode == "NATIVE" and requested > 1:
            return processed_area / requested
        # SINGLE and each EMULATED attempt report one physical pass.
        return processed_area

    floor_by_execution_group: dict[tuple[str, tuple[str, ...], tuple[str, ...]], float] = {}
    for execution in execution_rows:
        processed = _number((execution.get("metrics") or {}).get("area_m2"))
        if processed is None or processed <= 0:
            continue
        key = execution_identity(execution)
        floor = execution_floor_area(execution, processed)
        floor_by_execution_group[key] = max(floor_by_execution_group.get(key, 0.0), floor)
    total_floor_area = sum(floor_by_execution_group.values())

    # Attribute batch-level facts to logical zones only when defensible.  Time,
    # battery and processed area are additive across passes.  Floor area is not:
    # it uses the same max-per-target-group rule as the Job-level value.
    zone_rows: list[dict[str, Any]] = []
    zone_estimates: dict[str, dict[str, float]] = defaultdict(lambda: {"time": 0.0, "battery": 0.0, "processed_area": 0.0})
    zone_floor_groups: dict[tuple[str, tuple[str, tuple[str, ...], tuple[str, ...]]], float] = {}
    zone_estimate_available: set[str] = set()
    for execution in execution_rows:
        owners = [str(value) for value in execution["zone_ids"]]
        metrics = execution["metrics"]
        if not owners:
            continue
        processed = _number(metrics.get("area_m2"))
        identity = execution_identity(execution)
        if len(owners) == 1:
            zone_id = owners[0]
            zone_estimate_available.add(zone_id)
            if metrics.get("duration_seconds") is not None:
                zone_estimates[zone_id]["time"] += float(metrics["duration_seconds"])
            if metrics.get("battery_drop_percent") is not None:
                zone_estimates[zone_id]["battery"] += float(metrics["battery_drop_percent"])
            if processed is not None:
                zone_estimates[zone_id]["processed_area"] += float(processed)
                floor = execution_floor_area(execution, float(processed))
                floor_key = (zone_id, identity)
                zone_floor_groups[floor_key] = max(zone_floor_groups.get(floor_key, 0.0), floor)
            continue
        weights: dict[str, float] = {}
        for zone_id in owners:
            zone = zone_map.get(zone_id, {})
            metadata = dict(zone.get("metadata") or {})
            nominal = _number(metadata.get("nominal_area_m2"))
            if nominal is not None and nominal > 0:
                weights[zone_id] = nominal
        if len(weights) != len(owners) or sum(weights.values()) <= 0:
            continue
        total_weight = sum(weights.values())
        for zone_id, weight in weights.items():
            share = weight / total_weight
            zone_estimate_available.add(zone_id)
            if metrics.get("duration_seconds") is not None:
                zone_estimates[zone_id]["time"] += float(metrics["duration_seconds"]) * share
            if metrics.get("battery_drop_percent") is not None:
                zone_estimates[zone_id]["battery"] += float(metrics["battery_drop_percent"]) * share
            if processed is not None:
                attributed_processed = float(processed) * share
                zone_estimates[zone_id]["processed_area"] += attributed_processed
                floor = execution_floor_area(execution, attributed_processed)
                floor_key = (zone_id, identity)
                zone_floor_groups[floor_key] = max(zone_floor_groups.get(floor_key, 0.0), floor)

    for zone_id, zone in zone_map.items():
        metadata = dict(zone.get("metadata") or {})
        owners_count = sum(zone_id in [str(v) for v in row["zone_ids"]] for row in execution_rows)
        direct = owners_count > 0 and all(len(row["zone_ids"]) == 1 for row in execution_rows if zone_id in [str(v) for v in row["zone_ids"]])
        attribution = "measured" if direct else ("estimated" if zone_id in zone_estimate_available else "unavailable")
        zone_rows.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.get("zone_name") or zone_id,
                "robot_target_type": zone.get("robot_target_type"),
                "robot_target_id": zone.get("robot_target_id"),
                "result": zone.get("result"),
                "reason_code": zone.get("reason_code"),
                "started_at": zone.get("actual_start"),
                "finished_at": zone.get("finished_at"),
                "duration_seconds": _seconds(zone.get("actual_start"), zone.get("finished_at")),
                "nominal_area_m2": _number(metadata.get("nominal_area_m2")),
                "nominal_area_source": metadata.get("nominal_area_source") or ("manual" if metadata.get("nominal_area_m2") is not None else "unknown"),
                "attribution": attribution,
                "attributed_cleaning_seconds": zone_estimates[zone_id]["time"] if attribution != "unavailable" else None,
                "attributed_battery_percent": zone_estimates[zone_id]["battery"] if attribution != "unavailable" else None,
                "attributed_area_m2": (sum(value for (owner, _), value in zone_floor_groups.items() if owner == zone_id)) if attribution != "unavailable" else None,
                "attributed_floor_area_m2": (sum(value for (owner, _), value in zone_floor_groups.items() if owner == zone_id)) if attribution != "unavailable" else None,
                "attributed_processed_area_m2": zone_estimates[zone_id]["processed_area"] if attribution != "unavailable" else None,
            }
        )

    waits = wait_metrics(lifecycle_events, job.get("finished_at"))
    total_duration = _seconds(job.get("created_at"), job.get("finished_at"))
    start_delay = None
    if job.get("actual_start") and job.get("planned_start"):
        left, right = _dt(job.get("planned_start")), _dt(job.get("actual_start"))
        if left and right:
            try:
                start_delay = (right - left).total_seconds()
            except TypeError:
                start_delay = None

    battery_drop = None
    if battery_first is not None and battery_last is not None:
        battery_drop = max(0.0, battery_first - battery_last)

    result = str(job.get("result") or "FAILED")
    normalized_result = "PARTIAL" if result == "PARTIAL_SUCCESS" else result
    job_metadata = dict(job.get("metadata") or {})
    external_meta = dict(job_metadata.get("external_execution") or {}) if isinstance(job_metadata.get("external_execution"), Mapping) else {}
    return {
        "schema_version": STATISTICS_SCHEMA_VERSION,
        "record_id": uuid4().hex,
        "job_id": str(job.get("job_id") or ""),
        "occurrence_id": str(job.get("occurrence_id") or ""),
        "schedule_id": str(job.get("schedule_id") or ""),
        "schedule_name": str(job.get("schedule_name") or ""),
        "schedule_revision": int(job.get("schedule_revision") or 0),
        "origin": str(job.get("origin") or "SCHEDULED"),
        "execution_source": execution_source_for_job(job),
        "execution_mode": str(job.get("execution_mode") or "DRY_RUN"),
        "forecast_eligible": bool(external_meta.get("forecast_eligible")) if str(job.get("origin") or "").upper() == "EXTERNAL" else True,
        "external_execution": external_meta or None,
        "result": normalized_result,
        "raw_result": result,
        "reason_code": job.get("reason_code"),
        "created_at": job.get("created_at"),
        "warning_at": job.get("warning_at"),
        "planned_start": job.get("planned_start"),
        "actual_start": job.get("actual_start"),
        "finished_at": job.get("finished_at"),
        "deadline_at": job.get("deadline_at"),
        "recorded_at": recorded_at.isoformat(),
        "cleaning_params_snapshot": dict(job.get("cleaning_params") or {}),
        "targets_snapshot": list(job.get("targets") or []),
        "time": {
            "start_delay_seconds": start_delay,
            "total_duration_seconds": total_duration,
            "physical_execution_seconds": total_cleaning_seconds or None,
            "wait": waits,
            "pause_seconds": total_pause_seconds,
            "pause_count": total_pause_count,
        },
        "battery": {
            "start_percent": battery_first,
            "end_percent": battery_last,
            "consumed_percent": battery_drop,
            "charge_gain_percent": charge_gain,
            "charging_seconds": charge_seconds,
        },
        "area": {
            # ``physical_cleaned_m2`` remains as a compatibility alias, but from
            # schema v2 it means unique floor area. ``processed_m2`` is coverage
            # including repeated passes.
            "physical_cleaned_m2": total_floor_area or None,
            "floor_cleaned_m2": total_floor_area or None,
            "processed_m2": total_processed_area or None,
            "configured_passes": configured_passes,
            "source": "robot_observation" if total_processed_area else "unavailable",
        },
        "water_facts": {
            "wash_events": all_washes,
            "wash_count": len(all_washes),
        },
        "zones": zone_rows,
        "execution_batches": execution_rows,
        "source_quality": (
            "external_observed_0.12"
            if str(job.get("origin") or "").upper() == "EXTERNAL"
            else ("native_0.9" if any(isinstance((attempt.get("metadata") or {}).get("statistics_observations"), list) for attempt in attempts) else "historical_backfill")
        ),
    }


def repair_record_area(
    existing: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    *,
    repaired_at: datetime,
    repair_id: str,
) -> tuple[dict[str, Any], bool]:
    """Patch only area-derived fields in one persisted statistics record.

    Statistics Ledger records are otherwise immutable.  This helper exists for
    explicit deterministic repair migrations where a historical derivation bug
    can be recomputed from the durable terminal Job snapshot.  Record identity,
    timestamps and all non-area facts are preserved byte-for-byte where possible.
    """
    result = dict(existing)
    old_area = dict(existing.get("area") or {})
    new_area = dict(rebuilt.get("area") or {})
    changed = old_area != new_area or int(existing.get("schema_version") or 0) != STATISTICS_SCHEMA_VERSION
    if changed:
        result["schema_version"] = STATISTICS_SCHEMA_VERSION
        result["area"] = new_area

    rebuilt_batches = {
        str(row.get("attempt_id") or ""): dict(row)
        for row in rebuilt.get("execution_batches") or []
        if isinstance(row, Mapping)
    }
    batches: list[dict[str, Any]] = []
    for row in existing.get("execution_batches") or []:
        if not isinstance(row, Mapping):
            continue
        current = dict(row)
        fresh = rebuilt_batches.get(str(current.get("attempt_id") or ""))
        if fresh is not None:
            metrics = dict(current.get("metrics") or {})
            fresh_metrics = dict(fresh.get("metrics") or {})
            before = {key: metrics.get(key) for key in ("area_m2", "area_source", "area_diagnostics")}
            for key in ("area_m2", "area_source", "area_diagnostics"):
                if key in fresh_metrics:
                    metrics[key] = fresh_metrics.get(key)
                else:
                    metrics.pop(key, None)
            after = {key: metrics.get(key) for key in ("area_m2", "area_source", "area_diagnostics")}
            if before != after:
                changed = True
            current["metrics"] = metrics
        batches.append(current)
    if batches or existing.get("execution_batches") == []:
        result["execution_batches"] = batches

    rebuilt_zones = {
        str(row.get("zone_id") or ""): dict(row)
        for row in rebuilt.get("zones") or []
        if isinstance(row, Mapping)
    }
    zones: list[dict[str, Any]] = []
    for row in existing.get("zones") or []:
        if not isinstance(row, Mapping):
            continue
        current = dict(row)
        fresh = rebuilt_zones.get(str(current.get("zone_id") or ""))
        if fresh is not None:
            before = {key: current.get(key) for key in ("attributed_area_m2", "attributed_floor_area_m2", "attributed_processed_area_m2")}
            for key in ("attributed_area_m2", "attributed_floor_area_m2", "attributed_processed_area_m2"):
                current[key] = fresh.get(key)
            after = {key: current.get(key) for key in ("attributed_area_m2", "attributed_floor_area_m2", "attributed_processed_area_m2")}
            if before != after:
                changed = True
        zones.append(current)
    if zones or existing.get("zones") == []:
        result["zones"] = zones

    if not changed:
        return result, False

    repairs = [dict(item) for item in existing.get("repairs") or [] if isinstance(item, Mapping)]
    repairs = [item for item in repairs if str(item.get("repair_id") or "") != repair_id]
    repairs.append({
        "repair_id": repair_id,
        "repaired_at": repaired_at.isoformat(),
        "old_physical_cleaned_m2": old_area.get("physical_cleaned_m2"),
        "new_physical_cleaned_m2": new_area.get("physical_cleaned_m2"),
        "old_processed_m2": old_area.get("processed_m2"),
        "new_processed_m2": new_area.get("processed_m2"),
        "scope": "floor_processed_area_and_area_attribution_only",
    })
    result["repairs"] = repairs[-10:]
    return result, True



def record_matches(record: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    start = _dt(filters.get("start"))
    end = _dt(filters.get("end"))
    at = _dt(record.get("planned_start") or record.get("finished_at") or record.get("created_at"))
    if start and at:
        try:
            if at < start:
                return False
        except TypeError:
            pass
    if end and at:
        try:
            if at > end:
                return False
        except TypeError:
            pass
    for key in ("schedule_id", "execution_mode", "origin", "execution_source"):
        value = filters.get(key)
        if value not in (None, "", "all") and str(record.get(key)) != str(value):
            return False
    zone_id = filters.get("zone_id")
    if zone_id not in (None, "", "all") and not any(str(zone.get("zone_id")) == str(zone_id) for zone in record.get("zones") or []):
        return False
    return True


def _record_floor_area(record: Mapping[str, Any]) -> float | None:
    area = record.get("area") or {}
    return _number(area.get("floor_cleaned_m2")) or _number(area.get("physical_cleaned_m2"))


def _record_processed_area(record: Mapping[str, Any]) -> float | None:
    area = record.get("area") or {}
    return _number(area.get("processed_m2")) or _number(area.get("physical_cleaned_m2"))


def _profile_params(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the semantic profile used by Statistics grouping and display.

    Historical schedule snapshots may retain fields that are irrelevant to the
    effective cleaning scope (for example mop/water values after a weekday
    override switched the Job to vacuum-only). Grouping by those stale fields
    creates duplicate rows that render identically in the UI. Canonicalize the
    profile by physical meaning before calculating its signature.

    Unknown/legacy cleaning modes stay conservative: no parameters are dropped
    unless the effective scope is explicit enough to do so safely.
    """
    source = dict(record.get("cleaning_params_snapshot") or {})
    result: dict[str, Any] = {}
    for key in _PROFILE_PARAMETER_KEYS:
        value = source.get(key)
        if value not in (None, "", "__none__"):
            result[key] = value
    if "passes" not in result:
        result["passes"] = 1

    scope = cleaning_scope(result)
    if scope.explicitly_dry:
        # Vacuum-only: mop/water settings may be stale inherited base-profile
        # values and do not describe the physical run.
        result.pop("mop_mode", None)
        result.pop("water_mode", None)
    elif scope.kind == "wet" and scope.vacuum is False and scope.mop is True:
        # Mop-only: fan/route settings do not describe the physical run.
        result.pop("fan_mode", None)
        result.pop("cleaning_route", None)
    return result


def _profile_signature(record: Mapping[str, Any]) -> str:
    params = _profile_params(record)
    return "|".join(f"{key}={params.get(key)!r}" for key in _PROFILE_PARAMETER_KEYS if key in params) or "default"


def _compact_group_summary(
    items: Sequence[Mapping[str, Any]],
    metric_percentiles: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    selected = {"time": 90, "battery": 90, "clean_water": 90, "dirty_water": 90, "area": 90}
    selected.update({str(key): int(value) for key, value in dict(metric_percentiles or {}).items() if value is not None})
    selected["area"] = selected["time"]
    result_counts = Counter(str(item.get("result") or "FAILED") for item in items)
    durations = [(item.get("time") or {}).get("physical_execution_seconds") for item in items]
    waits = [(item.get("time") or {}).get("wait", {}).get("total_seconds") for item in items]
    battery = [(item.get("battery") or {}).get("consumed_percent") for item in items]
    floor_area = [_record_floor_area(item) for item in items]
    processed_area = [_record_processed_area(item) for item in items]
    seconds_per_m2: list[float] = []
    battery_per_m2: list[float] = []
    clean_per_m2: list[float] = []
    dirty_per_m2: list[float] = []
    for item in items:
        floor = _record_floor_area(item)
        duration = _number((item.get("time") or {}).get("physical_execution_seconds"))
        consumed = _number((item.get("battery") or {}).get("consumed_percent"))
        water = item.get("water_usage") or {}
        clean = _number(water.get("clean_used_ml_eq"))
        dirty = _number(water.get("dirty_gained_ml_eq"))
        water_available = bool(water.get("available"))
        if floor and floor > 0:
            if duration is not None:
                seconds_per_m2.append(duration / floor)
            if consumed is not None:
                battery_per_m2.append(consumed / floor)
            if water_available:
                clean_per_m2.append((clean or 0.0) / floor)
                dirty_per_m2.append((dirty or 0.0) / floor)
    return {
        "total": len(items),
        "success": result_counts["SUCCESS"],
        "partial": result_counts["PARTIAL"],
        "suppressed": result_counts["SUPPRESSED"],
        "failed": result_counts["FAILED"],
        "execution_seconds": distribution(durations, selected["time"]),
        # Common profile-table alias used by both schedule and zone rows.
        # Schedule rows historically exposed ``execution_seconds`` while the
        # shared frontend renderer expects ``cleaning_seconds``. Keep both.
        "cleaning_seconds": distribution(durations, selected["time"]),
        "wait_seconds": distribution(waits, selected["time"]),
        "battery_consumed_percent": distribution(battery, selected["battery"]),
        "floor_area_m2": distribution(floor_area, selected["area"]),
        "processed_area_m2": distribution(processed_area, selected["area"]),
        # Compatibility alias: profile tables now mean floor area.
        "area_m2": distribution(floor_area, selected["area"]),
        "seconds_per_m2": distribution(seconds_per_m2, selected["time"]),
        "battery_percent_per_m2": distribution(battery_per_m2, selected["battery"]),
        "clean_water_ml_eq_per_m2": distribution(clean_per_m2, selected["clean_water"]),
        "dirty_water_ml_eq_per_m2": distribution(dirty_per_m2, selected["dirty_water"]),
    }


def _weighted_ratio(records: Sequence[Mapping[str, Any]], numerator_getter, *, water_only: bool = False) -> dict[str, Any]:
    numerator = 0.0
    denominator = 0.0
    count = 0
    for record in records:
        if water_only and not bool((record.get("water_usage") or {}).get("available")):
            continue
        floor = _record_floor_area(record)
        value = _number(numerator_getter(record))
        if floor is None or floor <= 0 or value is None:
            continue
        numerator += value
        denominator += floor
        count += 1
    return {
        "count": count,
        "value": (numerator / denominator) if denominator > 0 else None,
        "numerator": numerator if count else None,
        "floor_area_m2": denominator if count else None,
    }


def aggregate_records(
    records: Sequence[Mapping[str, Any]],
    *,
    current_zone_nominal_areas: Mapping[str, float | None] | None = None,
    metric_percentiles: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return UI/report aggregates derived from immutable records.

    Global per-square-metre values are weighted totals over unique floor area.
    Comparative rows are profile-aware: a schedule or zone is split whenever
    execution parameters affecting resource use change.  For historical records
    that predate nominal-area configuration, current zone nominal areas may be
    supplied as a query-time fallback for *estimated* attribution only.  The
    immutable Statistics Ledger itself is never rewritten.
    """
    selected = {"time": 90, "battery": 90, "clean_water": 90, "dirty_water": 90, "area": 90}
    selected.update({str(key): int(value) for key, value in dict(metric_percentiles or {}).items() if value is not None})
    selected["area"] = selected["time"]
    total = len(records)
    result_counts = Counter(str(record.get("result") or "FAILED") for record in records)
    reasons = Counter(str(record.get("reason_code") or "unknown") for record in records if str(record.get("result")) != "SUCCESS")
    scheduled = [record for record in records if str(record.get("origin")) == "SCHEDULED"]
    scheduled_failed = sum(str(record.get("result")) == "FAILED" for record in scheduled)
    scheduled_partial = sum(str(record.get("result")) == "PARTIAL" for record in scheduled)
    scheduled_suppressed = sum(str(record.get("result")) == "SUPPRESSED" for record in scheduled)

    zones_total = zones_success = zones_failed = zones_skipped = 0
    zone_profile_rows: dict[tuple[str, str], dict[str, Any]] = {}
    parameter_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    schedule_profile_groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)

    def fill_zone_metric(
        values: Sequence[float | None],
        total_value: float | None,
        weights: Sequence[float | None],
        eligible: Sequence[bool],
    ) -> tuple[list[float | None], list[bool]]:
        """Fill only defensible historical zone-attribution gaps.

        Newer Statistics Records already carry per-zone facts. Older backfilled
        rows often only have Job totals. For one participating zone the Job
        total belongs to that zone. For multiple participating zones we may
        distribute only the *unattributed remainder*, and only when every
        missing zone has a positive floor/nominal-area weight. Otherwise the
        value deliberately remains unavailable.
        """
        output = [None if value is None else float(value) for value in values]
        estimated = [False] * len(output)
        if total_value is None or not output:
            return output, estimated
        missing = [index for index, value in enumerate(output) if value is None and bool(eligible[index])]
        if not missing:
            return output, estimated
        participating = [index for index, allowed in enumerate(eligible) if allowed]
        if len(participating) == 1:
            index = participating[0]
            if output[index] is None:
                output[index] = max(0.0, float(total_value))
                estimated[index] = True
            return output, estimated
        chosen = [weights[index] for index in missing]
        if not chosen or any(weight is None or float(weight) <= 0 for weight in chosen):
            return output, estimated
        existing = sum(max(0.0, float(value)) for index, value in enumerate(output) if value is not None and bool(eligible[index]))
        remainder = max(0.0, float(total_value) - existing)
        denominator = sum(float(weight) for weight in chosen if weight is not None)
        if denominator <= 0:
            return output, estimated
        for index in missing:
            output[index] = remainder * float(weights[index]) / denominator
            estimated[index] = True
        return output, estimated

    for record in records:
        signature = _profile_signature(record)
        params = _profile_params(record)
        parameter_groups[signature].append(record)
        schedule_profile_groups[(str(record.get("schedule_id") or ""), signature)].append(record)
        record_zones = [zone for zone in record.get("zones") or [] if isinstance(zone, Mapping)]
        water = record.get("water_usage") or {}
        water_available = bool(water.get("available"))
        clean_total = _number(water.get("clean_used_ml_eq")) or 0.0
        dirty_total = _number(water.get("dirty_gained_ml_eq")) or 0.0

        # Historical 0.9.x/early-0.10 records may contain Job totals but lack
        # defensible per-zone time/battery/area attribution. Reconstruct only
        # what can be derived without guessing: one participating zone gets the
        # Job total; multi-zone gaps use the unattributed remainder weighted by
        # known floor/nominal area. Skipped zones never receive physical work.
        eligible = [str(zone.get("result") or "").upper() != "SKIPPED" for zone in record_zones]
        nominal_weights = []
        for zone in record_zones:
            saved_nominal = _number(zone.get("nominal_area_m2"))
            if saved_nominal is not None and saved_nominal > 0:
                nominal_weights.append(saved_nominal)
                continue
            zone_id = str(zone.get("zone_id") or "")
            current_nominal = _number((current_zone_nominal_areas or {}).get(zone_id))
            nominal_weights.append(current_nominal if current_nominal is not None and current_nominal > 0 else None)
        floor_raw = [
            _number(zone.get("attributed_floor_area_m2"))
            if _number(zone.get("attributed_floor_area_m2")) is not None
            else _number(zone.get("attributed_area_m2"))
            for zone in record_zones
        ]
        processed_raw = [
            _number(zone.get("attributed_processed_area_m2"))
            if _number(zone.get("attributed_processed_area_m2")) is not None
            else _number(zone.get("attributed_area_m2"))
            for zone in record_zones
        ]
        time_raw = [_number(zone.get("attributed_cleaning_seconds")) for zone in record_zones]
        battery_raw = [_number(zone.get("attributed_battery_percent")) for zone in record_zones]

        floor_values, floor_estimated = fill_zone_metric(
            floor_raw,
            _record_floor_area(record),
            nominal_weights,
            eligible,
        )
        effective_floor_weights = [
            value if value is not None and value > 0 else nominal_weights[index]
            for index, value in enumerate(floor_values)
        ]
        processed_values, processed_estimated = fill_zone_metric(
            processed_raw,
            _record_processed_area(record),
            effective_floor_weights,
            eligible,
        )
        time_values, time_estimated = fill_zone_metric(
            time_raw,
            _number((record.get("time") or {}).get("physical_execution_seconds")),
            effective_floor_weights,
            eligible,
        )
        battery_values_by_zone, battery_estimated = fill_zone_metric(
            battery_raw,
            _number((record.get("battery") or {}).get("consumed_percent")),
            effective_floor_weights,
            eligible,
        )
        processed_sum = sum(max(0.0, value or 0.0) for value in processed_values)
        floor_sum = sum(max(0.0, value or 0.0) for value in floor_values)

        for index, zone in enumerate(record_zones):
            zones_total += 1
            result = str(zone.get("result") or "")
            zones_success += result == "SUCCESS"
            zones_failed += result == "FAILED"
            zones_skipped += result == "SKIPPED"
            zone_id = str(zone.get("zone_id") or "")
            key = (zone_id, signature)
            row = zone_profile_rows.setdefault(
                key,
                {
                    "zone_id": zone_id,
                    "zone_name": zone.get("zone_name") or zone_id,
                    "profile_signature": signature,
                    "profile_params": params,
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                    "measured": 0,
                    "estimated": 0,
                    "unavailable": 0,
                    "time_values": [],
                    "battery_values": [],
                    "floor_area_values": [],
                    "processed_area_values": [],
                    "seconds_per_m2_values": [],
                    "battery_per_m2_values": [],
                    "clean_per_m2_values": [],
                    "dirty_per_m2_values": [],
                },
            )
            row["total"] += 1
            row["success"] += result == "SUCCESS"
            row["failed"] += result == "FAILED"
            row["skipped"] += result == "SKIPPED"
            attribution = str(zone.get("attribution") or "unavailable")
            fallback_used = any((
                floor_estimated[index],
                processed_estimated[index],
                time_estimated[index],
                battery_estimated[index],
            ))
            effective_attribution = "estimated" if fallback_used else attribution
            if effective_attribution not in {"measured", "estimated", "unavailable"}:
                effective_attribution = "unavailable"
            row[effective_attribution] += 1
            attributed_time = time_values[index]
            attributed_battery = battery_values_by_zone[index]
            floor = floor_values[index]
            processed = processed_values[index]
            if attributed_time is not None:
                row["time_values"].append(attributed_time)
            if attributed_battery is not None:
                row["battery_values"].append(attributed_battery)
            if floor is not None:
                row["floor_area_values"].append(floor)
            if processed is not None:
                row["processed_area_values"].append(processed)
            if floor and floor > 0:
                if attributed_time is not None:
                    row["seconds_per_m2_values"].append(attributed_time / floor)
                if attributed_battery is not None:
                    row["battery_per_m2_values"].append(attributed_battery / floor)
                if water_available:
                    if processed_sum > 0 and processed is not None:
                        share = processed / processed_sum
                    elif floor_sum > 0:
                        share = floor / floor_sum
                    else:
                        share = 1.0 / max(1, len(record_zones))
                    row["clean_per_m2_values"].append((clean_total * share) / floor)
                    row["dirty_per_m2_values"].append((dirty_total * share) / floor)

    finalized_zones: list[dict[str, Any]] = []
    for row in zone_profile_rows.values():
        output = {key: value for key, value in row.items() if not key.endswith("_values")}
        output["cleaning_seconds"] = distribution(row["time_values"], selected["time"])
        output["battery_consumed_percent"] = distribution(row["battery_values"], selected["battery"])
        output["floor_area_m2"] = distribution(row["floor_area_values"], selected["area"])
        output["processed_area_m2"] = distribution(row["processed_area_values"], selected["area"])
        output["area_m2"] = output["floor_area_m2"]
        output["seconds_per_m2"] = distribution(row["seconds_per_m2_values"], selected["time"])
        output["battery_percent_per_m2"] = distribution(row["battery_per_m2_values"], selected["battery"])
        output["clean_water_ml_eq_per_m2"] = distribution(row["clean_per_m2_values"], selected["clean_water"])
        output["dirty_water_ml_eq_per_m2"] = distribution(row["dirty_per_m2_values"], selected["dirty_water"])
        finalized_zones.append(output)

    wait_totals = [(record.get("time") or {}).get("wait", {}).get("total_seconds") for record in records]
    wait_counts = [(record.get("time") or {}).get("wait", {}).get("count") for record in records]
    wait_reasons: Counter[str] = Counter()
    for record in records:
        for reason, seconds in ((record.get("time") or {}).get("wait", {}).get("by_reason_seconds") or {}).items():
            wait_reasons[str(reason)] += float(seconds or 0.0)

    floor_area_values = [_record_floor_area(record) for record in records]
    processed_area_values = [_record_processed_area(record) for record in records]
    battery_values = [(record.get("battery") or {}).get("consumed_percent") for record in records]
    duration_values = [(record.get("time") or {}).get("physical_execution_seconds") for record in records]
    start_delays = [(record.get("time") or {}).get("start_delay_seconds") for record in records]
    charge_rates = []
    for record in records:
        battery = record.get("battery") or {}
        gain = _number(battery.get("charge_gain_percent")) or 0.0
        seconds = _number(battery.get("charging_seconds")) or 0.0
        if gain > 0 and seconds > 0:
            charge_rates.append(gain / (seconds / 60.0))

    schedule_rows = []
    for (schedule_id, signature), items in schedule_profile_groups.items():
        summary = _compact_group_summary(items, selected)
        summary.update({
            "schedule_id": schedule_id,
            "schedule_name": next((str(item.get("schedule_name") or "") for item in items if item.get("schedule_name")), schedule_id),
            "profile_signature": signature,
            "profile_params": _profile_params(items[0]) if items else {},
        })
        schedule_rows.append(summary)

    parameter_rows = []
    for signature, items in parameter_groups.items():
        summary = _compact_group_summary(items, selected)
        summary.update({"signature": signature, "profile_params": _profile_params(items[0]) if items else {}, "count": len(items)})
        parameter_rows.append(summary)

    weighted_time = _weighted_ratio(records, lambda record: (record.get("time") or {}).get("physical_execution_seconds"))
    weighted_battery = _weighted_ratio(records, lambda record: (record.get("battery") or {}).get("consumed_percent"))
    weighted_clean = _weighted_ratio(records, lambda record: (record.get("water_usage") or {}).get("clean_used_ml_eq") if bool((record.get("water_usage") or {}).get("available")) else None, water_only=True)
    weighted_dirty = _weighted_ratio(records, lambda record: (record.get("water_usage") or {}).get("dirty_gained_ml_eq") if bool((record.get("water_usage") or {}).get("available")) else None, water_only=True)

    return {
        "records": total,
        "execution": {
            "success": result_counts["SUCCESS"],
            "partial": result_counts["PARTIAL"],
            "suppressed": result_counts["SUPPRESSED"],
            "failed": result_counts["FAILED"],
            "success_rate": (result_counts["SUCCESS"] / total * 100.0) if total else None,
            "partial_rate": (result_counts["PARTIAL"] / total * 100.0) if total else None,
            "suppressed_rate": (result_counts["SUPPRESSED"] / total * 100.0) if total else None,
            "failure_rate": (result_counts["FAILED"] / total * 100.0) if total else None,
            "scheduled": len(scheduled),
            "scheduled_uncompleted": scheduled_failed + scheduled_partial + scheduled_suppressed,
            "scheduled_uncompleted_rate": ((scheduled_failed + scheduled_partial + scheduled_suppressed) / len(scheduled) * 100.0) if scheduled else None,
        },
        "zones": {
            "planned": zones_total,
            "success": zones_success,
            "failed": zones_failed,
            "skipped": zones_skipped,
            "rows": sorted(finalized_zones, key=lambda row: (str(row["zone_name"]).casefold(), str(row.get("profile_signature") or ""))),
        },
        "time": {
            "execution_seconds": distribution(duration_values, selected["time"]),
            "seconds_per_m2": distribution([
                (_number((record.get("time") or {}).get("physical_execution_seconds")) / floor)
                for record in records
                if (floor := _record_floor_area(record)) and _number((record.get("time") or {}).get("physical_execution_seconds")) is not None
            ], selected["time"]),
            "seconds_per_m2_weighted": weighted_time,
            "start_delay_seconds": distribution(start_delays, selected["time"]),
            "wait_seconds": distribution(wait_totals, selected["time"]),
            "wait_count": int(sum(int(value or 0) for value in wait_counts)),
            "wait_by_reason_seconds": dict(wait_reasons.most_common()),
            "pause_seconds": distribution([(record.get("time") or {}).get("pause_seconds") for record in records], selected["time"]),
        },
        "battery": {
            "consumed_percent": distribution(battery_values, selected["battery"]),
            "consumed_percent_per_m2": distribution([
                (_number((record.get("battery") or {}).get("consumed_percent")) / floor)
                for record in records
                if (floor := _record_floor_area(record)) and _number((record.get("battery") or {}).get("consumed_percent")) is not None
            ], selected["battery"]),
            "consumed_percent_per_m2_weighted": weighted_battery,
            "charge_rate_percent_per_minute": distribution(charge_rates, selected["battery"]),
        },
        "water_usage": {
            "clean_ml_eq_per_m2_weighted": weighted_clean,
            "dirty_ml_eq_per_m2_weighted": weighted_dirty,
        },
        "area": {
            "floor_cleaned_m2": sum(float(value or 0.0) for value in floor_area_values),
            "processed_m2": sum(float(value or 0.0) for value in processed_area_values),
            "physical_cleaned_m2": sum(float(value or 0.0) for value in floor_area_values),
            "per_job_m2": distribution(floor_area_values, selected["area"]),
            "processed_per_job_m2": distribution(processed_area_values, selected["area"]),
        },
        "reasons": [{"reason_code": reason, "count": count} for reason, count in reasons.most_common()],
        "by_schedule": sorted(schedule_rows, key=lambda row: (str(row.get("schedule_name") or "").casefold(), str(row.get("profile_signature") or ""))),
        "cleaning_parameters": sorted(parameter_rows, key=lambda row: (-int(row.get("count") or 0), str(row.get("signature")))),
    }



def build_job_comparison(
    current: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    minimum_samples: int = 3,
    metric_percentiles: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Build a conservative presentation-only comparison for one Job.

    Previous runs from the same execution mode are considered. Exact zone and
    parameter matches are preferred; broader fallbacks are explicitly labelled.
    The current Job is never included in its own baseline.
    """
    job_id = str(current.get("job_id") or "")
    current_at = str(current.get("planned_start") or current.get("finished_at") or current.get("created_at") or "")
    execution_mode = str(current.get("execution_mode") or "DRY_RUN")
    current_zones = tuple(sorted(str(z.get("zone_id") or "") for z in current.get("zones") or [] if z.get("zone_id")))
    keys = ("cleaning_mode", "mop_mode", "fan_mode", "water_mode", "passes")
    params = dict(current.get("cleaning_params_snapshot") or {})
    current_params = tuple((key, str(params.get(key))) for key in keys if params.get(key) not in (None, "", "__none__"))

    def zone_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(str(z.get("zone_id") or "") for z in row.get("zones") or [] if z.get("zone_id")))

    def parameter_signature(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
        values = dict(row.get("cleaning_params_snapshot") or {})
        return tuple((key, str(values.get(key))) for key in keys if values.get(key) not in (None, "", "__none__"))

    previous = [
        row for row in records
        if str(row.get("job_id") or "") != job_id
        and str(row.get("execution_mode") or "DRY_RUN") == execution_mode
        and str(row.get("result") or "") in {"SUCCESS", "PARTIAL"}
        and (not current_at or str(row.get("planned_start") or row.get("finished_at") or row.get("created_at") or "") < current_at)
    ]
    exact = [row for row in previous if current_zones and zone_signature(row) == current_zones and parameter_signature(row) == current_params]
    same_zones = [row for row in previous if current_zones and zone_signature(row) == current_zones]
    current_schedule_id = str(current.get("schedule_id") or "")
    same_schedule = [row for row in previous if current_schedule_id and str(row.get("schedule_id") or "") == current_schedule_id]
    minimum = max(1, int(minimum_samples))
    if len(exact) >= minimum:
        candidates, basis = exact, "zones_and_parameters"
    elif len(same_zones) >= minimum:
        candidates, basis = same_zones, "zones"
    elif len(same_schedule) >= minimum:
        candidates, basis = same_schedule, "schedule"
    else:
        candidates, basis = max(
            ((exact, "zones_and_parameters"), (same_zones, "zones"), (same_schedule, "schedule")),
            key=lambda item: len(item[0]),
        )
    return {
        "available": len(candidates) >= minimum,
        "basis": basis,
        "sample_count": len(candidates),
        "minimum_samples": minimum,
        "aggregate": aggregate_records(candidates, metric_percentiles=metric_percentiles),
    }
