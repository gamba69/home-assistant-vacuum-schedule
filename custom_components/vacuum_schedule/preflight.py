"""Real pre-flight engine plus clearly separated synthetic state-machine injection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any, TYPE_CHECKING

try:
    from .bindings import BindingMode, ExecutionGateMode
    from .cleaning_scope import cleaning_scope
    from .job import JobInstance
    from .execution_models import ExecutionMode
    from .preflight_models import Blocker, PreflightDecision, PreflightPhase, PreflightReport, classify_blocker
    from .time_utils import instant_add, instant_delta, instant_gt, instant_lt
except ImportError:  # pragma: no cover - isolated source-file testing
    from bindings import BindingMode, ExecutionGateMode
    from cleaning_scope import cleaning_scope
    from job import JobInstance
    from execution_models import ExecutionMode
    from preflight_models import Blocker, PreflightDecision, PreflightPhase, PreflightReport, classify_blocker
    from time_utils import instant_add, instant_delta, instant_gt, instant_lt

if TYPE_CHECKING:
    try:
        from .input_provider import InputProvider, InputSnapshot, NormalizedInput, CleaningZoneInputSnapshot
    except ImportError:  # pragma: no cover
        from input_provider import InputProvider, InputSnapshot, NormalizedInput, CleaningZoneInputSnapshot


SYNTHETIC_WAIT_BLOCKERS = frozenset({
    "vacuum_busy",
    "vacuum_unavailable",
    "room_busy",
    "dnd",
    "battery_low",
})
SYNTHETIC_FAIL_BLOCKERS = frozenset({"global_disabled"})
SUPPORTED_BLOCKERS = tuple(sorted(SYNTHETIC_WAIT_BLOCKERS | SYNTHETIC_FAIL_BLOCKERS))


@dataclass(frozen=True, slots=True)
class SyntheticPreflightReport:
    """Legacy-compatible report for direct synthetic blocker unit tests."""

    decision: PreflightDecision
    phase: PreflightPhase
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "phase": self.phase.value,
            "blockers": list(self.blockers),
        }


class SimulationPreflightProvider:
    """Direct blocker injection retained only for state-machine regression tests."""

    def __init__(self) -> None:
        self._global: set[str] = set()
        self._per_job: dict[str, set[str]] = {}

    def set_blocker(self, blocker: str, enabled: bool, job_id: str | None = None) -> None:
        if blocker not in SUPPORTED_BLOCKERS:
            raise ValueError("unsupported_blocker")
        target = self._global if job_id is None else self._per_job.setdefault(job_id, set())
        if enabled:
            target.add(blocker)
        else:
            target.discard(blocker)
            if job_id is not None and not target:
                self._per_job.pop(job_id, None)

    def blockers_for(self, job: JobInstance) -> tuple[str, ...]:
        return tuple(sorted(self._global | self._per_job.get(job.job_id, set())))

    def evaluate(self, job: JobInstance, phase: PreflightPhase) -> SyntheticPreflightReport:
        blockers = self.blockers_for(job)
        if any(item in SYNTHETIC_FAIL_BLOCKERS for item in blockers):
            decision = PreflightDecision.FAIL
        elif blockers:
            decision = PreflightDecision.WAIT
        else:
            decision = PreflightDecision.PASS
        return SyntheticPreflightReport(decision=decision, phase=phase, blockers=blockers)

    def clear_job(self, job_id: str) -> None:
        self._per_job.pop(job_id, None)

    def clear_all(self) -> None:
        self._global.clear()
        self._per_job.clear()

    def as_dict(self) -> dict[str, object]:
        return {
            "supported": list(SUPPORTED_BLOCKERS),
            "global": sorted(self._global),
            "per_job": {key: sorted(value) for key, value in self._per_job.items()},
        }


class PreflightEngine:
    """Evaluate normalized live inputs for one scheduled occurrence."""

    def __init__(
        self, input_provider: InputProvider, synthetic: SimulationPreflightProvider | None = None,
        forecast_provider: Any | None = None,
    ) -> None:
        self.inputs = input_provider
        self.forecasts = forecast_provider
        # Synthetic injection remains available to isolated regression tests but
        # is no longer wired into the production SchedulerEngine in 0.7.0.
        self.synthetic = synthetic
        self.last_report: PreflightReport | None = None

    def evaluate(
        self, job: JobInstance, phase: PreflightPhase, now: datetime | None = None,
        *, zone_ids: tuple[str, ...] | None = None,
    ) -> PreflightReport:
        try:
            snapshot = self.inputs.snapshot(
                now, allow_test_overrides=job.execution_mode is ExecutionMode.DRY_RUN
            )
        except TypeError as err:
            # Isolated/unit providers from earlier stages expose snapshot(now)
            # only. Runtime InputProvider supports the explicit safety flag.
            if "allow_test_overrides" not in str(err):
                raise
            snapshot = self.inputs.snapshot(now)
        selected_zone_ids = tuple(zone_ids if zone_ids is not None else job.targets)
        blockers: list[Blocker] = []
        next_recheck: datetime | None = None
        now = now or snapshot.evaluated_at
        policy = self.inputs.policy
        forecast_checks: dict[str, Any] = {}

        # A manual binding is an explicit configuration contract. If its registry
        # entry/entity has been deleted, fail as invalid configuration instead of
        # silently treating the source as a temporarily unavailable optional input.
        resolver = getattr(self.inputs, "_resolve_registry_entity", None)
        states = getattr(getattr(self.inputs, "hass", None), "states", None)
        for key, binding in self.inputs.bindings.items():
            if binding.binding_mode is not BindingMode.MANUAL:
                continue
            # DND begin/end are validated together by the DND-specific check
            # below and only when the enable switch is ON. This prevents stale
            # time entities from blocking cleaning while DND is disabled.
            if key in {"dnd.starts_at", "dnd.ends_at"}:
                continue
            if callable(resolver):
                resolved_entity_id, _renamed = resolver(
                    binding.entity_id, binding.entity_registry_id
                )
            else:
                resolved_entity_id = binding.entity_id
            state = states.get(resolved_entity_id) if states is not None and resolved_entity_id else None
            if not resolved_entity_id or state is None:
                blockers.append(
                    self._block(
                        "invalid_configuration",
                        PreflightDecision.FAIL,
                        key,
                        entity_id=resolved_entity_id or binding.entity_id,
                        raw=binding.entity_id,
                        normalized=None,
                        details={
                            "binding_mode": binding.binding_mode.value,
                            "configured_entity_id": binding.entity_id,
                            "entity_registry_id": binding.entity_registry_id,
                            "problem": "manual_entity_not_found",
                        },
                    )
                )

        # Global gate is intentionally terminal at start: no WAIT backlog is created.
        if policy.execution_gate is ExecutionGateMode.DISABLED:
            blockers.append(self._block("global_disabled", PreflightDecision.FAIL, "execution_gate", normalized="disabled"))
        elif policy.execution_gate is ExecutionGateMode.DISABLED_UNTIL:
            until = policy.disabled_until_datetime
            if until is None or instant_lt(now, _as_local(until, now)):
                if until is not None:
                    next_recheck = _min_dt(next_recheck, _as_local(until, now))
                blockers.append(
                    self._block(
                        "global_disabled_until",
                        PreflightDecision.FAIL,
                        "execution_gate",
                        raw=policy.disabled_until,
                        normalized="disabled_until",
                        details={"disabled_until": policy.disabled_until},
                    )
                )

        vacuum_available = snapshot.values["vacuum.available"]
        activity = snapshot.values["vacuum.activity"]
        if not bool(vacuum_available.effective_value):
            blockers.append(self._input_block("vacuum_unavailable", PreflightDecision.WAIT, vacuum_available))
        elif activity.effective_value == "error":
            blockers.append(self._input_block("vacuum_error", PreflightDecision.FAIL, activity))
        elif activity.effective_value in {"cleaning", "paused", "returning", "unknown"}:
            blockers.append(self._input_block("vacuum_busy", PreflightDecision.WAIT, activity))

        battery = snapshot.values["vacuum.battery_percent"]
        charging = snapshot.values["vacuum.charging"]
        battery_binding = self.inputs.bindings["vacuum.battery_percent"]
        battery_threshold = _schedule_battery_threshold(job, policy.default_min_battery_percent)
        if battery_binding.binding_mode is not BindingMode.DISABLED:
            threshold = battery_threshold
            if battery.effective_available and _number(battery.effective_value) is not None:
                if float(battery.effective_value) < threshold:
                    decision = PreflightDecision.WAIT if charging.effective_value is True else PreflightDecision.FAIL
                    blockers.append(
                        self._input_block(
                            "battery_insufficient",
                            decision,
                            battery,
                            details={
                                "minimum_percent": threshold,
                                "charging": charging.effective_value,
                                "schedule_override": "minimum_battery_percent" in job.cleaning_params,
                            },
                            dependencies=_deps(battery, charging),
                        )
                    )
            elif battery.source_entity_id:
                blockers.append(self._input_block("battery_unavailable", PreflightDecision.WAIT, battery))

        self._check_optional_dock(snapshot, blockers)
        scope = cleaning_scope(job.cleaning_params)
        # Explicit vacuum-only occurrences must not be blocked by mop/water
        # resources inherited from the base profile. Unknown/default scope stays
        # conservative for dock-water resources because the robot may still mop.
        if not scope.explicitly_dry:
            self._check_binary_resource(snapshot, blockers, "dock.clean_water", "clean_water_insufficient")
            self._check_binary_resource(snapshot, blockers, "dock.dirty_water", "dirty_water_full")
            self._check_binary_resource(snapshot, blockers, "dock.detergent", "detergent_unavailable")
        self._check_mop(job, snapshot, blockers)

        # DND has three independent inputs: enable switch, begin time and end
        # time. The switch enables/disables the schedule itself. 0.5.9 uses
        # one minimum remaining start-window threshold for both the normal
        # occurrence deadline and an upcoming DND boundary.
        dnd = snapshot.values["dnd.active"]
        dnd_start = snapshot.values["dnd.starts_at"]
        dnd_end = snapshot.values["dnd.ends_at"]
        dnd_binding = self.inputs.bindings["dnd.active"]
        dnd_used = dnd_binding.binding_mode is not BindingMode.DISABLED
        window_reference = (
            job.planned_start
            if phase is PreflightPhase.ADVISORY and instant_lt(now, job.planned_start)
            else now
        )
        dnd_blocks_window = False
        dnd_release_at: datetime | None = None
        next_dnd_start: datetime | None = None
        next_dnd_end: datetime | None = None
        if dnd_used and dnd.source_entity_id and not dnd.effective_available:
            blockers.append(self._input_block("dnd_unavailable", PreflightDecision.WAIT, dnd))
        elif dnd_used and dnd.effective_available and dnd.effective_value is True:
            start_clock = _parse_clock(dnd_start.effective_value) if dnd_start.effective_available else None
            end_clock = _parse_clock(dnd_end.effective_value) if dnd_end.effective_available else None
            dependencies = _deps(dnd, dnd_start, dnd_end)
            if start_clock is None or end_clock is None or start_clock == end_clock:
                blockers.append(
                    self._input_block(
                        "invalid_configuration",
                        PreflightDecision.FAIL,
                        dnd,
                        details={
                            "problem": "dnd_schedule_missing_or_invalid",
                            "enabled": True,
                            "starts_at": dnd_start.effective_value,
                            "ends_at": dnd_end.effective_value,
                        },
                        dependencies=dependencies,
                    )
                )
            else:
                inside_actual, dnd_release_at = _dnd_window(window_reference, start_clock, end_clock)
                dnd_blocks_window = inside_actual
                if inside_actual:
                    decision = (
                        PreflightDecision.WAIT
                        if dnd_release_at is not None and instant_lt(dnd_release_at, job.deadline_at)
                        else PreflightDecision.FAIL
                    )
                    if dnd_release_at is not None and decision is PreflightDecision.WAIT:
                        next_recheck = _min_dt(next_recheck, dnd_release_at)
                    blockers.append(
                        self._input_block(
                            "dnd_active" if decision is PreflightDecision.WAIT else "dnd_window",
                            decision,
                            dnd,
                            details={
                                "enabled": True,
                                "starts_at": dnd_start.effective_value,
                                "ends_at": dnd_end.effective_value,
                                "evaluated_for": window_reference.isoformat(),
                            },
                            dependencies=dependencies,
                            estimated_release_at=dnd_release_at,
                        )
                    )
                else:
                    next_dnd_start, next_dnd_end = _next_dnd_window(window_reference, start_clock, end_clock)

        minimum_window_minutes = _schedule_start_window_threshold(
            job, policy.minimum_start_window_minutes
        )
        if minimum_window_minutes > 0 and not dnd_blocks_window:
            effective_cutoff = job.deadline_at
            cutoff_reason = "deadline"
            if next_dnd_start is not None and instant_lt(next_dnd_start, effective_cutoff):
                effective_cutoff = next_dnd_start
                cutoff_reason = "dnd_start"
            remaining = instant_delta(effective_cutoff, window_reference)
            minimum_remaining = timedelta(minutes=minimum_window_minutes)
            if remaining < minimum_remaining:
                decision = PreflightDecision.FAIL
                estimated_release_at = None
                # If the current allowed slice is too short only because DND
                # starts soon, waiting through DND is valid when a sufficiently
                # large slice remains before the occurrence deadline.
                if (
                    cutoff_reason == "dnd_start"
                    and next_dnd_end is not None
                    and instant_lt(next_dnd_end, job.deadline_at)
                    and instant_delta(job.deadline_at, next_dnd_end) >= minimum_remaining
                ):
                    decision = PreflightDecision.WAIT
                    estimated_release_at = next_dnd_end
                    next_recheck = _min_dt(next_recheck, next_dnd_end)
                blockers.append(
                    self._block(
                        "insufficient_time_window",
                        decision,
                        "time_window.remaining",
                        raw=max(0, int(remaining.total_seconds())),
                        normalized=max(0, int(remaining.total_seconds() // 60)),
                        details={
                            "minimum_minutes": minimum_window_minutes,
                            "remaining_minutes": max(0, int(remaining.total_seconds() // 60)),
                            "effective_cutoff": effective_cutoff.isoformat(),
                            "cutoff_reason": cutoff_reason,
                            "schedule_override": "minimum_start_window_minutes" in job.cleaning_params,
                        },
                        estimated_release_at=estimated_release_at,
                    )
                )
            else:
                cutoff = instant_add(
                    effective_cutoff, -minimum_remaining + timedelta(seconds=1)
                )
                if instant_gt(cutoff, window_reference):
                    next_recheck = _min_dt(next_recheck, cutoff)

        zone_map: dict[str, CleaningZoneInputSnapshot] = {}
        if job.target_type != "cleaning_zones":
            blockers.append(
                self._block(
                    "invalid_target", PreflightDecision.FAIL, "cleaning_zone.mapping",
                    raw=job.target_type, normalized=None, details={"target_type": job.target_type},
                )
            )
        else:
            configured = {zone.zone_id: zone for zone in self.inputs.cleaning_zones}
            for zone_id in selected_zone_ids:
                zone = configured.get(str(zone_id))
                zone_snapshot = snapshot.zones.get(str(zone_id))
                if zone is None or zone_snapshot is None:
                    blockers.append(
                        self._block(
                            "invalid_target", PreflightDecision.FAIL, "cleaning_zone.mapping",
                            raw=zone_id, normalized=None, details={"zone_id": str(zone_id), "problem": "cleaning_zone_not_found"},
                        )
                    )
                    continue
                valid_target, problem = self.inputs.target_configuration_valid(zone)
                if not valid_target:
                    blockers.append(
                        self._block(
                            "invalid_target", PreflightDecision.FAIL, "cleaning_zone.target",
                            raw=zone.robot_target_id, normalized=None, room_id=zone.zone_id,
                            details={
                                "zone_id": zone.zone_id, "zone_name": zone.name,
                                "robot_target_type": zone.robot_target_type.value,
                                "robot_target_id": zone.robot_target_id, "problem": problem,
                            },
                        )
                    )
                    continue
                zone_map[zone.zone_id] = zone_snapshot
                zone_blockers = self._zone_blockers(zone_snapshot)
                manual_overrides = job.metadata.get("manual_overrides", {})
                ignored_busy = {
                    str(item)
                    for item in (
                        manual_overrides.get("ignore_busy_zones", ())
                        if isinstance(manual_overrides, dict)
                        else ()
                    )
                }
                if zone.zone_id in ignored_busy:
                    # A user may explicitly accept occupancy for this one Job.
                    # This suppresses only the positive zone_busy blocker;
                    # unknown presence, access, DND, resources and every other
                    # safety/start condition remain authoritative.
                    zone_blockers = [item for item in zone_blockers if item.code != "zone_busy"]
                blockers.extend(zone_blockers)

        # 0.11.0 predictive resource checks.  Forecast absence is deliberately
        # fail-open; existing physical/input blockers above remain authoritative.
        if self.forecasts is not None:
            for metric in ("time", "battery", "clean_water", "dirty_water"):
                try:
                    estimate = self.forecasts.forecast_estimate(
                        metric, zone_ids=selected_zone_ids, cleaning_params=job.cleaning_params,
                        minimum_samples=3, now=now,
                    )
                except Exception as err:  # Forecasting may never make pre-flight unavailable.
                    estimate = {
                        "metric": metric, "enabled": False, "available": False,
                        "basis": "forecast_error", "error": f"{type(err).__name__}: {err}",
                    }
                check: dict[str, Any] = {**dict(estimate), "decision": "PASS"}
                forecast_checks[metric] = check
                if not bool(estimate.get("enabled")) or not bool(estimate.get("available")):
                    check["decision_reason"] = "disabled" if not bool(estimate.get("enabled")) else "untrained_or_unavailable"
                    continue
                predicted = _number(estimate.get("forecast_value"))
                if predicted is None or predicted < 0:
                    check["decision_reason"] = "forecast_value_unavailable"
                    continue

                if metric == "time":
                    predictive_cutoff = job.deadline_at
                    cutoff_reason = "deadline"
                    if next_dnd_start is not None and instant_lt(next_dnd_start, predictive_cutoff):
                        predictive_cutoff = next_dnd_start
                        cutoff_reason = "dnd_start"
                    remaining_seconds = max(0.0, instant_delta(predictive_cutoff, window_reference).total_seconds())
                    check.update({
                        "current_available_value": remaining_seconds,
                        "cutoff_at": predictive_cutoff.isoformat(),
                        "cutoff_reason": cutoff_reason,
                    })
                    if predicted > remaining_seconds:
                        decision = PreflightDecision.FAIL
                        release_at = None
                        if (
                            cutoff_reason == "dnd_start" and next_dnd_end is not None
                            and instant_lt(next_dnd_end, job.deadline_at)
                            and instant_delta(job.deadline_at, next_dnd_end).total_seconds() >= predicted
                        ):
                            decision = PreflightDecision.WAIT
                            release_at = next_dnd_end
                            next_recheck = _min_dt(next_recheck, next_dnd_end)
                        check.update({"decision": decision.value, "decision_reason": "forecast_exceeds_time_window"})
                        blockers.append(self._block(
                            "forecast_time_insufficient", decision, "forecast.time",
                            raw=remaining_seconds, normalized=predicted,
                            details={**dict(estimate), "available_seconds": remaining_seconds, "cutoff_at": predictive_cutoff.isoformat(), "cutoff_reason": cutoff_reason},
                            estimated_release_at=release_at,
                        ))
                    else:
                        check["decision_reason"] = "forecast_fits_time_window"
                    continue

                if metric == "battery":
                    current = _number(battery.effective_value) if battery.effective_available else None
                    projected_remaining = (current - predicted) if current is not None else None
                    check.update({
                        "current_available_value": current,
                        "minimum_remaining_percent": battery_threshold,
                        "projected_remaining_percent": projected_remaining,
                    })
                    if projected_remaining is not None and projected_remaining < battery_threshold:
                        decision = PreflightDecision.WAIT if charging.effective_value is True else PreflightDecision.FAIL
                        check.update({"decision": decision.value, "decision_reason": "projected_battery_below_minimum"})
                        blockers.append(self._block(
                            "forecast_battery_insufficient", decision, "forecast.battery",
                            raw=current, normalized=projected_remaining,
                            details={**dict(estimate), "current_percent": current, "projected_remaining_percent": projected_remaining, "minimum_percent": battery_threshold, "charging": charging.effective_value},
                            dependencies=_deps(battery, charging),
                        ))
                    else:
                        check["decision_reason"] = "projected_battery_sufficient" if current is not None else "current_battery_unknown_fail_open"
                    continue

                resource = self.forecasts.forecast_resource_state(metric)
                available_value = _number(resource.get("available_value")) if bool(resource.get("known")) else None
                check.update({"resource_state": dict(resource), "current_available_value": available_value})
                if available_value is None:
                    check["decision_reason"] = "synthetic_state_unknown_fail_open"
                    continue
                if available_value < predicted:
                    code = "forecast_clean_water_insufficient" if metric == "clean_water" else "forecast_dirty_water_insufficient"
                    check.update({"decision": PreflightDecision.WAIT.value, "decision_reason": "forecast_exceeds_synthetic_resource"})
                    blockers.append(self._block(
                        code, PreflightDecision.WAIT, f"forecast.{metric}",
                        raw=available_value, normalized=predicted,
                        details={**dict(estimate), "available_ml_eq": available_value, "resource_state": dict(resource)},
                    ))
                else:
                    check["decision_reason"] = "synthetic_resource_sufficient"

        # Stabilization delays expose an exact release timestamp. Arm the same
        # one-shot recheck mechanism used by DND/time-window boundaries so a
        # zone becomes eligible at the configured second, not at the watchdog.
        for blocker in blockers:
            if blocker.estimated_release_at is not None and instant_gt(blocker.estimated_release_at, now):
                next_recheck = _min_dt(next_recheck, blocker.estimated_release_at)

        # Direct state-machine injection is intentionally explicit and distinguishable.
        for synthetic in (self.synthetic.blockers_for(job) if self.synthetic is not None else ()):
            decision = (
                PreflightDecision.FAIL
                if synthetic in SYNTHETIC_FAIL_BLOCKERS
                else PreflightDecision.WAIT
            )
            blockers.append(
                self._block(
                    f"synthetic_{synthetic}",
                    decision,
                    f"synthetic.{synthetic}",
                    normalized=True,
                    details={"synthetic": True, "original_blocker": synthetic},
                )
            )

        decision = _aggregate_decision(blockers)
        # Subscribe to all live inputs relevant to this job, not only currently
        # failing ones. This lets a new blocker appear after warning time without
        # waiting for the safety watchdog.
        observed_dependencies = {
            item.source_entity_id
            for item in snapshot.values.values()
            if item.source_entity_id
        }
        for zone in zone_map.values():
            observed_dependencies.update(zone.dependencies)
        observed_dependencies.update(
            dep for blocker in blockers for dep in blocker.dependencies if dep
        )
        dependencies = tuple(sorted(str(dep) for dep in observed_dependencies if dep))
        report = PreflightReport(
            decision=decision,
            phase=phase,
            blockers=tuple(blockers),
            evaluated_at=now,
            input_snapshot_id=snapshot.snapshot_id,
            dependencies=dependencies,
            next_recheck_at=next_recheck,
            input_snapshot={**snapshot.to_dict(), "forecast": forecast_checks},
        )
        self.last_report = report
        return report

    def current_preview(self, job: JobInstance, now: datetime | None = None) -> PreflightReport:
        return self.evaluate(job, PreflightPhase.CURRENT_PREVIEW, now)

    def evaluate_zone(
        self, job: JobInstance, zone_id: str, phase: PreflightPhase, now: datetime | None = None
    ) -> PreflightReport:
        """Evaluate global checks plus one independent scheduler cleaning zone."""
        return self.evaluate(job, phase, now, zone_ids=(str(zone_id),))

    def validation_issues(self) -> list[dict[str, Any]]:
        """Return configuration problems without creating a JobInstance."""
        snapshot = self.inputs.snapshot()
        bindings = self.inputs.bindings
        policy = self.inputs.policy
        issues: list[dict[str, Any]] = []

        for key, binding in bindings.items():
            item = snapshot.values.get(key)
            if binding.binding_mode is BindingMode.MANUAL and not binding.entity_id:
                issues.append(_issue("error", key, "manual_source_missing", "invalid_configuration"))
            elif binding.binding_mode is BindingMode.MANUAL and binding.entity_id:
                resolved_entity_id, _renamed = self.inputs._resolve_registry_entity(
                    binding.entity_id, binding.entity_registry_id
                )
                if not resolved_entity_id or self.inputs.hass.states.get(resolved_entity_id) is None:
                    issues.append(_issue("error", key, "entity_not_found", "invalid_configuration", entity_id=binding.entity_id))
            elif item is not None and item.status == "requires_attention" and binding.binding_mode is not BindingMode.DISABLED:
                binary_resource = key in {"dock.clean_water", "dock.dirty_water", "dock.detergent", "mop.attached"}
                # Binary resources use "check if found": absence of an AUTO candidate is valid.
                if binary_resource and binding.binding_mode is BindingMode.AUTO and not item.source_entity_id:
                    continue
                severity = "error" if binary_resource and item.source_entity_id else "warning"
                problem = "binary_normal_state_missing" if binary_resource and item.source_entity_id else (item.note or "requires_attention")
                issues.append(_issue(severity, key, problem, "invalid_configuration" if severity == "error" else "required_capability_missing", entity_id=item.source_entity_id))

        for zone in self.inputs.cleaning_zones:
            # Multiple logical Cleaning Zones may intentionally reference the
            # same physical target. ExecutionPlanBuilder deduplicates that
            # physical target within one execution attempt.
            valid_target, problem = self.inputs.target_configuration_valid(zone)
            if not valid_target:
                issues.append(_issue("error", zone.name, problem or "robot_target_missing", "invalid_target", room_id=zone.zone_id))
            for condition in zone.busy_sources:
                resolved_entity_id, _renamed = self.inputs._resolve_condition_entity(condition)
                if not resolved_entity_id or self.inputs.hass.states.get(resolved_entity_id) is None:
                    issues.append(_issue("error", zone.name, "zone_source_not_found", "zone_state_unknown", room_id=zone.zone_id, entity_id=condition.entity_id))
            for path in zone.access_paths:
                if not path.conditions:
                    issues.append(_issue("warning", f"{zone.name} / {path.name}", "empty_access_path", "zone_access_blocked", room_id=zone.zone_id))
                for condition in path.conditions:
                    resolved_entity_id, _renamed = self.inputs._resolve_condition_entity(condition)
                    if not resolved_entity_id or self.inputs.hass.states.get(resolved_entity_id) is None:
                        issues.append(_issue("error", f"{zone.name} / {path.name}", "path_source_not_found", "zone_access_blocked", room_id=zone.zone_id, entity_id=condition.entity_id))
        return issues

    def _check_optional_dock(self, snapshot: InputSnapshot, blockers: list[Blocker]) -> None:
        dock = snapshot.values["dock.available"]
        binding = self.inputs.bindings["dock.available"]
        if binding.binding_mode is BindingMode.DISABLED:
            return
        # Absence of an auto-discovered dock is not an error. Once a concrete source
        # exists (manual or autodiscovered), its loss/unavailable state is recoverable WAIT.
        has_concrete_source = binding.binding_mode is BindingMode.MANUAL or bool(dock.source_entity_id)
        if has_concrete_source and (not dock.effective_available or not bool(dock.effective_value)):
            blockers.append(self._input_block("dock_unavailable", PreflightDecision.WAIT, dock))

    def _check_binary_resource(
        self, snapshot: InputSnapshot, blockers: list[Blocker], key: str, blocked_code: str
    ) -> None:
        """Check a simple binary resource: effective True means resource is OK."""
        binding = self.inputs.bindings[key]
        if binding.binding_mode is BindingMode.DISABLED:
            return
        item = snapshot.values[key]
        # AUTO with no detected binary entity means "use if found" and is skipped.
        if not item.source_entity_id and binding.binding_mode is BindingMode.AUTO:
            return
        if not item.effective_available:
            blockers.append(self._input_block(f"{key.replace('.', '_')}_unavailable", PreflightDecision.WAIT, item))
            return
        if item.status != "ready":
            blockers.append(
                self._input_block(
                    "invalid_configuration", PreflightDecision.FAIL, item,
                    details={"problem": "binary_normal_state_missing", "required_input": key},
                )
            )
            return
        if item.effective_value is False:
            # A correctly configured physical resource can be serviced by the
            # user while the occurrence is still inside its execution window.
            # Keep the Job in WAIT and let the normal dependency/watchdog path
            # release it when the resource becomes ready.
            blockers.append(self._input_block(blocked_code, PreflightDecision.WAIT, item))

    def _check_mop(self, job: JobInstance, snapshot: InputSnapshot, blockers: list[Blocker]) -> None:
        if not cleaning_scope(job.cleaning_params).uses_water:
            return
        self._check_binary_resource(snapshot, blockers, "mop.attached", "mop_not_attached")

    def _zone_blockers(self, zone: CleaningZoneInputSnapshot) -> list[Blocker]:
        result: list[Blocker] = []
        deps = zone.dependencies
        if not zone.busy.effective_available:
            result.append(self._zone_block("zone_state_unknown", PreflightDecision.WAIT, zone, zone.busy, deps))
        elif zone.busy.effective_value is True:
            result.append(
                self._zone_block(
                    "zone_busy", PreflightDecision.WAIT, zone, zone.busy, deps,
                    extra={
                        "stabilizing": bool(getattr(zone.busy, "stabilizing", False)),
                        "configured_delay_seconds": int(getattr(zone.busy, "configured_delay_seconds", 0) or 0),
                    },
                    estimated_release_at=getattr(zone.busy, "stabilizing_until", None),
                )
            )

        if not zone.accessible.effective_available or zone.accessible.effective_value is False:
            closed = [item for item in zone.path_details if not item.get("available") or not item.get("matched")]
            result.append(
                self._zone_block(
                    "zone_access_blocked", PreflightDecision.WAIT, zone, zone.accessible, deps,
                    extra={
                        "paths": list(zone.path_details), "blocking_conditions": closed,
                        "stabilizing": bool(getattr(zone.accessible, "stabilizing", False)),
                        "configured_delay_seconds": int(getattr(zone.accessible, "configured_delay_seconds", 0) or 0),
                    },
                    estimated_release_at=getattr(zone.accessible, "stabilizing_until", None),
                )
            )
        return result

    def _zone_block(
        self, code: str, decision: PreflightDecision, zone: CleaningZoneInputSnapshot,
        item: NormalizedInput, deps: tuple[str, ...], *, extra: dict[str, Any] | None = None,
        estimated_release_at: datetime | None = None,
    ) -> Blocker:
        details = {
            "zone_id": zone.zone_id, "zone_name": zone.name,
            "robot_target_type": zone.robot_target_type, "robot_target_id": zone.robot_target_id,
            **(extra or {}),
        }
        return self._input_block(
            code, decision, item, room_id=zone.zone_id, details=details, dependencies=deps,
            estimated_release_at=estimated_release_at,
        )

    @staticmethod
    def _block(
        code: str,
        decision: PreflightDecision,
        source_key: str,
        *,
        entity_id: str | None = None,
        room_id: str | None = None,
        raw: Any = None,
        normalized: Any = None,
        details: dict[str, Any] | None = None,
        dependencies: tuple[str, ...] = (),
        estimated_release_at: datetime | None = None,
    ) -> Blocker:
        scope, attention = classify_blocker(code, source_key, room_id)
        return Blocker(
            code=code,
            decision_class=decision,
            source_key=source_key,
            entity_id=entity_id,
            room_id=room_id,
            raw_value=raw,
            normalized_value=normalized,
            translation_key=code,
            scope=scope,
            attention=attention,
            details=details or {},
            dependencies=tuple(sorted(set(dependencies))),
            expected_release_condition=None,
            estimated_release_at=estimated_release_at,
        )

    def _input_block(
        self,
        code: str,
        decision: PreflightDecision,
        item: NormalizedInput,
        *,
        room_id: str | None = None,
        details: dict[str, Any] | None = None,
        dependencies: tuple[str, ...] | None = None,
        estimated_release_at: datetime | None = None,
    ) -> Blocker:
        deps = dependencies if dependencies is not None else _deps(item)
        return self._block(
            code,
            decision,
            item.key,
            entity_id=item.source_entity_id,
            room_id=room_id,
            raw=item.raw_value if item.raw_value is not None else item.live_value,
            normalized=item.effective_value,
            details=details,
            dependencies=deps,
            estimated_release_at=estimated_release_at,
        )


def _aggregate_decision(blockers: list[Blocker]) -> PreflightDecision:
    if any(item.decision_class is PreflightDecision.FAIL for item in blockers):
        return PreflightDecision.FAIL
    if blockers:
        return PreflightDecision.WAIT
    return PreflightDecision.PASS


def _schedule_battery_threshold(job: JobInstance, default: float) -> float:
    try:
        value = float(job.cleaning_params.get("minimum_battery_percent", default))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(100.0, value))


def _schedule_start_window_threshold(job: JobInstance, default: int) -> int:
    try:
        value = int(job.cleaning_params.get("minimum_start_window_minutes", default))
    except (TypeError, ValueError):
        value = int(default)
    return max(0, value)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deps(*items: NormalizedInput) -> tuple[str, ...]:
    return tuple(sorted({item.source_entity_id for item in items if item.source_entity_id}))


def _parse_clock(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None)
    text = str(value).strip()
    try:
        return time.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).timetz().replace(tzinfo=None)
    except ValueError:
        return None


def _next_dnd_window(reference: datetime, start_clock: time, end_clock: time) -> tuple[datetime, datetime]:
    """Return the next DND start/end pair strictly after an allowed instant."""
    local = reference
    start_today = local.replace(
        hour=start_clock.hour, minute=start_clock.minute, second=start_clock.second, microsecond=0
    )
    if start_today <= local:
        start_at = start_today + timedelta(days=1)
    else:
        start_at = start_today
    end_at = start_at.replace(
        hour=end_clock.hour, minute=end_clock.minute, second=end_clock.second, microsecond=0
    )
    if end_clock <= start_clock:
        end_at += timedelta(days=1)
    return start_at, end_at


def _dnd_window(reference: datetime, starts_at: time, ends_at: time) -> tuple[bool, datetime | None]:
    """Return whether reference is inside DND and the next end instant.

    A start later than end is the normal overnight case (for example
    22:00 -> 08:00).  The end boundary itself is allowed for cleaning.
    """
    current = reference.timetz().replace(tzinfo=None)
    if starts_at < ends_at:
        inside = starts_at <= current < ends_at
        release_day = reference.date()
    else:
        inside = current >= starts_at or current < ends_at
        release_day = reference.date() + timedelta(days=1) if current >= starts_at else reference.date()
    if not inside:
        return False, None
    release = datetime.combine(release_day, ends_at, tzinfo=reference.tzinfo)
    return True, release


def _parse_dt(value: Any, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return _as_local(parsed, now)


def _as_local(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _min_dt(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if instant_lt(left, right) else right


def _issue(
    severity: str,
    object_name: str,
    problem: str,
    blocker: str,
    *,
    room_id: str | None = None,
    entity_id: str | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "object": object_name,
        "problem": problem,
        "preflight_blocker": blocker,
        "room_id": room_id,
        "entity_id": entity_id,
    }
