"""Persistence for scheduler jobs and durable lifecycle history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .job import JobInstance, JobOrigin
from .execution_models import ExecutionMode
from .storage_migrations import migrate_job_history_archive

_STORAGE_VERSION = 1
_HISTORY_STORAGE_VERSION = 1
_OPERATIONAL_HISTORY_LIMIT = 100


class JobStore:
    """Persist active jobs, bounded UI history, and durable append-only history.

    ``history`` is intentionally small because it feeds the operational UI.
    ``terminal_archive`` and ``events`` are the durable source material for
    future statistics and are never trimmed during normal operation.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass, _STORAGE_VERSION, f"vacuum_schedule.{entry_id}.jobs"
        )
        self._history_store = Store(
            hass,
            _HISTORY_STORAGE_VERSION,
            f"vacuum_schedule.{entry_id}.job_history",
        )
        self.active: dict[str, JobInstance] = {}
        self.history: list[JobInstance] = []
        self.terminal_archive: list[JobInstance] = []
        self.events: list[dict[str, Any]] = []
        self.debug_state: dict[str, object] = {}

    async def async_load(self) -> None:
        raw = await self._store.async_load() or {}
        durable_raw = await self._history_store.async_load() or {}
        self.debug_state = dict(raw.get("debug_state", {}))
        self.active = {}
        for item in raw.get("active_jobs", []):  # type: ignore[union-attr]
            try:
                job = JobInstance.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if not job.terminal:
                self.active[job.job_id] = job

        operational_history: list[JobInstance] = []
        for item in raw.get("history", []):  # type: ignore[union-attr]
            try:
                job = JobInstance.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if job.terminal:
                operational_history.append(job)
        self.history = operational_history[-_OPERATIONAL_HISTORY_LIMIT:]

        durable_jobs: list[JobInstance] = []
        for item in durable_raw.get("terminal_jobs", []):  # type: ignore[union-attr]
            try:
                job = JobInstance.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if job.terminal:
                durable_jobs.append(job)

        self.terminal_archive, self.events, migration_needed = migrate_job_history_archive(
            self, durable_jobs, operational_history, durable_raw
        )
        if migration_needed:
            await self.async_save()

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "active_jobs": [item.to_dict() for item in self.active.values()],
                "history": [
                    item.to_dict()
                    for item in self.history[-_OPERATIONAL_HISTORY_LIMIT:]
                ],
                "debug_state": dict(self.debug_state),
            }
        )
        await self._history_store.async_save(
            {
                "terminal_jobs": [item.to_dict() for item in self.terminal_archive],
                "events": list(self.events),
            }
        )


    def export_execution_history(self) -> dict[str, Any]:
        """Return the completed-Job history layer without active runtime state."""
        return {
            "history": [item.to_dict() for item in self.history],
            "terminal_jobs": [item.to_dict() for item in self.terminal_archive],
            "events": [dict(item) for item in self.events],
        }

    async def async_clear_execution_history(self) -> dict[str, int]:
        """Delete completed Job history while preserving active Jobs/debug state."""
        counts = {
            "history": len(self.history),
            "terminal_jobs": len(self.terminal_archive),
            "events": len(self.events),
        }
        self.history = []
        self.terminal_archive = []
        self.events = []
        await self.async_save()
        return counts

    async def async_restore_execution_history(self, payload: Mapping[str, Any]) -> dict[str, int]:
        """Replace only completed Job history from a trusted backup payload."""
        terminal: list[JobInstance] = []
        for item in payload.get("terminal_jobs", []):
            if not isinstance(item, Mapping):
                continue
            try:
                job = JobInstance.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if job.terminal:
                terminal.append(job)
        terminal.sort(key=lambda item: as_utc(item.finished_at or item.planned_start))
        self.terminal_archive = terminal

        operational: list[JobInstance] = []
        for item in payload.get("history", []):
            if not isinstance(item, Mapping):
                continue
            try:
                job = JobInstance.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if job.terminal:
                operational.append(job)
        self.history = operational[-_OPERATIONAL_HISTORY_LIMIT:]
        self.events = [dict(item) for item in payload.get("events", []) if isinstance(item, Mapping)]
        await self.async_save()
        return {
            "history": len(self.history),
            "terminal_jobs": len(self.terminal_archive),
            "events": len(self.events),
        }

    @staticmethod
    def _dry_run_timeline_generation(job: JobInstance) -> int:
        """Return the Dry-run replay namespace carried by a Job snapshot."""
        try:
            return int(job.metadata.get("dry_run_timeline_generation", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _occurrence_rearm_generation(job: JobInstance) -> int:
        """Return explicit replay generation for a deliberately re-armed slot.

        Generation 0 is the normal deterministic occurrence namespace.  A
        positive generation exists only when the operator explicitly asks the
        scheduler refresh action to restore a future occurrence that had already
        been executed early.  This preserves both completed executions in durable
        history without changing the canonical occurrence id.
        """
        try:
            return max(0, int(job.metadata.get("occurrence_rearm_generation", 0)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _occurrence_namespace_key(cls, job: JobInstance) -> tuple[str, str, int, int]:
        """Return the occurrence uniqueness key used by durable history.

        REAL occurrences normally live in generation 0.  An explicit operator
        refresh may re-arm an already early-executed future slot; those replays
        use a positive re-arm generation so the original terminal snapshot is
        retained rather than overwritten. DRY_RUN additionally keeps its normal
        timeline-generation namespace.
        """
        generation = (
            cls._dry_run_timeline_generation(job)
            if job.execution_mode is ExecutionMode.DRY_RUN
            else 0
        )
        return (
            job.occurrence_id,
            job.execution_mode.value,
            generation,
            cls._occurrence_rearm_generation(job),
        )

    def by_occurrence_id(
        self,
        occurrence_id: str,
        execution_mode: ExecutionMode | None = None,
        *,
        dry_run_timeline_generation: int | None = None,
        occurrence_rearm_generation: int | None = None,
        exclude_job_id: str | None = None,
    ) -> JobInstance | None:
        """Find an occurrence within an execution/timeline namespace.

        DRY_RUN never consumes the future REAL occurrence with the same
        deterministic occurrence_id. Starting a new Dry-run timeline generation
        also makes earlier Dry-run terminal snapshots non-consuming while keeping
        them in operational/audit history.
        """
        def matches(job: JobInstance) -> bool:
            if exclude_job_id is not None and job.job_id == exclude_job_id:
                return False
            if job.occurrence_id != occurrence_id:
                return False
            if execution_mode is not None and job.execution_mode is not execution_mode:
                return False
            if (
                execution_mode is ExecutionMode.DRY_RUN
                and dry_run_timeline_generation is not None
                and self._dry_run_timeline_generation(job) != int(dry_run_timeline_generation)
            ):
                return False
            if (
                occurrence_rearm_generation is not None
                and self._occurrence_rearm_generation(job) != int(occurrence_rearm_generation)
            ):
                return False
            return True
        for job in self.active.values():
            if matches(job):
                return job
        for job in reversed(self.history):
            if matches(job):
                return job
        for job in reversed(self.terminal_archive):
            if matches(job):
                return job
        return None

    def by_schedule_slot(
        self,
        schedule_id: str,
        planned_start: datetime,
        execution_mode: ExecutionMode | None = None,
        *,
        dry_run_timeline_generation: int | None = None,
        exclude_job_id: str | None = None,
    ) -> JobInstance | None:
        """Find a scheduled Job owning one canonical schedule/time slot.

        ``occurrence_id`` is revision-aware, while physical schedule semantics
        are not: editing a Schedule during an already-started occurrence must
        never create a second Job for the same planned wall-clock slot.  This
        lookup therefore deliberately ignores schedule revision and re-arm
        generation while retaining REAL/DRY_RUN namespace isolation.
        """
        def matches(job: JobInstance) -> bool:
            if exclude_job_id is not None and job.job_id == exclude_job_id:
                return False
            if job.origin is not JobOrigin.SCHEDULED:
                return False
            if job.schedule_id != str(schedule_id):
                return False
            if job.planned_start != planned_start:
                return False
            if execution_mode is not None and job.execution_mode is not execution_mode:
                return False
            if (
                execution_mode is ExecutionMode.DRY_RUN
                and dry_run_timeline_generation is not None
                and self._dry_run_timeline_generation(job) != int(dry_run_timeline_generation)
            ):
                return False
            return True

        for job in self.active.values():
            if matches(job):
                return job
        for job in reversed(self.history):
            if matches(job):
                return job
        for job in reversed(self.terminal_archive):
            if matches(job):
                return job
        return None

    def active_for_schedule(self, schedule_id: str) -> list[JobInstance]:
        return sorted(
            (job for job in self.active.values() if job.schedule_id == schedule_id),
            key=lambda item: item.planned_start.timestamp(),
        )

    def add_active(self, job: JobInstance) -> None:
        dry_generation = (
            self._dry_run_timeline_generation(job)
            if job.execution_mode is ExecutionMode.DRY_RUN
            else None
        )
        if self.by_occurrence_id(
            job.occurrence_id,
            job.execution_mode,
            dry_run_timeline_generation=dry_generation,
            occurrence_rearm_generation=self._occurrence_rearm_generation(job),
        ) is not None:
            raise ValueError("duplicate_occurrence")
        self.active[job.job_id] = job

    def remove_active(self, job_id: str) -> JobInstance | None:
        return self.active.pop(job_id, None)

    def discard_active_projection(self, job_id: str) -> JobInstance | None:
        """Drop one unstarted projection and its lifecycle trace without history.

        This is intentionally stronger than ``remove_active`` and is reserved
        for the explicit operator action that rebuilds future schedule
        projections.  A never-started materialization is a cache, not execution
        history, so its created/WAIT trace is discarded together with it.
        """
        job = self.active.pop(job_id, None)
        if job is not None:
            self.events = [
                item for item in self.events
                if str(item.get("job_id") or "") != str(job_id)
            ]
        return job

    def next_occurrence_rearm_generation(
        self,
        occurrence_id: str,
        execution_mode: ExecutionMode,
        *,
        dry_run_timeline_generation: int | None = None,
    ) -> int:
        """Return a fresh positive replay generation for one canonical slot."""
        generations = [0]
        for job in [*self.active.values(), *self.history, *self.terminal_archive]:
            if job.occurrence_id != str(occurrence_id):
                continue
            if job.execution_mode is not execution_mode:
                continue
            if (
                execution_mode is ExecutionMode.DRY_RUN
                and dry_run_timeline_generation is not None
                and self._dry_run_timeline_generation(job) != int(dry_run_timeline_generation)
            ):
                continue
            generations.append(self._occurrence_rearm_generation(job))
        return max(generations) + 1

    def archive(self, job: JobInstance) -> None:
        self.active.pop(job.job_id, None)
        self.history.append(job)
        self.history = self.history[-_OPERATIONAL_HISTORY_LIMIT:]
        namespace_key = self._occurrence_namespace_key(job)
        if not any(
            self._occurrence_namespace_key(item) == namespace_key
            for item in self.terminal_archive
        ):
            self.terminal_archive.append(job)

    @staticmethod
    def _event_payload(
        job: JobInstance,
        event_type: str,
        at: datetime,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a compact, statistics-safe immutable lifecycle event."""
        return {
            "event_id": uuid4().hex,
            "event_type": str(event_type),
            "at": at.isoformat(),
            "scheduler_at": at.isoformat(),
            "recorded_at": datetime.now().astimezone().isoformat(),
            "job_id": job.job_id,
            "occurrence_id": job.occurrence_id,
            "schedule_id": job.schedule_id,
            "schedule_revision": job.schedule_revision,
            "origin": job.origin.value,
            "execution_mode": job.execution_mode.value,
            "state": job.state.value,
            "result": job.result.value if job.result else None,
            "reason_code": job.reason_code,
            "transition_generation": job.transition_generation,
            "wait_cycle": job.wait_cycle,
            "first_wait_at": job.first_wait_at.isoformat() if job.first_wait_at else None,
            "blockers": list(job.blockers),
            "current_blockers": list(job.current_blockers),
            "current_preflight_decision": job.current_preflight_decision,
            "zones": {
                zone_id: {
                    "state": zone.state.value,
                    "result": zone.result.value if zone.result else None,
                    "reason_code": zone.reason_code,
                    "blockers": list(zone.blockers),
                    "first_wait_at": zone.first_wait_at.isoformat() if zone.first_wait_at else None,
                    "wait_cycle": zone.wait_cycle,
                    "actual_start": zone.actual_start.isoformat() if zone.actual_start else None,
                    "finished_at": zone.finished_at.isoformat() if zone.finished_at else None,
                }
                for zone_id, zone in job.zone_runs.items()
            },
            "details": dict(details or {}),
        }

    @staticmethod
    def _semantic_event_signature(event: Mapping[str, Any]) -> tuple[Any, ...]:
        zones = event.get("zones", {})
        zone_signature = tuple(
            (
                str(zone_id),
                str(value.get("state")),
                str(value.get("result")),
                str(value.get("reason_code")),
                tuple(value.get("blockers", [])),
                value.get("first_wait_at"),
                value.get("wait_cycle"),
                value.get("actual_start"),
                value.get("finished_at"),
            )
            for zone_id, value in sorted(dict(zones).items())
            if isinstance(value, Mapping)
        )
        return (
            event.get("state"),
            event.get("result"),
            event.get("reason_code"),
            event.get("wait_cycle"),
            event.get("first_wait_at"),
            tuple(event.get("blockers", [])),
            tuple(event.get("current_blockers", [])),
            event.get("current_preflight_decision"),
            zone_signature,
        )

    def append_event(
        self,
        job: JobInstance,
        event_type: str,
        at: datetime,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one immutable lifecycle event without truncating history.

        Repeated watchdog evaluations may update diagnostic snapshots while the
        actual lifecycle/blockers stay unchanged. Those are intentionally
        coalesced so the durable statistics source records semantic changes, not
        30-second polling noise.
        """
        event = self._event_payload(job, event_type, at, details=details)
        if event_type == "updated":
            previous = next(
                (
                    item
                    for item in reversed(self.events)
                    if item.get("job_id") == job.job_id
                    and item.get("event_type") != "user_action"
                ),
                None,
            )
            if (
                previous is not None
                and self._semantic_event_signature(previous)
                == self._semantic_event_signature(event)
            ):
                return previous
        self.events.append(event)
        return event

    def events_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return the durable semantic lifecycle trace for one JobInstance."""
        key = str(job_id)
        return [dict(item) for item in self.events if str(item.get("job_id")) == key]

    def clear_dry_run_data(self) -> set[str]:
        """Delete only DRY_RUN runtime/history; REAL audit data is immutable here."""
        dry_ids = {
            job.job_id
            for job in [*self.active.values(), *self.history, *self.terminal_archive]
            if job.execution_mode is ExecutionMode.DRY_RUN
        }
        self.active = {
            key: job for key, job in self.active.items()
            if job.execution_mode is not ExecutionMode.DRY_RUN
        }
        self.history = [job for job in self.history if job.execution_mode is not ExecutionMode.DRY_RUN]
        self.terminal_archive = [
            job for job in self.terminal_archive if job.execution_mode is not ExecutionMode.DRY_RUN
        ]
        self.events = [item for item in self.events if item.get("job_id") not in dry_ids]
        # Keep only non-test future configuration in debug_state. Caller resets
        # clock/overrides/fault state explicitly.
        self.debug_state.clear()
        return dry_ids

    def clear_simulation_data(self) -> None:
        """Compatibility alias for pre-0.7 tests."""
        self.clear_dry_run_data()
