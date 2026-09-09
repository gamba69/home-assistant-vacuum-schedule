"""Event-driven dependency index for Vacuum Schedule WAIT jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event


class DependencyIndex:
    """Track entity -> active job dependencies and debounce relevant rechecks."""

    def __init__(
        self,
        hass: HomeAssistant,
        recheck_callback: Callable[[set[str]], Awaitable[None]],
        *,
        debounce_seconds: float = 0.25,
    ) -> None:
        self.hass = hass
        self._recheck_callback = recheck_callback
        self._debounce_seconds = debounce_seconds
        self._job_dependencies: dict[str, set[str]] = {}
        self._entity_jobs: dict[str, set[str]] = {}
        self._unsub: Callable[[], None] | None = None
        self._pending_jobs: set[str] = set()
        self._debounce_task: asyncio.Task[None] | None = None

    def update_job(self, job_id: str, dependencies: Iterable[str]) -> None:
        values = {str(item) for item in dependencies if item}
        if self._job_dependencies.get(job_id, set()) == values:
            return
        self.remove_job(job_id, rebuild=False)
        if values:
            self._job_dependencies[job_id] = values
            for entity_id in values:
                self._entity_jobs.setdefault(entity_id, set()).add(job_id)
        self._rebuild_subscription()

    def remove_job(self, job_id: str, *, rebuild: bool = True) -> None:
        old = self._job_dependencies.pop(job_id, set())
        for entity_id in old:
            jobs = self._entity_jobs.get(entity_id)
            if jobs is None:
                continue
            jobs.discard(job_id)
            if not jobs:
                self._entity_jobs.pop(entity_id, None)
        self._pending_jobs.discard(job_id)
        if rebuild:
            self._rebuild_subscription()

    def clear(self) -> None:
        self._job_dependencies.clear()
        self._entity_jobs.clear()
        self._pending_jobs.clear()
        self._rebuild_subscription()

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = None

    def _rebuild_subscription(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if not self._entity_jobs:
            return

        @callback
        def _state_changed(event: Event[EventStateChangedData]) -> None:
            entity_id = str(event.data.get("entity_id", ""))
            jobs = self._entity_jobs.get(entity_id)
            if not jobs:
                return
            self._pending_jobs.update(jobs)
            if self._debounce_task is None or self._debounce_task.done():
                self._debounce_task = self.hass.async_create_task(self._debounced_recheck())

        self._unsub = async_track_state_change_event(
            self.hass, sorted(self._entity_jobs), _state_changed
        )

    async def _debounced_recheck(self) -> None:
        await asyncio.sleep(self._debounce_seconds)
        jobs = set(self._pending_jobs)
        self._pending_jobs.clear()
        if jobs:
            await self._recheck_callback(jobs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_count": len(self._entity_jobs),
            "job_count": len(self._job_dependencies),
            "entities": {key: sorted(value) for key, value in sorted(self._entity_jobs.items())},
            "jobs": {key: sorted(value) for key, value in sorted(self._job_dependencies.items())},
            "pending_jobs": sorted(self._pending_jobs),
            "debounce_seconds": self._debounce_seconds,
        }
