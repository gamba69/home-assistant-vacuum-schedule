"""Scheduler cleaning-zone models for Vacuum Schedule 0.5.0.

A scheduler cleaning zone is intentionally a one-to-one wrapper around exactly
one physical vacuum target. Room/HA Area abstractions are not part of this
model. Presence, route accessibility and manual enable/disable state belong to
the scheduler zone itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence
from uuid import uuid4


try:
    from .time_utils import instant_lt
except ImportError:  # pragma: no cover - isolated source-file testing
    from time_utils import instant_lt


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    ABOVE = "above"
    BELOW = "below"
    IN = "in"


class RobotTargetType(StrEnum):
    SEGMENT = "segment"
    ZONE = "zone"


class CleaningZoneControl(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DISABLED_UNTIL = "disabled_until"


@dataclass(slots=True, frozen=True)
class EntityCondition:
    """A condition against an entity state or attribute."""

    entity_id: str
    entity_registry_id: str | None = None
    attribute: str | None = None
    operator: ConditionOperator = ConditionOperator.EQUALS
    value: Any = "on"
    label: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntityCondition":
        return cls(
            entity_id=str(data.get("entity_id", "")).strip(),
            entity_registry_id=(str(data["entity_registry_id"]) if data.get("entity_registry_id") else None),
            attribute=str(data["attribute"]).strip() if data.get("attribute") else None,
            operator=ConditionOperator(str(data.get("operator", ConditionOperator.EQUALS.value))),
            value=data.get("value", "on"),
            label=str(data["label"]) if data.get("label") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_registry_id": self.entity_registry_id,
            "attribute": self.attribute,
            "operator": self.operator.value,
            "value": self.value,
            "label": self.label,
        }


@dataclass(slots=True, frozen=True)
class AccessPath:
    """One alternative path. All conditions inside the path use logical AND."""

    path_id: str
    name: str
    conditions: tuple[EntityCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccessPath":
        return cls(
            path_id=str(data.get("path_id") or uuid4().hex),
            name=str(data.get("name") or "Path").strip(),
            conditions=tuple(
                EntityCondition.from_dict(item)
                for item in data.get("conditions", ())
                if isinstance(item, Mapping) and item.get("entity_id")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "name": self.name,
            "conditions": [item.to_dict() for item in self.conditions],
        }


@dataclass(slots=True, frozen=True)
class CleaningZone:
    """One scheduler zone mapped to exactly one physical vacuum target."""

    zone_id: str
    name: str
    robot_target_type: RobotTargetType
    robot_target_id: str
    control: CleaningZoneControl = CleaningZoneControl.ENABLED
    disabled_until: str | None = None
    busy_sources: tuple[EntityCondition, ...] = ()
    access_paths: tuple[AccessPath, ...] = ()
    occupancy_clear_delay_seconds: int | None = None
    access_stable_delay_seconds: int | None = None
    nominal_area_m2: float | None = None

    def __post_init__(self) -> None:
        if not self.zone_id.strip():
            raise ValueError("missing_zone_id")
        if not self.name.strip():
            raise ValueError("missing_zone_name")
        if not self.robot_target_id.strip():
            raise ValueError("missing_robot_target")
        if self.control is CleaningZoneControl.DISABLED_UNTIL and not self.disabled_until:
            raise ValueError("missing_disabled_until")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CleaningZone":
        target_id = data.get("robot_target_id")
        # Compatibility with the superseded early-0.5 RoomProfile payload. This
        # fallback is only safe for a single target; multi-target splitting is
        # handled explicitly by config-entry migration.
        if not target_id:
            values = data.get("robot_target_ids") or ()
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and len(values) == 1:
                target_id = values[0]
        return cls(
            zone_id=str(data.get("zone_id") or data.get("room_id") or uuid4().hex),
            name=str(data.get("name") or "Cleaning zone").strip(),
            robot_target_type=RobotTargetType(str(data.get("robot_target_type") or RobotTargetType.SEGMENT.value)),
            robot_target_id=str(target_id or "").strip(),
            control=CleaningZoneControl(str(data.get("control") or data.get("enabled_mode") or CleaningZoneControl.ENABLED.value)),
            disabled_until=(str(data["disabled_until"]) if data.get("disabled_until") else None),
            busy_sources=tuple(
                EntityCondition.from_dict(item)
                for item in data.get("busy_sources", ())
                if isinstance(item, Mapping) and item.get("entity_id")
            ),
            access_paths=tuple(
                AccessPath.from_dict(item)
                for item in data.get("access_paths", ())
                if isinstance(item, Mapping)
            ),
            occupancy_clear_delay_seconds=_optional_nonnegative_int(data.get("occupancy_clear_delay_seconds")),
            access_stable_delay_seconds=_optional_nonnegative_int(data.get("access_stable_delay_seconds")),
            nominal_area_m2=_optional_positive_float(data.get("nominal_area_m2")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "robot_target_type": self.robot_target_type.value,
            "robot_target_id": self.robot_target_id,
            "control": self.control.value,
            "disabled_until": self.disabled_until,
            "busy_sources": [item.to_dict() for item in self.busy_sources],
            "access_paths": [item.to_dict() for item in self.access_paths],
            "occupancy_clear_delay_seconds": self.occupancy_clear_delay_seconds,
            "access_stable_delay_seconds": self.access_stable_delay_seconds,
            "nominal_area_m2": self.nominal_area_m2,
        }

    @property
    def target_key(self) -> tuple[str, str]:
        return (self.robot_target_type.value, self.robot_target_id)

    @property
    def dependency_entity_ids(self) -> tuple[str, ...]:
        values = {item.entity_id for item in self.busy_sources if item.entity_id}
        for path in self.access_paths:
            values.update(item.entity_id for item in path.conditions if item.entity_id)
        return tuple(sorted(values))

    def disabled_at(self, now: datetime) -> bool:
        if self.control is CleaningZoneControl.DISABLED:
            return True
        if self.control is not CleaningZoneControl.DISABLED_UNTIL:
            return False
        if not self.disabled_until:
            return True
        try:
            until = datetime.fromisoformat(self.disabled_until)
        except ValueError:
            return True
        if until.tzinfo is None and now.tzinfo is not None:
            until = until.replace(tzinfo=now.tzinfo)
        elif until.tzinfo is not None and now.tzinfo is not None:
            until = until.astimezone(now.tzinfo)
        return instant_lt(now, until)



def _optional_nonnegative_int(value: Any) -> int | None:
    """Parse a per-zone delay override; None means use the global default."""
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_positive_float(value: Any) -> float | None:
    """Parse an optional positive nominal zone area in square metres."""
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_nominal_area") from None
    if parsed <= 0:
        raise ValueError("invalid_nominal_area")
    return round(parsed, 3)


def effective_zone_delay(zone_value: int | None, global_value: int) -> int:
    """Resolve a per-zone override against its entry-wide default."""
    return max(0, int(global_value if zone_value is None else zone_value))

def aggregate_access_paths(path_results: Sequence[tuple[bool, bool]]) -> tuple[bool, bool]:
    """Return (accessible, known) for OR-combined alternative access paths."""
    if not path_results:
        return True, True
    if any(is_open for is_open, _known in path_results):
        return True, True
    return False, all(known for _is_open, known in path_results)


def _to_number(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return float(int(value))
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalized_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"true", "on", "yes", "1"}:
        return True
    if lowered in {"false", "off", "no", "0"}:
        return False
    number = _to_number(value)
    if number is not None:
        return number
    return text


def condition_matches(actual: Any, condition: EntityCondition) -> bool:
    """Evaluate a configured condition against a raw value."""
    expected = condition.value
    if condition.operator in {ConditionOperator.ABOVE, ConditionOperator.BELOW}:
        left = _to_number(actual)
        right = _to_number(expected)
        if left is None or right is None:
            return False
        return left > right if condition.operator is ConditionOperator.ABOVE else left < right
    if condition.operator is ConditionOperator.IN:
        if isinstance(expected, (list, tuple, set)):
            candidates = list(expected)
        else:
            candidates = [item.strip() for item in str(expected).split(",") if item.strip()]
        left = _normalized_scalar(actual)
        return any(left == _normalized_scalar(item) for item in candidates)
    equal = _normalized_scalar(actual) == _normalized_scalar(expected)
    return not equal if condition.operator is ConditionOperator.NOT_EQUALS else equal


def cleaning_zones_from_options(raw: Sequence[Any] | None) -> list[CleaningZone]:
    """Deserialize configured cleaning zones without silently repairing conflicts.

    Physical-target uniqueness is enforced by configuration save/validation. Keeping
    all valid records here is deliberate: malformed imported options must remain
    visible to diagnostics instead of one duplicate being silently discarded.
    """
    result: list[CleaningZone] = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        try:
            result.append(CleaningZone.from_dict(item))
        except (TypeError, ValueError):
            continue
    return result
