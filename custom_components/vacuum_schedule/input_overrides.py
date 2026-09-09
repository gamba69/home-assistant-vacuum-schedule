"""Non-invasive test input overrides for Vacuum Schedule 0.5.0."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class OverrideMode(StrEnum):
    LIVE = "LIVE"
    FREEZE = "FREEZE"
    FORCE = "FORCE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(slots=True, frozen=True)
class TestOverride:
    target: str
    mode: OverrideMode
    value: Any = None
    persistent: bool = False
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TestOverride":
        return cls(
            target=str(data.get("target", "")),
            mode=OverrideMode(str(data.get("mode", OverrideMode.LIVE.value)).upper()),
            value=data.get("value"),
            persistent=bool(data.get("persistent", False)),
            created_at=str(data["created_at"]) if data.get("created_at") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "mode": self.mode.value,
            "value": self.value,
            "persistent": self.persistent,
            "created_at": self.created_at,
        }


class TestOverrideManager:
    __test__ = False

    """Runtime + persistent override registry.

    Volatile data is passed in by the integration and therefore survives a config-entry
    reload but not a Home Assistant process restart. Persistent data is separately
    serialized into the scheduler store.
    """

    def __init__(self, volatile_store: dict[str, dict[str, Any]]) -> None:
        self._volatile_store = volatile_store
        self._items: dict[str, TestOverride] = {}
        for target, raw in volatile_store.items():
            if isinstance(raw, Mapping):
                try:
                    item = TestOverride.from_dict(raw)
                except (ValueError, TypeError):
                    continue
                if item.mode is not OverrideMode.LIVE:
                    self._items[target] = item

    def restore_persistent(self, values: Mapping[str, Any] | None) -> None:
        for target, raw in dict(values or {}).items():
            if not isinstance(raw, Mapping):
                continue
            try:
                item = TestOverride.from_dict(raw)
            except (ValueError, TypeError):
                continue
            if item.persistent and item.mode is not OverrideMode.LIVE:
                self._items[str(target)] = item
        self._sync_volatile()

    def set(
        self,
        target: str,
        mode: OverrideMode | str,
        *,
        value: Any = None,
        persistent: bool = False,
        frozen_live_value: Any = None,
    ) -> TestOverride | None:
        mode_value = mode if isinstance(mode, OverrideMode) else OverrideMode(str(mode).upper())
        target = str(target).strip()
        if not target:
            raise ValueError("missing_override_target")
        if mode_value is OverrideMode.LIVE:
            self._items.pop(target, None)
            self._sync_volatile()
            return None
        effective_value = frozen_live_value if mode_value is OverrideMode.FREEZE else value
        item = TestOverride(
            target=target,
            mode=mode_value,
            value=effective_value,
            persistent=bool(persistent),
            created_at=datetime.now().astimezone().isoformat(),
        )
        self._items[target] = item
        self._sync_volatile()
        return item

    def get(self, target: str) -> TestOverride | None:
        return self._items.get(target)

    def clear(self, target: str | None = None) -> None:
        if target:
            self._items.pop(target, None)
        else:
            self._items.clear()
        self._sync_volatile()

    def apply(self, target: str, live_value: Any, *, live_available: bool = True) -> tuple[Any, bool, TestOverride | None]:
        item = self._items.get(target)
        if item is None:
            return live_value, live_available, None
        if item.mode is OverrideMode.UNAVAILABLE:
            return None, False, item
        if item.mode in {OverrideMode.FORCE, OverrideMode.FREEZE}:
            return item.value, True, item
        return live_value, live_available, None

    def persistent_dict(self) -> dict[str, Any]:
        return {
            key: item.to_dict()
            for key, item in sorted(self._items.items())
            if item.persistent
        }

    def as_dict(self) -> dict[str, Any]:
        return {key: item.to_dict() for key, item in sorted(self._items.items())}

    def _sync_volatile(self) -> None:
        self._volatile_store.clear()
        self._volatile_store.update(self.as_dict())
