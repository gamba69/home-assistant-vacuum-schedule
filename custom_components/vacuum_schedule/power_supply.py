"""Small vendor-independent normalization for an optional mains sensor."""

from __future__ import annotations

from typing import Any


def normalize_power_supply(raw: Any, available: bool, present_state: str = "on") -> bool | None:
    """Return True (power), False (confirmed outage), or None (unknown)."""
    if not available:
        return None
    value = str(raw).strip().lower()
    expected = str(present_state).strip().lower()
    if expected not in {"on", "off"} or value not in {"on", "off"}:
        return None
    return value == expected
