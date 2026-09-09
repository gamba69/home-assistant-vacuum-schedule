"""Schedule data model for Vacuum Schedule.

This module deliberately has no Home Assistant imports. The calendar model is
kept pure so it can be unit-tested independently from Home Assistant runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import hashlib
from typing import Any, Mapping, Sequence
from uuid import uuid4

try:
    from .cleaning_scope import canonicalize_effective_cleaning_params
    from .const import (
        DEFAULT_EXECUTION_WINDOW_MINUTES,
        DEFAULT_PASSES,
        DEFAULT_PREWARNING_MINUTES,
        DEFAULT_ZONE_EXECUTION_POLICY,
        LEGACY_ZONE_EXECUTION_POLICY,
        TARGET_TYPE_CLEANING_ZONES,
        TARGET_TYPES,
        WEEKDAY_TO_INDEX,
        ZONE_EXECUTION_POLICIES,
    )
except ImportError:  # pragma: no cover - allows isolated source-file testing
    def canonicalize_effective_cleaning_params(params):
        result = dict(params or {})
        if str(result.get("cleaning_mode") or "").strip().lower() == "vacuum":
            result["mop_mode"] = ""
            result["water_mode"] = ""
        return result
    DEFAULT_EXECUTION_WINDOW_MINUTES = 120
    DEFAULT_PASSES = 1
    DEFAULT_PREWARNING_MINUTES = 15
    DEFAULT_ZONE_EXECUTION_POLICY = "combined"
    LEGACY_ZONE_EXECUTION_POLICY = "progressive"
    ZONE_EXECUTION_POLICIES = ("combined", "progressive")
    TARGET_TYPE_CLEANING_ZONES = "cleaning_zones"
    TARGET_TYPES = (TARGET_TYPE_CLEANING_ZONES,)
    WEEKDAY_TO_INDEX = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }


class ScheduleValidationError(ValueError):
    """Raised when a schedule definition is invalid."""


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        if value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError as err:
        raise ScheduleValidationError("invalid_local_time") from err
    return parsed.replace(tzinfo=None)


def _parse_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as err:
        raise ScheduleValidationError("invalid_calendar_date") from err


def _normalize_weekdays(values: Sequence[int | str]) -> tuple[int, ...]:
    normalized: set[int] = set()
    for value in values:
        if isinstance(value, str) and value.lower() in WEEKDAY_TO_INDEX:
            index = WEEKDAY_TO_INDEX[value.lower()]
        else:
            try:
                index = int(value)
            except (TypeError, ValueError) as err:
                raise ScheduleValidationError("invalid_weekday") from err
        if index < 0 or index > 6:
            raise ScheduleValidationError("invalid_weekday")
        normalized.add(index)
    return tuple(sorted(normalized))


def _weekday_index(value: int | str) -> int:
    """Normalize one weekday key used by flexible weekly schedules."""
    if isinstance(value, str) and value.lower() in WEEKDAY_TO_INDEX:
        index = WEEKDAY_TO_INDEX[value.lower()]
    else:
        try:
            index = int(value)
        except (TypeError, ValueError) as err:
            raise ScheduleValidationError("invalid_weekday") from err
    if index < 0 or index > 6:
        raise ScheduleValidationError("invalid_weekday")
    return index


def _normalize_weekday_times(
    value: Mapping[int | str, str | time] | None,
    *,
    weekdays: Sequence[int],
    local_time: time,
) -> dict[int, time]:
    """Return normalized per-weekday start times.

    Legacy schedules have no ``weekday_times`` mapping. They are migrated in
    memory by assigning their one historical ``local_time`` to every selected
    weekday, preserving pre-0.10 execution semantics exactly.
    """
    if value:
        result: dict[int, time] = {}
        for raw_day, raw_time in value.items():
            result[_weekday_index(raw_day)] = _parse_time(raw_time)
        return dict(sorted(result.items()))
    return {int(day): local_time for day in weekdays}


def _normalize_weekday_overrides(
    value: Mapping[int | str, Mapping[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    """Normalize sparse cleaning-parameter overrides by occurrence weekday."""
    result: dict[int, dict[str, Any]] = {}
    for raw_day, raw_params in (value or {}).items():
        day = _weekday_index(raw_day)
        if not isinstance(raw_params, Mapping):
            raise ScheduleValidationError("invalid_weekday_override")
        params = _clean_mapping(raw_params)
        passes = params.get("passes")
        if passes is not None:
            try:
                passes_value = int(passes)
            except (TypeError, ValueError) as err:
                raise ScheduleValidationError("invalid_passes") from err
            if passes_value < 1:
                raise ScheduleValidationError("invalid_passes")
            params["passes"] = passes_value
        if params:
            result[day] = params
    return dict(sorted(result.items()))


def _normalize_dates(values: Sequence[str | date]) -> tuple[date, ...]:
    return tuple(sorted({_parse_date(value) for value in values}))


def _normalize_targets(values: Sequence[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return tuple(result)


def _clean_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {str(key): item for key, item in value.items() if item is not None}




_FORCE_CONDITION_TYPES = {"entity", "numeric", "person", "people_absent", "binary"}
_FORCE_ENTITY_OPERATORS = {"eq", "ne"}
_FORCE_NUMERIC_OPERATORS = {"gt", "gte", "lt", "lte"}


def _normalize_force_condition_groups(value: Any) -> tuple[tuple[dict[str, Any], ...], ...]:
    """Normalize the intentionally shallow OR-of-AND force condition tree."""
    if value in (None, (), []):
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ScheduleValidationError("invalid_force_condition_groups")
    groups: list[tuple[dict[str, Any], ...]] = []
    for group_index, raw_group in enumerate(value):
        if not isinstance(raw_group, Sequence) or isinstance(raw_group, (str, bytes, bytearray)):
            raise ScheduleValidationError("invalid_force_condition_group")
        conditions: list[dict[str, Any]] = []
        for condition_index, raw in enumerate(raw_group):
            if not isinstance(raw, Mapping):
                raise ScheduleValidationError("invalid_force_condition")
            item = {str(key): val for key, val in raw.items() if val is not None}
            kind = str(item.get("type", "")).strip().lower()
            if kind not in _FORCE_CONDITION_TYPES:
                raise ScheduleValidationError("invalid_force_condition_type")
            condition_id = str(item.get("condition_id") or f"g{group_index + 1}c{condition_index + 1}").strip()
            if not condition_id:
                raise ScheduleValidationError("invalid_force_condition_id")
            normalized: dict[str, Any] = {
                "condition_id": condition_id,
                "type": kind,
                "for_minutes": max(0, int(item.get("for_minutes", 0) or 0)),
            }
            if kind == "people_absent":
                raw_entities = item.get("entity_ids", ())
                if not isinstance(raw_entities, Sequence) or isinstance(raw_entities, (str, bytes, bytearray)):
                    raise ScheduleValidationError("invalid_force_people")
                entity_ids = tuple(dict.fromkeys(str(entity).strip() for entity in raw_entities if str(entity).strip()))
                if not entity_ids or any("." not in entity for entity in entity_ids):
                    raise ScheduleValidationError("invalid_force_people")
                minimum_absent = int(item.get("minimum_absent", 1) or 1)
                if minimum_absent < 1 or minimum_absent > len(entity_ids):
                    raise ScheduleValidationError("invalid_force_minimum_absent")
                normalized["entity_ids"] = list(entity_ids)
                normalized["minimum_absent"] = minimum_absent
            else:
                entity_id = str(item.get("entity_id", "")).strip()
                if not entity_id or "." not in entity_id:
                    raise ScheduleValidationError("invalid_force_entity")
                normalized["entity_id"] = entity_id
                if kind == "entity":
                    operator = str(item.get("operator", "eq")).strip().lower()
                    if operator not in _FORCE_ENTITY_OPERATORS:
                        raise ScheduleValidationError("invalid_force_operator")
                    normalized["operator"] = operator
                    normalized["value"] = str(item.get("value", ""))
                elif kind == "numeric":
                    operator = str(item.get("operator", "gte")).strip().lower()
                    if operator not in _FORCE_NUMERIC_OPERATORS:
                        raise ScheduleValidationError("invalid_force_operator")
                    try:
                        normalized["value"] = float(item.get("value"))
                    except (TypeError, ValueError) as err:
                        raise ScheduleValidationError("invalid_force_numeric_value") from err
                    normalized["operator"] = operator
                elif kind == "person":
                    desired = str(item.get("state", "not_home")).strip().lower()
                    if desired not in {"home", "not_home"}:
                        raise ScheduleValidationError("invalid_force_person_state")
                    normalized["state"] = desired
                elif kind == "binary":
                    desired = str(item.get("state", "on")).strip().lower()
                    if desired not in {"on", "off"}:
                        raise ScheduleValidationError("invalid_force_binary_state")
                    normalized["state"] = desired
            conditions.append(normalized)
        if not conditions:
            raise ScheduleValidationError("empty_force_condition_group")
        groups.append(tuple(conditions))
    return tuple(groups)


def _force_groups_to_list(groups: Sequence[Sequence[Mapping[str, Any]]]) -> list[list[dict[str, Any]]]:
    return [[dict(condition) for condition in group] for group in groups]


def _normalize_force_condition_group_names(value: Any, group_count: int) -> tuple[str, ...]:
    """Normalize display-only names for shallow OR condition groups."""
    if group_count <= 0:
        return ()
    if value in (None, (), []):
        return tuple("" for _ in range(group_count))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ScheduleValidationError("invalid_force_condition_group_names")
    names = [str(item or "").strip()[:120] for item in value]
    if len(names) < group_count:
        names.extend("" for _ in range(group_count - len(names)))
    return tuple(names[:group_count])


def _legacy_execution_window(value: Any) -> int:
    """Normalize only the legacy zero-window bug; keep other validation strict."""
    parsed = int(value)
    return 1 if parsed == 0 else parsed


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    """Persistent user-defined calendar rule.

    ``schedule_id`` is stable across edits. ``revision`` increments for every
    calendar or execution-intent change, making planned occurrence identifiers
    deterministic and revision-aware.
    """

    schedule_id: str
    revision: int
    name: str
    enabled: bool
    weekdays: tuple[int, ...]
    dates: tuple[date, ...]
    local_time: time
    target_type: str
    targets: tuple[str, ...]
    zone_execution_policy: str = DEFAULT_ZONE_EXECUTION_POLICY
    cleaning_params: Mapping[str, Any] = field(default_factory=dict)
    prewarning_minutes: int = DEFAULT_PREWARNING_MINUTES
    execution_window_minutes: int = DEFAULT_EXECUTION_WINDOW_MINUTES
    notification_policy: Mapping[str, Any] = field(default_factory=dict)
    weekday_times: Mapping[int, time] = field(default_factory=dict)
    weekday_overrides: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    force_enabled: bool = False
    force_max_advance_minutes: int = 0
    force_priority: int = 0
    force_preempts_scheduled: bool = False
    force_condition_groups: tuple[tuple[Mapping[str, Any], ...], ...] = ()
    force_condition_group_names: tuple[str, ...] = ()
    paused: bool = False

    def __post_init__(self) -> None:
        # Direct dataclass construction is retained for compatibility with the
        # existing pure-model tests and third-party callers. Normalize missing
        # flexible fields exactly like ``create()`` would.
        if not self.weekday_times and self.weekdays:
            object.__setattr__(
                self,
                "weekday_times",
                {int(day): self.local_time for day in self.weekdays},
            )
        normalized_force_groups = _normalize_force_condition_groups(self.force_condition_groups)
        if normalized_force_groups != self.force_condition_groups:
            object.__setattr__(self, "force_condition_groups", normalized_force_groups)
        normalized_group_names = _normalize_force_condition_group_names(
            self.force_condition_group_names, len(normalized_force_groups)
        )
        if normalized_group_names != self.force_condition_group_names:
            object.__setattr__(self, "force_condition_group_names", normalized_group_names)
        if not self.schedule_id.strip():
            raise ScheduleValidationError("missing_schedule_id")
        if self.revision < 1:
            raise ScheduleValidationError("invalid_revision")
        if not self.name.strip():
            raise ScheduleValidationError("missing_schedule_name")
        if not self.weekdays and not self.dates:
            raise ScheduleValidationError("missing_calendar_rule")
        if set(self.weekday_times) != set(self.weekdays):
            raise ScheduleValidationError("weekday_times_mismatch")
        if any(day not in self.weekdays for day in self.weekday_overrides):
            raise ScheduleValidationError("weekday_override_for_disabled_day")
        if self.target_type not in TARGET_TYPES:
            raise ScheduleValidationError("invalid_target_type")
        if not self.targets:
            raise ScheduleValidationError("missing_targets")
        if self.zone_execution_policy not in ZONE_EXECUTION_POLICIES:
            raise ScheduleValidationError("invalid_zone_execution_policy")
        if self.prewarning_minutes < 0:
            raise ScheduleValidationError("invalid_prewarning")
        if self.execution_window_minutes < 1:
            raise ScheduleValidationError("invalid_execution_window")
        if self.force_max_advance_minutes < 0 or self.force_max_advance_minutes > 10080:
            raise ScheduleValidationError("invalid_force_max_advance")
        if self.force_enabled and self.force_max_advance_minutes < 1:
            raise ScheduleValidationError("force_window_required")
        if self.force_enabled and not self.force_condition_groups:
            raise ScheduleValidationError("force_conditions_required")
        passes = self.cleaning_params.get("passes")
        if passes is not None and int(passes) < 1:
            raise ScheduleValidationError("invalid_passes")

    @classmethod
    def create(
        cls,
        *,
        name: str,
        enabled: bool,
        weekdays: Sequence[int | str],
        dates: Sequence[str | date],
        local_time: str | time,
        target_type: str = TARGET_TYPE_CLEANING_ZONES,
        targets: Sequence[Any],
        paused: bool = False,
        zone_execution_policy: str = DEFAULT_ZONE_EXECUTION_POLICY,
        cleaning_params: Mapping[str, Any] | None = None,
        prewarning_minutes: int = DEFAULT_PREWARNING_MINUTES,
        execution_window_minutes: int = DEFAULT_EXECUTION_WINDOW_MINUTES,
        notification_policy: Mapping[str, Any] | None = None,
        weekday_times: Mapping[int | str, str | time] | None = None,
        weekday_overrides: Mapping[int | str, Mapping[str, Any]] | None = None,
        force_enabled: bool = False,
        force_max_advance_minutes: int = 0,
        force_priority: int = 0,
        force_preempts_scheduled: bool = False,
        force_condition_groups: Any = None,
        force_condition_group_names: Any = None,
        schedule_id: str | None = None,
        revision: int = 1,
    ) -> "ScheduleDefinition":
        """Create and normalize a schedule definition."""
        parsed_local_time = _parse_time(local_time)
        normalized_weekdays = _normalize_weekdays(weekdays)
        normalized_weekday_times = _normalize_weekday_times(
            weekday_times,
            weekdays=normalized_weekdays,
            local_time=parsed_local_time,
        )
        # A supplied flexible map is authoritative for recurring weekdays.
        # This lets the UI enable/disable a day atomically with its start time.
        if weekday_times:
            normalized_weekdays = tuple(sorted(normalized_weekday_times))
        return cls(
            schedule_id=schedule_id or uuid4().hex,
            revision=int(revision),
            name=name.strip(),
            enabled=bool(enabled),
            weekdays=normalized_weekdays,
            dates=_normalize_dates(dates),
            local_time=parsed_local_time,
            target_type=str(target_type),
            targets=_normalize_targets(targets),
            paused=bool(paused),
            zone_execution_policy=str(zone_execution_policy),
            cleaning_params=_clean_mapping(cleaning_params),
            prewarning_minutes=int(prewarning_minutes),
            execution_window_minutes=int(execution_window_minutes),
            notification_policy=_clean_mapping(notification_policy),
            weekday_times=normalized_weekday_times,
            weekday_overrides=_normalize_weekday_overrides(weekday_overrides),
            force_enabled=bool(force_enabled),
            force_max_advance_minutes=max(0, int(force_max_advance_minutes or 0)),
            force_priority=int(force_priority or 0),
            force_preempts_scheduled=bool(force_preempts_scheduled),
            force_condition_groups=_normalize_force_condition_groups(force_condition_groups),
            force_condition_group_names=force_condition_group_names or (),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScheduleDefinition":
        """Deserialize a schedule from config entry options."""
        return cls.create(
            schedule_id=str(data.get("schedule_id") or data.get("id") or uuid4().hex),
            revision=int(data.get("revision", 1)),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", True)),
            weekdays=data.get("weekdays", ()),
            dates=data.get("dates", ()),
            local_time=data.get("local_time", "09:00:00"),
            target_type=str(data.get("target_type", TARGET_TYPE_CLEANING_ZONES)),
            targets=data.get("targets", ()),
            paused=bool(data.get("paused", False)),
            # Schedules persisted before 0.8.5 used progressive zone execution.
            # Preserve that behavior on upgrade; only newly created schedules
            # default to combined execution.
            zone_execution_policy=str(
                data.get("zone_execution_policy", LEGACY_ZONE_EXECUTION_POLICY)
            ),
            cleaning_params=data.get("cleaning_params", {}),
            prewarning_minutes=int(
                data.get("prewarning_minutes", DEFAULT_PREWARNING_MINUTES)
            ),
            # 0.6.31 migration: legacy releases allowed zero, but a zero
            # execution window could never start because deadline == planned
            # start. Normalize persisted legacy zero to the new 1-minute floor.
            execution_window_minutes=_legacy_execution_window(
                data.get(
                    "execution_window_minutes",
                    DEFAULT_EXECUTION_WINDOW_MINUTES,
                )
            ),
            notification_policy=data.get("notification_policy", {}),
            weekday_times=data.get("weekday_times"),
            weekday_overrides=data.get("weekday_overrides"),
            force_enabled=bool(data.get("force_enabled", False)),
            force_max_advance_minutes=int(data.get("force_max_advance_minutes", 0) or 0),
            force_priority=int(data.get("force_priority", 0) or 0),
            force_preempts_scheduled=bool(data.get("force_preempts_scheduled", False)),
            force_condition_groups=data.get("force_condition_groups", ()),
            force_condition_group_names=data.get("force_condition_group_names", ()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize a schedule into Home Assistant config-entry-safe data."""
        return {
            "schedule_id": self.schedule_id,
            "revision": self.revision,
            "name": self.name,
            "enabled": self.enabled,
            "paused": self.paused,
            "weekdays": list(self.weekdays),
            "dates": [item.isoformat() for item in self.dates],
            "local_time": self.local_time.isoformat(),
            "target_type": self.target_type,
            "targets": list(self.targets),
            "zone_execution_policy": self.zone_execution_policy,
            "cleaning_params": dict(self.cleaning_params),
            "prewarning_minutes": self.prewarning_minutes,
            "execution_window_minutes": self.execution_window_minutes,
            "notification_policy": dict(self.notification_policy),
            "weekday_times": {
                str(index): value.isoformat()
                for index, value in sorted(self.weekday_times.items())
            },
            "weekday_overrides": {
                str(index): dict(params)
                for index, params in sorted(self.weekday_overrides.items())
            },
            "force_enabled": self.force_enabled,
            "force_max_advance_minutes": self.force_max_advance_minutes,
            "force_priority": self.force_priority,
            "force_preempts_scheduled": self.force_preempts_scheduled,
            "force_condition_groups": _force_groups_to_list(self.force_condition_groups),
            "force_condition_group_names": list(self.force_condition_group_names),
        }

    def matches_date(self, value: date) -> bool:
        """Return whether this local calendar date belongs to the rule."""
        return value in self.dates or value.weekday() in self.weekdays

    def local_time_for_date(self, value: date) -> time:
        """Return the wall-clock start time for one local occurrence date."""
        return self.weekday_times.get(value.weekday(), self.local_time)

    def effective_cleaning_params_for_date(self, value: date) -> dict[str, Any]:
        """Return the immutable cleaning-parameter snapshot for one occurrence."""
        result = dict(self.cleaning_params)
        override = self.weekday_overrides.get(value.weekday())
        if override:
            result.update(dict(override))
        return canonicalize_effective_cleaning_params(result)

    def force_config(self) -> dict[str, Any]:
        """Return the occurrence-snapshotted early-run policy."""
        return {
            "enabled": self.force_enabled,
            "max_advance_minutes": self.force_max_advance_minutes,
            "priority": self.force_priority,
            "preempts_scheduled": self.force_preempts_scheduled,
            "condition_groups": _force_groups_to_list(self.force_condition_groups),
        }

    def execution_fingerprint(self) -> tuple[Any, ...]:
        """Return fields that change occurrence/execution semantics.

        Display-only changes (name) and notification routing/policy do not
        invalidate an already materialized occurrence.
        """
        return (
            self.enabled,
            self.weekdays,
            self.dates,
            self.local_time.isoformat(),
            tuple(
                (index, value.isoformat())
                for index, value in sorted(self.weekday_times.items())
            ),
            tuple(
                (
                    index,
                    tuple(sorted((str(key), repr(value)) for key, value in params.items())),
                )
                for index, params in sorted(self.weekday_overrides.items())
            ),
            self.force_enabled,
            self.force_max_advance_minutes,
            self.force_priority,
            self.force_preempts_scheduled,
            tuple(
                tuple(sorted((str(key), repr(value)) for key, value in condition.items()))
                for group in self.force_condition_groups
                for condition in group
            ),
            tuple(len(group) for group in self.force_condition_groups),
            self.target_type,
            self.targets,
            self.zone_execution_policy,
            tuple(sorted((str(key), repr(value)) for key, value in self.cleaning_params.items())),
            self.prewarning_minutes,
            self.execution_window_minutes,
        )

    def revised(self, **changes: Any) -> "ScheduleDefinition":
        """Return an edited schedule, incrementing revision only for execution changes."""
        payload = self.to_dict()
        payload.update(changes)
        payload["schedule_id"] = self.schedule_id
        payload["revision"] = self.revision
        if "local_time" in changes and "weekday_times" not in changes:
            # Preserve the legacy API contract: changing the single time on a
            # uniform schedule changes all recurring weekdays. On a genuinely
            # flexible schedule ``local_time`` remains only the date fallback.
            if all(value == self.local_time for value in self.weekday_times.values()):
                replacement = _parse_time(changes["local_time"])
                payload["weekday_times"] = {
                    str(day): replacement.isoformat() for day in self.weekdays
                }
        candidate = ScheduleDefinition.from_dict(payload)
        if candidate.execution_fingerprint() != self.execution_fingerprint():
            payload["revision"] = self.revision + 1
            candidate = ScheduleDefinition.from_dict(payload)
        return candidate


@dataclass(frozen=True, slots=True)
class Occurrence:
    """Concrete calendar occurrence derived from a schedule definition."""

    occurrence_id: str
    schedule_id: str
    schedule_revision: int
    schedule_name: str
    planned_start: datetime
    warning_at: datetime
    deadline_at: datetime
    next_planned_start: datetime | None
    target_type: str
    targets: tuple[str, ...]
    cleaning_params: Mapping[str, Any]
    zone_execution_policy: str = LEGACY_ZONE_EXECUTION_POLICY
    force_config: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the occurrence for diagnostics and sensor attributes."""
        return {
            "occurrence_id": self.occurrence_id,
            "schedule_id": self.schedule_id,
            "schedule_revision": self.schedule_revision,
            "schedule_name": self.schedule_name,
            "planned_start": self.planned_start.isoformat(),
            "warning_at": self.warning_at.isoformat(),
            "deadline_at": self.deadline_at.isoformat(),
            "next_planned_start": (
                self.next_planned_start.isoformat()
                if self.next_planned_start is not None
                else None
            ),
            "target_type": self.target_type,
            "targets": list(self.targets),
            "cleaning_params": dict(self.cleaning_params),
            "zone_execution_policy": self.zone_execution_policy,
            "force_config": dict(self.force_config),
        }


def make_occurrence_id(
    schedule_id: str,
    revision: int,
    planned_start: datetime,
) -> str:
    """Create a deterministic revision-aware occurrence identifier."""
    if planned_start.tzinfo is None:
        raise ValueError("planned_start_must_be_timezone_aware")
    canonical = planned_start.astimezone(timezone.utc).isoformat(timespec="seconds")
    raw = f"{schedule_id}|{revision}|{canonical}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:20]
    return f"{schedule_id}:{revision}:{digest}"


def default_cleaning_params() -> dict[str, Any]:
    """Return stable defaults for newly created schedules."""
    return {
        "cleaning_mode": "default",
        "cleaning_route": "",
        "mop_mode": "",
        "fan_mode": "",
        "water_mode": "",
        "passes": DEFAULT_PASSES,
    }
