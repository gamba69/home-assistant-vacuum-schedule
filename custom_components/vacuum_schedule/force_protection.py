"""Future-plan protection for early (Force) execution.

Since 0.11.0 Force uses the same user-visible time Forecast Model as normal
predictive pre-flight.  There is no hidden five-minute buffer and no unknown-
duration fallback: an unavailable/untrained forecast is fail-open.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

# Kept as compatibility symbols for older imports. 0.11.0 deliberately makes
# both zero; user-configured forecast delta is the only safety margin.
DEFAULT_SAFETY_BUFFER_SECONDS = 0
DEFAULT_UNKNOWN_DURATION_MIN_GAP_SECONDS = 0


def evaluate_force_plan_protection(
    *,
    now: datetime,
    next_planned_start: datetime | None,
    duration_estimate: Mapping[str, Any] | None,
    safety_buffer_seconds: int = DEFAULT_SAFETY_BUFFER_SECONDS,
    unknown_duration_min_gap_seconds: int = DEFAULT_UNKNOWN_DURATION_MIN_GAP_SECONDS,
) -> dict[str, Any]:
    """Return a JSON-friendly Force admission decision.

    ``forecast_value`` already contains the selected percentile plus the user's
    delta.  If the time forecast is disabled or unavailable, Force is admitted
    by this protection layer.
    """
    estimate = dict(duration_estimate or {})
    enabled = bool(estimate.get("enabled"))
    available = enabled and bool(estimate.get("available"))
    raw = estimate.get("forecast_value") if available else None
    try:
        forecast_seconds = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        forecast_seconds = None

    if next_planned_start is None:
        return {
            "allowed": True,
            "reason": "no_future_planned_job",
            "next_planned_start": None,
            "available_gap_seconds": None,
            "estimate_available": forecast_seconds is not None,
            "estimate": estimate,
            "safety_buffer_seconds": 0,
            "required_gap_seconds": None,
            "latest_safe_start": None,
        }

    try:
        available_gap = max(0.0, (next_planned_start - now).total_seconds())
    except TypeError:
        available_gap = 0.0

    if forecast_seconds is None or forecast_seconds < 0:
        return {
            "allowed": True,
            "reason": "forecast_disabled_or_unavailable",
            "next_planned_start": next_planned_start.isoformat(),
            "available_gap_seconds": int(available_gap),
            "estimate_available": False,
            "estimate": estimate,
            "safety_buffer_seconds": 0,
            "required_gap_seconds": 0,
            "latest_safe_start": next_planned_start.isoformat(),
        }

    required_gap = max(0.0, forecast_seconds)
    allowed = available_gap >= required_gap
    latest_safe_start = next_planned_start - timedelta(seconds=required_gap)
    return {
        "allowed": bool(allowed),
        "reason": "forecast" if allowed else "forecast_insufficient_gap",
        "next_planned_start": next_planned_start.isoformat(),
        "available_gap_seconds": int(available_gap),
        "estimate_available": True,
        "estimate": estimate,
        "safety_buffer_seconds": 0,
        "required_gap_seconds": int(required_gap),
        "latest_safe_start": latest_safe_start.isoformat(),
    }
