"""Persistent binding and pre-flight policy models for Vacuum Schedule 0.5.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

try:
    from .forecast_models import forecast_policies_from_dict, forecast_policies_to_dict
except ImportError:  # pragma: no cover - legacy direct-module test harness
    from forecast_models import forecast_policies_from_dict, forecast_policies_to_dict


class BindingMode(StrEnum):
    """How one normalized input is sourced."""

    AUTO = "auto"
    MANUAL = "manual"
    DISABLED = "disabled"


class SupportStatus(StrEnum):
    """User-facing status of a capability/input binding."""

    DETECTED = "detected"
    MANUAL = "manual"
    UNSUPPORTED = "unsupported"
    DISABLED = "disabled"
    CONFLICT = "conflict"
    UNVERIFIED = "unverified"


class SourceType(StrEnum):
    """Source kind used by a capability binding."""

    VACUUM_FEATURE = "vacuum_feature"
    ENTITY_STATE = "entity_state"
    ENTITY_ATTRIBUTE = "entity_attribute"
    DEVICE = "device"
    DERIVED = "derived"
    CONSTANT = "constant"


class ExecutionGateMode(StrEnum):
    """Global schedule execution gate."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    DISABLED_UNTIL = "disabled_until"


@dataclass(slots=True, frozen=True)
class CapabilityBinding:
    """Persistent selection for one normalized input."""

    key: str
    binding_mode: BindingMode = BindingMode.AUTO
    source_type: SourceType = SourceType.DERIVED
    entity_id: str | None = None
    entity_registry_id: str | None = None
    device_id: str | None = None
    attribute: str | None = None
    value_mapping: Mapping[str, Any] = field(default_factory=dict)
    thresholds: Mapping[str, float] = field(default_factory=dict)
    semantic: str | None = None
    unit: str | None = None
    normal_state: str | None = None
    support_status: SupportStatus = SupportStatus.UNVERIFIED
    confidence: float | None = None
    discovery_reason: str | None = None
    last_verified_at: str | None = None

    @classmethod
    def from_dict(cls, key: str, data: Mapping[str, Any] | None) -> "CapabilityBinding":
        raw = dict(data or {})
        mode = BindingMode(str(raw.get("binding_mode", raw.get("mode", BindingMode.AUTO.value))))
        source_type = SourceType(str(raw.get("source_type", SourceType.DERIVED.value)))
        support_raw = raw.get("support_status")
        if support_raw is None:
            support = (
                SupportStatus.DISABLED
                if mode is BindingMode.DISABLED
                else SupportStatus.MANUAL
                if mode is BindingMode.MANUAL
                else SupportStatus.UNVERIFIED
            )
        else:
            support = SupportStatus(str(support_raw))
        thresholds: dict[str, float] = {}
        for name, value in dict(raw.get("thresholds", {})).items():
            try:
                thresholds[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
        confidence = raw.get("confidence")
        try:
            confidence_value = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence_value = None
        return cls(
            key=str(raw.get("key") or key),
            binding_mode=mode,
            source_type=source_type,
            entity_id=str(raw["entity_id"]) if raw.get("entity_id") else None,
            entity_registry_id=(str(raw["entity_registry_id"]) if raw.get("entity_registry_id") else None),
            device_id=str(raw["device_id"]) if raw.get("device_id") else None,
            attribute=str(raw["attribute"]) if raw.get("attribute") else None,
            value_mapping={str(k): v for k, v in dict(raw.get("value_mapping", {})).items()},
            thresholds=thresholds,
            semantic=str(raw["semantic"]) if raw.get("semantic") else None,
            unit=str(raw["unit"]) if raw.get("unit") else None,
            normal_state=(str(raw["normal_state"]).lower() if str(raw.get("normal_state", "")).lower() in {"on", "off"} else None),
            support_status=support,
            confidence=confidence_value,
            discovery_reason=(str(raw["discovery_reason"]) if raw.get("discovery_reason") else None),
            last_verified_at=(str(raw["last_verified_at"]) if raw.get("last_verified_at") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to config-entry-safe data."""
        return {
            "key": self.key,
            "binding_mode": self.binding_mode.value,
            "source_type": self.source_type.value,
            "entity_id": self.entity_id,
            "entity_registry_id": self.entity_registry_id,
            "device_id": self.device_id,
            "attribute": self.attribute,
            "value_mapping": dict(self.value_mapping),
            "thresholds": dict(self.thresholds),
            "semantic": self.semantic,
            "unit": self.unit,
            "normal_state": self.normal_state,
            "support_status": self.support_status.value,
            "confidence": self.confidence,
            "discovery_reason": self.discovery_reason,
            "last_verified_at": self.last_verified_at,
        }


@dataclass(slots=True, frozen=True)
class PreflightPolicy:
    """Persistent entry-wide pre-flight policy."""

    execution_gate: ExecutionGateMode = ExecutionGateMode.ENABLED
    disabled_until: str | None = None
    default_min_battery_percent: float = 20.0
    minimum_start_window_minutes: int = 0
    occupancy_clear_delay_seconds: int = 0
    access_stable_delay_seconds: int = 0
    forecasts: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "PreflightPolicy":
        raw = dict(data or {})
        try:
            gate = ExecutionGateMode(str(raw.get("execution_gate", ExecutionGateMode.ENABLED.value)))
        except ValueError:
            gate = ExecutionGateMode.ENABLED

        def _float(key: str, default: float) -> float:
            try:
                return float(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        def _int(key: str, default: int) -> int:
            try:
                return int(raw.get(key, default))
            except (TypeError, ValueError):
                return default



        # 0.5.9 folds the old DND-only safety margin into the single global
        # minimum remaining start-window threshold.  Taking the maximum is a
        # conservative one-time migration for existing 0.5.8 installations.
        minimum_window = max(
            0,
            _int("minimum_start_window_minutes", 0),
            _int("dnd_safety_margin_minutes", 0),
        )
        return cls(
            execution_gate=gate,
            disabled_until=(str(raw["disabled_until"]) if raw.get("disabled_until") else None),
            default_min_battery_percent=max(0.0, min(100.0, _float("default_min_battery_percent", 20.0))),
            minimum_start_window_minutes=minimum_window,
            occupancy_clear_delay_seconds=max(0, _int("occupancy_clear_delay_seconds", 0)),
            access_stable_delay_seconds=max(0, _int("access_stable_delay_seconds", 0)),
            forecasts=forecast_policies_from_dict(raw.get("forecasts")),
        )

    @property
    def disabled_until_datetime(self) -> datetime | None:
        if not self.disabled_until:
            return None
        try:
            return datetime.fromisoformat(self.disabled_until)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_gate": self.execution_gate.value,
            "disabled_until": self.disabled_until,
            "default_min_battery_percent": self.default_min_battery_percent,
            "minimum_start_window_minutes": self.minimum_start_window_minutes,
            "occupancy_clear_delay_seconds": self.occupancy_clear_delay_seconds,
            "access_stable_delay_seconds": self.access_stable_delay_seconds,
            "forecasts": forecast_policies_to_dict(self.forecasts),
        }


NORMALIZED_BINDING_KEYS = (
    "vacuum.battery_percent",
    "vacuum.charging",
    "dock.available",
    "dock.robot_docked",
    "dock.clean_water",
    "dock.dirty_water",
    "dock.detergent",
    "mop.attached",
    "dnd.active",
    "dnd.starts_at",
    "dnd.ends_at",
)


def bindings_from_options(raw: Mapping[str, Any] | None) -> dict[str, CapabilityBinding]:
    """Load all known bindings, retaining future/unknown keys as well."""
    data = dict(raw or {})
    keys = set(NORMALIZED_BINDING_KEYS) | {str(key) for key in data}
    return {key: CapabilityBinding.from_dict(key, data.get(key)) for key in sorted(keys)}


def bindings_to_options(bindings: Mapping[str, CapabilityBinding]) -> dict[str, Any]:
    return {key: binding.to_dict() for key, binding in sorted(bindings.items())}
