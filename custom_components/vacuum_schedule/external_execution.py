"""Observation-only tracking for physical cleanings started outside Scheduler.

External execution never acquires Scheduler's execution lease and never emits a
robot command.  The tracker only observes the already-running robot, persists a
small in-progress session for restart recovery, then materializes one terminal
``JobInstance`` so existing history/statistics/water pipelines can consume the
facts without inventing a scheduled occurrence.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .execution_models import (
    ExecutionAttempt,
    ExecutionAttemptState,
    ExecutionMode,
    RepetitionMode,
)
from .execution_observer import RobotExecutionObservation, RobotExecutionPhase
from .job import JobInstance, JobOrigin, JobReason, JobResult, JobState

_STORAGE_VERSION = 1
_COMPLETION_GRACE = timedelta(seconds=45)
_PREBUFFER_LIMIT = 40
_SESSION_OBSERVATION_LIMIT = 1200

_WET_STATUS = {
    "mopping",
    "sweep_and_mop",
    "washing_the_mop",
    "going_to_wash_the_mop",
}
_WASH_STATUS = {"washing_the_mop", "going_to_wash_the_mop"}
_DRY_STATUS = {"sweeping"}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _observation_changed(left: Mapping[str, Any] | None, right: Mapping[str, Any]) -> bool:
    """Keep telemetry compact while preserving metric/phase transitions."""
    if not left:
        return True
    keys = (
        "phase",
        "vendor_status",
        "session_active",
        "in_cleaning",
        "battery_percent",
        "cleaning_time_seconds",
        "cleaning_area_m2",
        "clean_percent",
        "wash_mode",
        "smart_wash",
        "wash_interval",
        "last_clean_start",
        "last_clean_end",
        "vacuum_error",
        "dock_error",
    )
    return any(left.get(key) != right.get(key) for key in keys)


def _cleaning_evidence(observation: RobotExecutionObservation) -> bool:
    """Return true only for actual floor-cleaning evidence, never dock service alone."""
    if observation.phase is not RobotExecutionPhase.CLEANING:
        return False
    status = str(observation.vendor_status or "").lower()
    # Roborock exposes several navigation/development states through the same
    # generic CLEANING phase.  Do not turn map building or remote control into a
    # cleaning history row.
    if status in {"mapping", "remote_control_active", "manual_mode", "going_to_target", "relocating"}:
        return False
    return True


def _wet_classification(observations: list[Mapping[str, Any]]) -> bool | None:
    """Return True/False only when telemetry proves wet/dry floor work."""
    statuses = [str(item.get("vendor_status") or "").lower() for item in observations]
    if any(status in _WET_STATUS for status in statuses):
        return True
    cleaning_statuses = [status for status in statuses if status]
    if cleaning_statuses and any(status in _DRY_STATUS for status in cleaning_statuses):
        # ``sweeping`` is explicit dry-floor evidence. Generic/segment/zoned
        # statuses remain unknown because Roborock does not encode mop use there.
        if not any(status in {"cleaning", "segment_cleaning", "zoned_cleaning"} for status in cleaning_statuses):
            return False
    return None


def _wash_evidence(observations: list[Mapping[str, Any]]) -> bool:
    return any(str(item.get("vendor_status") or "").lower() in _WASH_STATUS for item in observations)


class ExternalExecutionTracker:
    """Track one physical cleaning that Scheduler did not start."""

    def __init__(self, hass: HomeAssistant, entry_id: str, adapter: Any) -> None:
        self.hass = hass
        self.entry_id = str(entry_id)
        self.adapter = adapter
        self._store = Store(
            hass,
            _STORAGE_VERSION,
            f"vacuum_schedule.{self.entry_id}.external_execution",
        )
        self._session: dict[str, Any] | None = None
        self._prebuffer: list[dict[str, Any]] = []

    @property
    def active(self) -> bool:
        return self._session is not None

    async def async_start(self) -> None:
        raw = await self._store.async_load() or {}
        session = raw.get("session") if isinstance(raw, Mapping) else None
        self._session = dict(session) if isinstance(session, Mapping) else None

    async def async_stop(self) -> None:
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save({"session": dict(self._session) if self._session else None})

    def _buffer_observation(self, payload: dict[str, Any]) -> None:
        if _observation_changed(self._prebuffer[-1] if self._prebuffer else None, payload):
            self._prebuffer.append(payload)
            self._prebuffer = self._prebuffer[-_PREBUFFER_LIMIT:]

    @staticmethod
    def _safe_start(observation: RobotExecutionObservation, now: datetime) -> datetime:
        candidate = observation.last_clean_start
        if candidate is None:
            return now
        try:
            if candidate <= now and now - candidate <= timedelta(hours=12):
                return candidate
        except TypeError:
            pass
        return now

    def _start_session(self, observation: RobotExecutionObservation, now: datetime) -> None:
        start_at = self._safe_start(observation, now)
        useful_prebuffer: list[dict[str, Any]] = []
        for item in self._prebuffer:
            at = _parse_dt(item.get("observed_at"))
            if at is None:
                continue
            try:
                if start_at - timedelta(minutes=10) <= at <= now and str(item.get("vendor_status") or "").lower() in _WASH_STATUS:
                    useful_prebuffer.append(dict(item))
            except TypeError:
                continue
        current = observation.to_dict()
        observations = [*useful_prebuffer]
        if _observation_changed(observations[-1] if observations else None, current):
            observations.append(current)
        self._session = {
            "session_id": uuid4().hex,
            "detected_at": now.isoformat(),
            "start_at": start_at.isoformat(),
            "task_kind": observation.task_kind or "unknown",
            "vendor": observation.vendor,
            "observations": observations[-_SESSION_OBSERVATION_LIMIT:],
            "completion_candidate_at": None,
            "saw_cleaning": True,
            "saw_error": False,
        }
        self._prebuffer = []

    def _append_session_observation(self, observation: RobotExecutionObservation) -> bool:
        assert self._session is not None
        payload = observation.to_dict()
        rows = [dict(item) for item in self._session.get("observations") or [] if isinstance(item, Mapping)]
        if not _observation_changed(rows[-1] if rows else None, payload):
            return False
        rows.append(payload)
        self._session["observations"] = rows[-_SESSION_OBSERVATION_LIMIT:]
        if observation.phase is RobotExecutionPhase.CLEANING:
            self._session["saw_cleaning"] = True
        if observation.phase is RobotExecutionPhase.ERROR:
            self._session["saw_error"] = True
        if self._session.get("task_kind") in (None, "", "unknown") and observation.task_kind:
            self._session["task_kind"] = observation.task_kind
        return True

    def _build_job(self, finished_at: datetime) -> JobInstance:
        assert self._session is not None
        session = dict(self._session)
        rows = [dict(item) for item in session.get("observations") or [] if isinstance(item, Mapping)]
        start_at = _parse_dt(session.get("start_at")) or finished_at
        wet = _wet_classification(rows)
        wash = _wash_evidence(rows)
        task_kind = str(session.get("task_kind") or "unknown")
        target_type = task_kind if task_kind in {"segment", "zone"} else "unknown"
        params: dict[str, Any] = {}
        if wet is True or wash:
            # Existing water accounting only needs a positive wet-cleaning marker;
            # it intentionally does not fabricate a concrete user-selected mode.
            params["mop_mode"] = "external_detected"
            params["water_mode"] = "external_detected"

        job_id = uuid4().hex
        attempt = ExecutionAttempt.create(
            job_id=job_id,
            execution_mode=ExecutionMode.REAL,
            zone_ids=(),
            target_type=target_type,
            targets=(),
            cleaning_params=params,
            now=start_at,
            requested_passes=1,
            repetition_mode=RepetitionMode.SINGLE,
        )
        attempt.state = ExecutionAttemptState.COMPLETED
        attempt.requested_at = None
        attempt.start_confirmed_at = start_at
        attempt.completed_at = finished_at
        attempt.last_observed_at = _parse_dt(rows[-1].get("observed_at")) if rows else finished_at
        attempt.metadata = {
            "external_execution": True,
            "adapter_vendor": session.get("vendor") or "generic",
            "statistics_observations": rows,
            "start_observation": rows[0] if rows else {},
            "last_observation": rows[-1] if rows else {},
        }

        complete_water_facts = wet is not None or wash
        completion_evidence = any(item.get("last_clean_end") for item in rows)
        result = (
            JobResult.FAILED
            if bool(session.get("saw_error")) and not completion_evidence
            else JobResult.SUCCESS
        )
        reason = JobReason.EXECUTION_FAILED.value if result is JobResult.FAILED else JobReason.EXECUTION_SUCCESS.value
        job = JobInstance(
            job_id=job_id,
            occurrence_id=f"external:{session.get('session_id') or uuid4().hex}",
            schedule_id="",
            schedule_revision=0,
            schedule_name="External cleaning",
            created_at=start_at,
            warning_at=start_at,
            planned_start=start_at,
            deadline_at=finished_at,
            next_planned_start=None,
            target_type=target_type,
            targets=(),
            cleaning_params=params,
            origin=JobOrigin.EXTERNAL,
            state=JobState.FINISHED,
            result=result,
            reason_code=reason,
            last_state_change_at=finished_at,
            starting_at=start_at,
            actual_start=start_at,
            finished_at=finished_at,
            execution_mode=ExecutionMode.REAL,
            simulation=False,
            transition_generation=2,
            execution_attempts={attempt.attempt_id: attempt},
            metadata={
                "external_execution": {
                    "session_id": session.get("session_id"),
                    "detected_at": session.get("detected_at"),
                    "task_kind": task_kind,
                    "targets_resolved": False,
                    "parameters_resolved": bool(wet is not None or wash),
                    "forecast_eligible": False,
                    "water_observation_complete": bool(complete_water_facts),
                    "water_uncertain_clean_floor": not bool(complete_water_facts),
                }
            },
        )
        return job

    async def async_observe(
        self,
        now: datetime,
        *,
        scheduler_owned: bool,
    ) -> JobInstance | None:
        """Observe one state and return a terminal external Job when complete."""
        observation = self.adapter.observe(now)
        payload = observation.to_dict()

        if self._session is None:
            if scheduler_owned:
                # Never let Scheduler-owned post-clean service/wash observations
                # leak into the pre-buffer of a later external physical run.
                self._prebuffer = []
                return None
            self._buffer_observation(payload)
            if not _cleaning_evidence(observation):
                return None
            self._start_session(observation, now)
            await self._async_save()
            return None

        changed = self._append_session_observation(observation)
        assert self._session is not None

        # A Scheduler-owned REAL command starting after an external completion
        # candidate must never be merged into the old external observation set.
        if scheduler_owned and self._session.get("completion_candidate_at"):
            finished = _parse_dt(self._session.get("completion_candidate_at")) or now
            job = self._build_job(finished)
            self._session = None
            await self._async_save()
            return job

        active = observation.session_active or observation.phase in {
            RobotExecutionPhase.CLEANING,
            RobotExecutionPhase.PAUSED,
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
        }
        if active:
            if self._session.get("completion_candidate_at") is not None:
                self._session["completion_candidate_at"] = None
                changed = True
            if changed:
                await self._async_save()
            return None

        candidate = _parse_dt(self._session.get("completion_candidate_at"))
        if candidate is None:
            self._session["completion_candidate_at"] = now.isoformat()
            await self._async_save()
            return None
        try:
            ready = now - candidate >= _COMPLETION_GRACE
        except TypeError:
            ready = True
        if not ready:
            if changed:
                await self._async_save()
            return None

        job = self._build_job(candidate)
        self._session = None
        await self._async_save()
        return job
