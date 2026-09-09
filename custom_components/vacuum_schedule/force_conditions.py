"""Read-only early-run condition evaluator for Vacuum Schedule 0.10.3.

The force tree is intentionally shallow: OR between groups, AND inside a group.
This module never changes Job lifecycle state and never starts execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

_INVALID_STATES = {"unknown", "unavailable", "none", ""}


def force_dependencies(config: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return HA entity ids referenced by one normalized force config."""
    result: list[str] = []
    seen: set[str] = set()
    for group in (config or {}).get("condition_groups", ()) or ():
        for condition in group or ():
            if not isinstance(condition, Mapping):
                continue
            entities: Sequence[Any]
            if str(condition.get("type", "")) == "people_absent":
                entities = condition.get("entity_ids", ()) or ()
            else:
                entities = (condition.get("entity_id"),)
            for raw in entities:
                entity_id = str(raw or "").strip()
                if entity_id and entity_id not in seen:
                    seen.add(entity_id)
                    result.append(entity_id)
    return tuple(result)


def select_force_candidate(evaluations: Sequence[Mapping[str, Any]]) -> str | None:
    """Select highest-priority currently eligible force candidate deterministically."""
    eligible = [item for item in evaluations if bool(item.get("eligible"))]
    if not eligible:
        return None
    def key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        planned = datetime.fromisoformat(str(item.get("planned_start")))
        return (-int(item.get("priority", 0) or 0), planned, str(item.get("job_id", "")))
    return str(min(eligible, key=key).get("job_id"))


class ForceConditionEngine:
    """Evaluate condition truth plus continuous ``for N minutes`` dwell time."""

    def __init__(self, hass: Any) -> None:
        self.hass = hass
        self._true_since: dict[tuple[str, str], datetime] = {}

    def reset_job(self, job_id: str) -> None:
        prefix = str(job_id)
        self._true_since = {
            key: value for key, value in self._true_since.items() if key[0] != prefix
        }

    @staticmethod
    def _state_value(state: Any) -> str | None:
        if state is None:
            return None
        value = str(getattr(state, "state", "")).strip()
        if value.lower() in _INVALID_STATES:
            return None
        return value

    @staticmethod
    def _safe_last_changed(state: Any, now: datetime) -> datetime | None:
        value = getattr(state, "last_changed", None)
        if not isinstance(value, datetime):
            return None
        if (value.tzinfo is None) != (now.tzinfo is None):
            return None
        if value > now:
            return None
        return value

    def _instant_condition(
        self, condition: Mapping[str, Any], now: datetime
    ) -> tuple[bool, datetime | None, dict[str, Any]]:
        kind = str(condition.get("type", ""))
        if kind == "people_absent":
            ids = [str(item) for item in condition.get("entity_ids", ())]
            minimum = max(1, int(condition.get("minimum_absent", 1) or 1))
            absent: list[tuple[str, datetime | None]] = []
            states: dict[str, str | None] = {}
            for entity_id in ids:
                state = self.hass.states.get(entity_id)
                value = self._state_value(state)
                states[entity_id] = value
                if value is not None and value.lower() != "home":
                    absent.append((entity_id, self._safe_last_changed(state, now)))
            matched = len(absent) >= minimum
            inferred: datetime | None = None
            if matched:
                # At least N people have continuously been away since the newest
                # last_changed among the N longest-away people.
                known = sorted(item[1] for item in absent if item[1] is not None)
                if len(known) >= minimum:
                    inferred = known[minimum - 1]
            return matched, inferred, {
                "entity_ids": ids,
                "minimum_absent": minimum,
                "absent_count": len(absent),
                "states": states,
            }

        entity_id = str(condition.get("entity_id", ""))
        state = self.hass.states.get(entity_id)
        value = self._state_value(state)
        inferred = self._safe_last_changed(state, now)
        if value is None:
            return False, None, {"entity_id": entity_id, "actual": None}

        if kind == "entity":
            desired = str(condition.get("value", ""))
            operator = str(condition.get("operator", "eq"))
            matched = value == desired if operator == "eq" else value != desired
            return matched, inferred if matched else None, {
                "entity_id": entity_id, "actual": value, "operator": operator, "expected": desired,
            }
        if kind == "numeric":
            operator = str(condition.get("operator", "gte"))
            threshold = float(condition.get("value"))
            try:
                actual = float(value)
            except (TypeError, ValueError):
                return False, None, {"entity_id": entity_id, "actual": value, "operator": operator, "expected": threshold}
            comparisons = {
                "gt": actual > threshold,
                "gte": actual >= threshold,
                "lt": actual < threshold,
                "lte": actual <= threshold,
            }
            matched = comparisons.get(operator, False)
            return matched, inferred if matched else None, {
                "entity_id": entity_id, "actual": actual, "operator": operator, "expected": threshold,
            }
        if kind == "person":
            desired = str(condition.get("state", "not_home"))
            matched = value.lower() == "home" if desired == "home" else value.lower() != "home"
            return matched, inferred if matched else None, {
                "entity_id": entity_id, "actual": value, "expected": desired,
            }
        if kind == "binary":
            desired = str(condition.get("state", "on"))
            matched = value.lower() == desired
            return matched, inferred if matched else None, {
                "entity_id": entity_id, "actual": value, "expected": desired,
            }
        return False, None, {"entity_id": entity_id, "actual": value}

    def evaluate(
        self,
        *,
        job_id: str,
        groups: Sequence[Sequence[Mapping[str, Any]]],
        now: datetime,
    ) -> dict[str, Any]:
        """Return detailed OR-of-AND evaluation without mutating the Job."""
        group_results: list[dict[str, Any]] = []
        next_transitions: list[datetime] = []
        active_keys: set[tuple[str, str]] = set()
        for group_index, group in enumerate(groups):
            condition_results: list[dict[str, Any]] = []
            group_match = bool(group)
            for condition_index, condition in enumerate(group):
                condition_id = str(condition.get("condition_id") or f"g{group_index + 1}c{condition_index + 1}")
                key = (str(job_id), condition_id)
                active_keys.add(key)
                instant, inferred, detail = self._instant_condition(condition, now)
                for_minutes = max(0, int(condition.get("for_minutes", 0) or 0))
                required_seconds = for_minutes * 60
                if not instant:
                    self._true_since.pop(key, None)
                    true_since = None
                    matched = False
                    remaining = required_seconds if required_seconds else 0
                else:
                    true_since = self._true_since.get(key)
                    if true_since is None:
                        true_since = inferred or now
                        self._true_since[key] = true_since
                    elapsed = max(0.0, (now - true_since).total_seconds())
                    matched = elapsed >= required_seconds
                    remaining = max(0, int(round(required_seconds - elapsed)))
                    if not matched and required_seconds:
                        next_transitions.append(true_since + timedelta(seconds=required_seconds))
                group_match = group_match and matched
                condition_results.append({
                    "condition_id": condition_id,
                    "type": str(condition.get("type", "")),
                    "instant_match": instant,
                    "matched": matched,
                    "for_minutes": for_minutes,
                    "true_since": true_since.isoformat() if true_since else None,
                    "remaining_seconds": remaining,
                    **detail,
                })
            group_results.append({
                "group_index": group_index,
                "matched": group_match,
                "conditions": condition_results,
            })
        # Drop dwell timers for conditions removed by an edited schedule.
        job_prefix = str(job_id)
        for key in tuple(self._true_since):
            if key[0] == job_prefix and key not in active_keys:
                self._true_since.pop(key, None)
        matched = any(group["matched"] for group in group_results)
        next_transition = min(next_transitions) if next_transitions else None
        return {
            "matched": matched,
            "groups": group_results,
            "next_transition_at": next_transition.isoformat() if next_transition else None,
        }
