"""Pre-flight report models for Vacuum Schedule 0.5.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class PreflightDecision(StrEnum):
    PASS = "PASS"
    WAIT = "WAIT"
    FAIL = "FAIL"


class PreflightPhase(StrEnum):
    ADVISORY = "ADVISORY"
    AUTHORITATIVE = "AUTHORITATIVE"
    CURRENT_PREVIEW = "CURRENT_PREVIEW"


class BlockerScope(StrEnum):
    """UI/operational scope of a pre-flight blocker."""

    SYSTEM = "SYSTEM"
    ROBOT = "ROBOT"
    JOB = "JOB"
    ZONE = "ZONE"


class BlockerAttention(StrEnum):
    """Whether a blocker deserves persistent user attention."""

    NONE = "NONE"
    INFORMATION = "INFORMATION"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    CRITICAL = "CRITICAL"


_CRITICAL_BLOCKERS = frozenset({
    "invalid_configuration",
    "vacuum_error",
})
_ACTION_REQUIRED_BLOCKERS = frozenset({
    "clean_water_insufficient",
    "dirty_water_full",
    "detergent_unavailable",
    "mop_not_attached",
    "invalid_target",
    "forecast_clean_water_insufficient",
    "forecast_dirty_water_insufficient",
})
_INFORMATION_BLOCKERS = frozenset({
    "global_disabled",
    "global_disabled_until",
})


def classify_blocker(
    code: str, source_key: str, room_id: str | None = None
) -> tuple[BlockerScope, BlockerAttention]:
    """Return stable presentation semantics independent of WAIT/FAIL severity.

    A blocker can be operationally blocking without being globally important.
    For example a closed access path is a normal ZONE WAIT and must not become
    a persistent top-of-panel alert. Conversely a serviceable water resource
    requires explicit user action and should remain visible globally.
    """
    normalized = str(code or "")
    source = str(source_key or "")
    if room_id or normalized.startswith("zone_"):
        scope = BlockerScope.ZONE
    elif normalized in {"global_disabled", "global_disabled_until", "invalid_configuration"}:
        scope = BlockerScope.SYSTEM
    elif normalized.startswith("vacuum_") or source.startswith(("vacuum.", "dock.", "mop.")):
        scope = BlockerScope.ROBOT
    else:
        scope = BlockerScope.JOB

    if normalized in _CRITICAL_BLOCKERS:
        attention = BlockerAttention.CRITICAL
    elif normalized in _ACTION_REQUIRED_BLOCKERS:
        attention = BlockerAttention.ACTION_REQUIRED
    elif normalized in _INFORMATION_BLOCKERS:
        attention = BlockerAttention.INFORMATION
    else:
        attention = BlockerAttention.NONE
    return scope, attention


@dataclass(slots=True, frozen=True)
class Blocker:
    code: str
    decision_class: PreflightDecision
    source_key: str
    entity_id: str | None = None
    room_id: str | None = None
    raw_value: Any = None
    normalized_value: Any = None
    translation_key: str | None = None
    scope: BlockerScope = BlockerScope.JOB
    attention: BlockerAttention = BlockerAttention.NONE
    details: Mapping[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    expected_release_condition: str | None = None
    estimated_release_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "decision_class": self.decision_class.value,
            "source_key": self.source_key,
            "entity_id": self.entity_id,
            "room_id": self.room_id,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "translation_key": self.translation_key or self.code,
            "scope": self.scope.value,
            "attention": self.attention.value,
            "details": dict(self.details),
            "dependencies": list(self.dependencies),
            "expected_release_condition": self.expected_release_condition,
            "estimated_release_at": self.estimated_release_at.isoformat() if self.estimated_release_at else None,
        }


@dataclass(slots=True, frozen=True)
class PreflightReport:
    decision: PreflightDecision
    phase: PreflightPhase
    blockers: tuple[Blocker, ...]
    evaluated_at: datetime
    input_snapshot_id: str
    dependencies: tuple[str, ...] = ()
    next_recheck_at: datetime | None = None
    input_snapshot: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.blockers)

    def to_dict(self, *, include_snapshot: bool = True) -> dict[str, Any]:
        result = {
            "decision": self.decision.value,
            "phase": self.phase.value,
            "blockers": [item.to_dict() for item in self.blockers],
            "blocker_codes": list(self.blocker_codes),
            "evaluated_at": self.evaluated_at.isoformat(),
            "input_snapshot_id": self.input_snapshot_id,
            "dependencies": list(self.dependencies),
            "next_recheck_at": self.next_recheck_at.isoformat() if self.next_recheck_at else None,
        }
        if include_snapshot:
            result["input_snapshot"] = dict(self.input_snapshot)
        return result


def dominant_fail_reason(report: PreflightReport, fallback: str = "preflight_failed") -> str:
    """Return the first deterministic FAIL blocker code from a report."""
    for blocker in report.blockers:
        if blocker.decision_class is PreflightDecision.FAIL:
            return str(blocker.code)
    return str(fallback)
