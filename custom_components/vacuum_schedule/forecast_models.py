"""Trainable resource forecasting for Vacuum Schedule 0.11.x.

Forecast data is intentionally separate from Statistics Ledger.  The ledger is
immutable fact history; forecast branches are disposable, generation-scoped
training state that may be archived, trimmed, reset and restored independently.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4
try:
    from .cleaning_scope import cleaning_scope, canonicalize_forecast_profile_params, water_parameter_signature
except ImportError:  # pragma: no cover - isolated source-file testing
    from cleaning_scope import cleaning_scope, canonicalize_forecast_profile_params, water_parameter_signature


FORECAST_METRICS = ("time", "battery", "clean_water", "dirty_water")
FORECAST_MODEL_SCHEMA_VERSION = 2
DEFAULT_MINIMUM_SAMPLES = 3


@dataclass(frozen=True, slots=True)
class ForecastPolicyConfig:
    """User policy for one independent forecast resource."""

    enabled: bool = False
    percentile: int = 90
    delta_percent: float = 10.0
    lookback_days: int = 90

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ForecastPolicyConfig":
        raw = dict(data or {})
        try:
            percentile = int(raw.get("percentile", 90))
        except (TypeError, ValueError):
            percentile = 90
        try:
            delta = float(raw.get("delta_percent", 10.0))
        except (TypeError, ValueError):
            delta = 10.0
        try:
            lookback = int(raw.get("lookback_days", 90))
        except (TypeError, ValueError):
            lookback = 90
        return cls(
            enabled=bool(raw.get("enabled", False)),
            percentile=max(50, min(99, percentile)),
            delta_percent=max(0.0, min(500.0, delta)),
            lookback_days=max(1, min(3650, lookback)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "percentile": self.percentile,
            "delta_percent": self.delta_percent,
            "lookback_days": self.lookback_days,
        }


def default_forecast_policies() -> dict[str, ForecastPolicyConfig]:
    """Return conservative, backwards-compatible disabled defaults."""
    return {metric: ForecastPolicyConfig() for metric in FORECAST_METRICS}


def forecast_policies_from_dict(data: Mapping[str, Any] | None) -> dict[str, ForecastPolicyConfig]:
    raw = dict(data or {})
    return {metric: ForecastPolicyConfig.from_dict(raw.get(metric)) for metric in FORECAST_METRICS}


def forecast_policies_to_dict(data: Mapping[str, ForecastPolicyConfig]) -> dict[str, Any]:
    return {metric: data.get(metric, ForecastPolicyConfig()).to_dict() for metric in FORECAST_METRICS}


def _number(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _percentile(values: Sequence[float], percentile: int | float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    q = max(0.0, min(1.0, float(percentile) / 100.0 if float(percentile) > 1 else float(percentile)))
    rank = (len(ordered) - 1) * q
    low = int(rank)
    high = min(len(ordered) - 1, low + 1)
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def distribution(values: Iterable[float | int | None], percentile: int) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "count": len(clean),
        "mean": (sum(clean) / len(clean)) if clean else None,
        "median": median(clean) if clean else None,
        "percentile": _percentile(clean, percentile),
        "percentile_number": int(percentile),
    }


_PARAMETER_KEYS = ("cleaning_mode", "cleaning_route", "mop_mode", "fan_mode", "water_mode", "passes")


def parameter_signature(
    params: Mapping[str, Any] | None, metric: str | None = None
) -> tuple[tuple[str, str], ...]:
    if metric in {"clean_water", "dirty_water"}:
        return water_parameter_signature(params)
    values = canonicalize_forecast_profile_params(params)
    return tuple(
        (key, str(values.get(key)))
        for key in _PARAMETER_KEYS
        if values.get(key) not in (None, "", "__none__")
    )


def zone_signature(zone_ids: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in zone_ids if str(value)}))


def _sample_time(record: Mapping[str, Any]) -> str | None:
    for key in ("finished_at", "recorded_at", "actual_start", "planned_start"):
        dt = _dt(record.get(key))
        if dt is not None:
            return dt.isoformat()
    return None


def _zone_water_values(record: Mapping[str, Any], total: float | None) -> dict[str, float]:
    """Defensibly attribute one Job water total across successful logical zones."""
    if total is None:
        return {}
    zones = [dict(item) for item in record.get("zones") or [] if isinstance(item, Mapping)]
    eligible = [zone for zone in zones if str(zone.get("result") or "").upper() == "SUCCESS"]
    if not eligible:
        return {}
    if len(eligible) == 1:
        return {str(eligible[0].get("zone_id") or ""): float(total)}
    weights: dict[str, float] = {}
    for zone in eligible:
        zone_id = str(zone.get("zone_id") or "")
        value = _number(zone.get("attributed_processed_area_m2"))
        if value is None or value <= 0:
            value = _number(zone.get("attributed_floor_area_m2")) or _number(zone.get("attributed_area_m2"))
        if value is None or value <= 0:
            value = _number(zone.get("nominal_area_m2"))
        if zone_id and value is not None and value > 0:
            weights[zone_id] = value
    if len(weights) != len(eligible) or sum(weights.values()) <= 0:
        return {}
    denominator = sum(weights.values())
    return {zone_id: float(total) * weight / denominator for zone_id, weight in weights.items()}


def compact_forecast_sample(
    record: Mapping[str, Any], metric: str, *, water_usage: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Extract a compact REAL observation for one resource model."""
    if metric not in FORECAST_METRICS:
        raise ValueError("invalid_forecast_metric")
    if str(record.get("execution_mode") or "").upper() != "REAL":
        return None
    # External physical runs are valuable facts, but unresolved targets or
    # effective parameters must never train a specific Scheduler forecast model.
    if str(record.get("origin") or "").upper() == "EXTERNAL" and not bool(record.get("forecast_eligible")):
        return None
    at = _sample_time(record)
    if at is None:
        return None
    result = str(record.get("result") or "").upper()
    params = dict(record.get("cleaning_params_snapshot") or {})
    scope = cleaning_scope(params)
    if metric in {"clean_water", "dirty_water"} and scope.kind != "wet":
        # Explicit dry jobs require no predictive water model at all; unresolved
        # default/vendor modes are excluded rather than contaminating wet data.
        return None
    zones = [dict(item) for item in record.get("zones") or [] if isinstance(item, Mapping)]
    water = dict(water_usage or record.get("water_usage") or {})

    if metric == "time":
        job_value = _number((record.get("time") or {}).get("physical_execution_seconds"))
        zone_values = {
            str(zone.get("zone_id") or ""): _number(zone.get("attributed_cleaning_seconds"))
            for zone in zones if str(zone.get("result") or "").upper() == "SUCCESS"
        }
    elif metric == "battery":
        job_value = _number((record.get("battery") or {}).get("consumed_percent"))
        zone_values = {
            str(zone.get("zone_id") or ""): _number(zone.get("attributed_battery_percent"))
            for zone in zones if str(zone.get("result") or "").upper() == "SUCCESS"
        }
    elif metric == "clean_water":
        job_value = _number(water.get("clean_used_ml_eq")) if bool(water.get("available")) else None
        zone_values = _zone_water_values(record, job_value)
    else:
        job_value = _number(water.get("dirty_gained_ml_eq")) if bool(water.get("available")) else None
        zone_values = _zone_water_values(record, job_value)

    zone_rows: list[dict[str, Any]] = []
    for zone in zones:
        zone_id = str(zone.get("zone_id") or "")
        value = zone_values.get(zone_id)
        if not zone_id or value is None or value < 0:
            continue
        zone_rows.append({
            "zone_id": zone_id,
            "zone_name": zone.get("zone_name") or zone_id,
            "result": zone.get("result"),
            "value": float(value),
            "attribution": zone.get("attribution") or ("estimated" if metric in {"clean_water", "dirty_water"} else "unavailable"),
        })

    # Whole-Job distributions are trained only by successful complete Jobs.
    complete_value = float(job_value) if result == "SUCCESS" and job_value is not None and job_value >= 0 else None
    if complete_value is None and not zone_rows:
        return None
    return {
        "sample_id": uuid4().hex,
        "job_id": str(record.get("job_id") or ""),
        "schedule_id": str(record.get("schedule_id") or ""),
        "schedule_name": str(record.get("schedule_name") or ""),
        "at": at,
        "result": result,
        "params": params,
        "cleaning_scope": scope.kind,
        "job_value": complete_value,
        "zones": zone_rows,
        "zone_ids": [str(zone.get("zone_id") or "") for zone in zones if str(zone.get("result") or "").upper() == "SUCCESS"],
    }


def new_active_branch(*, now: datetime, generation: int = 1, samples: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return {
        "generation": max(1, int(generation)),
        "started_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "samples": [dict(item) for item in samples],
        "archives": [],
    }


def normalize_branch(raw: Mapping[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    data = dict(raw or {})
    branch = new_active_branch(now=now, generation=int(data.get("generation") or 1))
    branch["started_at"] = str(data.get("started_at") or branch["started_at"])
    branch["updated_at"] = str(data.get("updated_at") or branch["updated_at"])
    branch["samples"] = [dict(item) for item in data.get("samples") or [] if isinstance(item, Mapping)][-10000:]
    branch["archives"] = [dict(item) for item in data.get("archives") or [] if isinstance(item, Mapping)][-100:]
    return branch


def filtered_samples(
    branch: Mapping[str, Any], policy: ForecastPolicyConfig, *, now: datetime
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=policy.lookback_days)
    generation_start = _dt(branch.get("started_at"))
    if generation_start is not None:
        # Forecast generations bootstrapped from pre-0.11 statistics may carry
        # legacy offset-naive timestamps.  Compare them in the same timezone
        # context as the current HA-local cutoff instead of raising TypeError.
        if generation_start.tzinfo is None and cutoff.tzinfo is not None:
            generation_start = generation_start.replace(tzinfo=cutoff.tzinfo)
        elif generation_start.tzinfo is not None and cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=generation_start.tzinfo)
        if generation_start > cutoff:
            cutoff = generation_start
    rows: list[dict[str, Any]] = []
    for item in branch.get("samples") or []:
        if not isinstance(item, Mapping):
            continue
        at = _dt(item.get("at"))
        if at is None:
            continue
        try:
            if at >= cutoff:
                rows.append(dict(item))
        except TypeError:
            # Old naive values are interpreted in their own local ordering; do
            # not let one malformed historical timestamp break current forecasts.
            rows.append(dict(item))
    return rows


def build_profiles(samples: Sequence[Mapping[str, Any]], metric: str | None = None) -> dict[str, Any]:
    exact: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    zone_exact: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    for sample in samples:
        params = parameter_signature(sample.get("params") or {}, metric)
        zones = zone_signature(sample.get("zone_ids") or [row.get("zone_id") for row in sample.get("zones") or []])
        value = _number(sample.get("job_value"))
        if zones and value is not None and value >= 0:
            exact[(zones, params)].append(value)
        for zone in sample.get("zones") or []:
            if not isinstance(zone, Mapping):
                continue
            zone_id = str(zone.get("zone_id") or "")
            zone_value = _number(zone.get("value"))
            if zone_id and zone_value is not None and zone_value >= 0:
                zone_exact[(zone_id, params)].append(zone_value)
    return {"exact": exact, "zone_exact": zone_exact}


def estimate_from_samples(
    samples: Sequence[Mapping[str, Any]], *, zone_ids: Iterable[Any], cleaning_params: Mapping[str, Any] | None,
    policy: ForecastPolicyConfig, minimum_samples: int = DEFAULT_MINIMUM_SAMPLES, metric: str | None = None,
) -> dict[str, Any]:
    minimum = max(1, int(minimum_samples))
    if metric in {"clean_water", "dirty_water"}:
        scope = cleaning_scope(cleaning_params)
        if scope.explicitly_dry:
            return {
                "available": True,
                "basis": "dry_cleaning",
                "sample_count": 0,
                "minimum_samples": minimum,
                "percentile": policy.percentile,
                "delta_percent": policy.delta_percent,
                "median_value": 0.0,
                "raw_percentile_value": 0.0,
                "forecast_value": 0.0,
                "distribution": {
                    "count": 0, "mean": 0.0, "median": 0.0,
                    "percentile": 0.0, "percentile_number": int(policy.percentile),
                },
            }
        if scope.kind != "wet":
            return {
                "available": False,
                "basis": "cleaning_scope_unknown",
                "sample_count": 0,
                "minimum_samples": minimum,
                "percentile": policy.percentile,
                "delta_percent": policy.delta_percent,
                "median_value": None,
                "raw_percentile_value": None,
                "forecast_value": None,
            }
    profiles = build_profiles(samples, metric)
    zones = zone_signature(zone_ids)
    params = parameter_signature(cleaning_params, metric)

    def make(values: Sequence[float], basis: str) -> dict[str, Any] | None:
        if len(values) < minimum:
            return None
        dist = distribution(values, policy.percentile)
        raw = _number(dist.get("percentile"))
        if raw is None or raw < 0:
            return None
        forecast = raw * (1.0 + policy.delta_percent / 100.0)
        return {
            "available": True,
            "basis": basis,
            "sample_count": len(values),
            "minimum_samples": minimum,
            "percentile": policy.percentile,
            "delta_percent": policy.delta_percent,
            "median_value": dist.get("median"),
            "raw_percentile_value": raw,
            "forecast_value": forecast,
            "distribution": dist,
        }

    exact_values = list(profiles["exact"].get((zones, params), ()))
    result = make(exact_values, "zones_and_parameters")
    if result is not None:
        return result

    if zones:
        exact_parts = [(zone_id, list(profiles["zone_exact"].get((zone_id, params), ()))) for zone_id in zones]
        if all(len(values) >= minimum for _, values in exact_parts):
            raw_parts = [_percentile(values, policy.percentile) for _, values in exact_parts]
            if all(value is not None for value in raw_parts):
                raw = sum(float(value) for value in raw_parts if value is not None)
                med = sum(float(median(values)) for _, values in exact_parts)
                return {
                    "available": True,
                    "basis": "zone_composite_and_parameters",
                    "sample_count": min(len(values) for _, values in exact_parts),
                    "minimum_samples": minimum,
                    "percentile": policy.percentile,
                    "delta_percent": policy.delta_percent,
                    "median_value": med,
                    "raw_percentile_value": raw,
                    "forecast_value": raw * (1.0 + policy.delta_percent / 100.0),
                    "zone_samples": {
                        zone_id: {"count": len(values), "percentile_value": _percentile(values, policy.percentile)}
                        for zone_id, values in exact_parts
                    },
                }

    # There is no defensible correction model between materially different
    # cleaning profiles.  Reusing the same-zone history here used to mix, for
    # example, vacuum 1x and vacuum+mop 2x into one distribution.  That made
    # distinct rows receive the same estimate and also polluted predictive
    # pre-flight / Force protection.  Fail open until the exact physical
    # profile (whole job or per-zone composite) has enough observations.
    return {
        "available": False,
        "basis": "insufficient_matching_water_profile" if metric in {"clean_water", "dirty_water"} else "insufficient_matching_profile",
        "sample_count": len(exact_values),
        "minimum_samples": minimum,
        "percentile": policy.percentile,
        "delta_percent": policy.delta_percent,
        "median_value": None,
        "raw_percentile_value": None,
        "forecast_value": None,
    }


def model_tables(
    samples: Sequence[Mapping[str, Any]], policy: ForecastPolicyConfig, *,
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES, metric: str | None = None,
) -> dict[str, Any]:
    """Build transparent schedule/zone tables from the active trained sample set."""
    schedule_groups: dict[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    schedule_names: dict[str, str] = {}
    schedule_zone_names: dict[tuple[str, tuple[str, ...]], tuple[str, ...]] = {}
    schedule_latest: dict[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]], datetime] = {}
    zone_groups: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    zone_names: dict[str, str] = {}
    zone_latest: dict[tuple[str, tuple[tuple[str, str], ...]], datetime] = {}

    def remember_latest(target: dict[Any, datetime], key: Any, candidate: datetime | None) -> None:
        if candidate is None:
            return
        current = target.get(key)
        if current is None:
            target[key] = candidate
            return
        try:
            newer = candidate > current
        except TypeError:
            # Legacy branches may mix offset-naive and offset-aware timestamps.
            # Freshness is only a presentation tie-break, so compare wall-clock
            # values rather than letting old data break the Estimate table.
            newer = candidate.replace(tzinfo=None) > current.replace(tzinfo=None)
        if newer:
            target[key] = candidate

    for sample in samples:
        params = parameter_signature(sample.get("params") or {}, metric)
        value = _number(sample.get("job_value"))
        sample_at = _dt(sample.get("at"))
        schedule_id = str(sample.get("schedule_id") or "")
        zones = zone_signature(sample.get("zone_ids") or [row.get("zone_id") for row in sample.get("zones") or []])
        if schedule_id and zones and value is not None:
            schedule_key = (schedule_id, zones, params)
            schedule_groups[schedule_key].append(value)
            remember_latest(schedule_latest, schedule_key, sample_at)
            schedule_names[schedule_id] = str(sample.get("schedule_name") or schedule_id)
            zone_name_map = {str(row.get("zone_id") or ""): str(row.get("zone_name") or row.get("zone_id") or "") for row in sample.get("zones") or [] if isinstance(row, Mapping)}
            schedule_zone_names[(schedule_id, zones)] = tuple(zone_name_map.get(zone_id, zone_id) for zone_id in zones)
        for zone in sample.get("zones") or []:
            if not isinstance(zone, Mapping):
                continue
            zone_id = str(zone.get("zone_id") or "")
            zone_value = _number(zone.get("value"))
            if zone_id and zone_value is not None:
                zone_key = (zone_id, params)
                zone_groups[zone_key].append(zone_value)
                remember_latest(zone_latest, zone_key, sample_at)
                zone_names[zone_id] = str(zone.get("zone_name") or zone_id)

    def schedule_rows():
        output = []
        for (schedule_id, zones, params), values in schedule_groups.items():
            # The Forecast tab must expose the *effective* estimate that Scheduler
            # would use, not only direct samples owned by this schedule row. Exact
            # same-profile observations from other schedules and per-zone partial
            # completions may train it; different cleaning profiles never may.
            profile_params = {key: value for key, value in params}
            estimate = estimate_from_samples(
                samples, zone_ids=zones, cleaning_params=profile_params,
                policy=policy, minimum_samples=minimum_samples, metric=metric,
            )
            trained = bool(estimate.get("available"))
            direct_dist = distribution(values, policy.percentile)
            output.append({
                "schedule_id": schedule_id,
                "schedule_name": schedule_names.get(schedule_id, schedule_id),
                "zone_ids": list(zones),
                "zone_names": list(schedule_zone_names.get((schedule_id, zones), zones)),
                "profile_params": profile_params,
                "direct_sample_count": len(values),
                "sample_count": int(estimate.get("sample_count") or 0),
                "basis": str(estimate.get("basis") or "insufficient_real_history"),
                "zone_samples": estimate.get("zone_samples"),
                "trained": trained,
                "percentile": policy.percentile,
                "median_value": estimate.get("median_value") if trained else direct_dist.get("median"),
                "raw_percentile_value": estimate.get("raw_percentile_value") if trained else None,
                "delta_percent": policy.delta_percent,
                "forecast_value": estimate.get("forecast_value") if trained else None,
                "last_sample_at": schedule_latest.get((schedule_id, zones, params)).isoformat() if schedule_latest.get((schedule_id, zones, params)) is not None else None,
            })
        output.sort(key=lambda row: (str(row.get("schedule_name") or "").lower(), str(row.get("zone_ids") or ""), str(row.get("profile_params") or "")))
        return output

    def zone_rows():
        output = []
        for (zone_id, params), values in zone_groups.items():
            dist = distribution(values, policy.percentile)
            raw = _number(dist.get("percentile"))
            trained = len(values) >= minimum_samples and raw is not None
            output.append({
                "zone_id": zone_id,
                "zone_name": zone_names.get(zone_id, zone_id),
                "profile_params": {key: value for key, value in params},
                "sample_count": len(values),
                "trained": trained,
                "percentile": policy.percentile,
                "median_value": dist.get("median"),
                "raw_percentile_value": raw if trained else None,
                "delta_percent": policy.delta_percent,
                "forecast_value": (raw * (1.0 + policy.delta_percent / 100.0)) if trained and raw is not None else None,
                "last_sample_at": zone_latest.get((zone_id, params)).isoformat() if zone_latest.get((zone_id, params)) is not None else None,
            })
        output.sort(key=lambda row: (str(row.get("zone_name") or "").lower(), str(row.get("profile_params") or "")))
        return output

    return {"by_schedule": schedule_rows(), "by_zone": zone_rows()}


def archive_branch(branch: Mapping[str, Any], *, retain_days: int, now: datetime) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Archive samples older than the requested retained tail and rotate generation."""
    retain = max(0, min(3650, int(retain_days)))
    cutoff = now - timedelta(days=retain)
    old_samples: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for item in branch.get("samples") or []:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        at = _dt(row.get("at"))
        is_old = retain == 0
        if not is_old and at is not None:
            try:
                is_old = at < cutoff
            except TypeError:
                # Historical naive timestamps are retained rather than silently
                # archived on an ambiguous timezone comparison.
                is_old = False
        if is_old:
            old_samples.append(row)
        else:
            retained.append(row)
    if not old_samples and retain > 0:
        return normalize_branch(branch, now=now), None
    archives = [dict(item) for item in branch.get("archives") or [] if isinstance(item, Mapping)]
    archive = None
    if old_samples:
        archive = {
            "archive_id": uuid4().hex,
            "source_generation": int(branch.get("generation") or 1),
            "archived_at": now.isoformat(),
            "retain_days": retain,
            "cutoff_at": cutoff.isoformat(),
            "sample_count": len(old_samples),
            "samples": old_samples,
        }
        archives.append(archive)
    next_branch = {
        "generation": int(branch.get("generation") or 1) + 1,
        "started_at": cutoff.isoformat() if retain > 0 else now.isoformat(),
        "updated_at": now.isoformat(),
        "samples": retained,
        "archives": archives[-100:],
    }
    return next_branch, archive
