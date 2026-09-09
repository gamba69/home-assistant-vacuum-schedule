"""Clock abstraction used by the scheduler and stage-0.4 simulator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .time_utils import instant_add


class SchedulerClock:
    """Home Assistant clock with an optional debug offset."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._offset = timedelta(0)

    def now(self) -> datetime:
        """Return scheduler time, aware and in the HA timezone."""
        return instant_add(dt_util.now(), self._offset)

    @property
    def offset(self) -> timedelta:
        return self._offset

    def set_offset(self, value: timedelta) -> None:
        self._offset = value

    def advance(self, delta: timedelta) -> datetime:
        self._offset += delta
        return self.now()

    def reset(self) -> datetime:
        self._offset = timedelta(0)
        return self.now()

    def virtual_to_real_utc(self, virtual_time: datetime) -> datetime:
        """Map a virtual scheduler instant to the corresponding real UTC instant."""
        if virtual_time.tzinfo is None or virtual_time.utcoffset() is None:
            raise ValueError("datetime_must_be_timezone_aware")
        return instant_add(virtual_time, -self._offset).astimezone(timezone.utc)

    def as_dict(self) -> dict[str, object]:
        return {
            "now": self.now().isoformat(),
            "offset_seconds": int(self._offset.total_seconds()),
        }
