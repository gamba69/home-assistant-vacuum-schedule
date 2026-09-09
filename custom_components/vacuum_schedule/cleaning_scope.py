"""Canonical cleaning-scope semantics shared across scheduler subsystems.

A schedule snapshot can legally retain base mop/water fields when a weekday
override changes only ``cleaning_mode``.  Every consumer must therefore honor an
explicit cleaning mode before looking at those inherited fields.  Keeping this
logic in one module prevents pre-flight, execution, statistics and forecasting
from disagreeing about whether a Job actually uses water.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_NONE_VALUES = {"", "none", "off", "__none__", "disabled", "false", "0"}
_DRY_MODES = {
    "vacuum",
    "vacuum_only",
    "vacuum-only",
    "vacuum only",
    "sweep",
    "sweeping",
}
_MOP_ONLY_MODES = {
    "mop",
    "mop_only",
    "mop-only",
    "mop only",
    "wash",
    "washing",
}
_WET_COMBINED_MODES = {
    "vac_and_mop",
    "vacuum_and_mop",
    "vacuum-and-mop",
    "vacuum+mop",
    "vacuum_mop",
    "sweep_and_mop",
    "sweep-and-mop",
}
_AMBIGUOUS_MODES = {"", "default", "standard", "custom", "auto", "automatic", "__none__", "none"}


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _enabled_setting(value: Any) -> bool:
    return _token(value) not in _NONE_VALUES


@dataclass(slots=True, frozen=True)
class CleaningScope:
    """Resolved physical scope for one effective cleaning-parameter snapshot."""

    kind: str  # dry | wet | unknown
    vacuum: bool | None
    mop: bool | None
    source: str

    @property
    def uses_water(self) -> bool:
        return self.kind == "wet"

    @property
    def explicitly_dry(self) -> bool:
        return self.kind == "dry"


def cleaning_scope(params: Mapping[str, Any] | None) -> CleaningScope:
    """Resolve dry/wet semantics, giving explicit ``cleaning_mode`` precedence.

    Legacy/default snapshots may not specify a decisive cleaning mode.  Only in
    that case do mop/water overrides provide a fallback signal.  If neither is
    decisive the result is ``unknown`` rather than silently assuming zero water.
    """

    values = dict(params or {})
    mode = _token(values.get("cleaning_mode"))
    if mode in _DRY_MODES:
        return CleaningScope("dry", True, False, "cleaning_mode")
    if mode in _MOP_ONLY_MODES:
        return CleaningScope("wet", False, True, "cleaning_mode")
    if mode in _WET_COMBINED_MODES:
        return CleaningScope("wet", True, True, "cleaning_mode")

    # Unknown vendor values are not overridden by stale inherited mop fields.
    # Only documented ambiguous/default modes may use those fields as fallback.
    if mode and mode not in _AMBIGUOUS_MODES:
        return CleaningScope("unknown", None, None, "unknown_cleaning_mode")

    if _enabled_setting(values.get("mop_mode")) or _enabled_setting(values.get("water_mode")):
        return CleaningScope("wet", None, True, "mop_or_water_mode")
    return CleaningScope("unknown", None, None, "insufficient_parameters")


def canonicalize_effective_cleaning_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy with parameters irrelevant to an explicit dry Job cleared.

    This normalization is intentionally applied to occurrence snapshots only.
    The base schedule profile is not mutated, so a Tuesday ``vacuum`` override
    cannot erase Wednesday's configured mop/water settings.
    """

    result = dict(params or {})
    if cleaning_scope(result).explicitly_dry:
        result["mop_mode"] = ""
        result["water_mode"] = ""
    return result


def canonicalize_forecast_profile_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize only parameters that are physically relevant to one profile.

    Forecast grouping is query-time and must remain compatible with historical
    samples.  Old snapshots can retain settings that the effective cleaning mode
    does not use (for example mop/water values on a vacuum-only weekday).  Such
    stale fields must not split one physical profile into multiple models.
    """

    result = dict(params or {})
    scope = cleaning_scope(result)
    if scope.explicitly_dry:
        result["mop_mode"] = ""
        result["water_mode"] = ""
    elif scope.kind == "wet" and scope.vacuum is False:
        # Mop-only execution does not use suction/route settings.
        result["fan_mode"] = ""
        result["cleaning_route"] = ""
    if result.get("passes") in (None, "", "__none__"):
        result["passes"] = 1
    return result


def water_parameter_signature(params: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """Return only parameters that can materially distinguish water demand."""

    values = canonicalize_forecast_profile_params(params)
    scope = cleaning_scope(values)
    output: list[tuple[str, str]] = [("scope", scope.kind)]
    for key in ("cleaning_mode", "mop_mode", "water_mode", "passes"):
        value = values.get(key)
        if value not in (None, "", "__none__"):
            output.append((key, str(value)))
    return tuple(output)
