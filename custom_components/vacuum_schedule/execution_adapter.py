"""Physical vacuum adapters for Vacuum Schedule REAL execution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers import entity_registry as er

from .cleaning_scope import canonicalize_effective_cleaning_params
from .execution_models import ExecutionAttempt, RepetitionMode
from .execution_observer import RobotExecutionObservation, RobotExecutionObserver, RobotExecutionPhase


class PhysicalExecutionError(RuntimeError):
    """Base physical execution error with a stable machine code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


@dataclass(slots=True, frozen=True)
class TargetValidation:
    target_type: str
    targets: tuple[str, ...]
    metadata: dict[str, Any]


class VacuumExecutionAdapter:
    """Generic Home Assistant command adapter."""

    vendor = "generic"

    def __init__(
        self,
        hass: Any,
        executor: Any,
        *,
        persist_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.hass = hass
        self.executor = executor
        self.observer = RobotExecutionObserver(hass, executor)
        self.vendor = self.observer.vendor
        self._persist_callback = persist_callback

    def _loaded_vacuum_entity(self) -> Any | None:
        entity_id = self.executor.vacuum_entity_id
        component = self.hass.data.get(DATA_INSTANCES, {}).get("vacuum")
        return component.get_entity(entity_id) if component is not None and entity_id else None

    def observe(self, now: datetime) -> RobotExecutionObservation:
        return self.observer.observe(now)

    def observation_dependencies(self) -> tuple[str, ...]:
        """Return entity ids that can change the physical observation."""
        return self.observer.observation_dependencies()


    async def async_segment_catalog(self) -> dict[str, str]:
        """Return current Home Assistant segment ids/names for REAL readiness."""
        entity = self._loaded_vacuum_entity()
        getter = getattr(entity, "async_get_segments", None) if entity is not None else None
        cleaner = getattr(entity, "async_clean_segments", None) if entity is not None else None
        if getter is None or cleaner is None:
            raise PhysicalExecutionError("segment_cleaning_not_supported")
        try:
            current = await getter()
        except Exception as err:
            raise PhysicalExecutionError(
                "segment_validation_failed", f"{type(err).__name__}: {err}"
            ) from err
        return {
            str(getattr(item, "id", "")): str(getattr(item, "name", "") or getattr(item, "id", ""))
            for item in (current or [])
            if str(getattr(item, "id", ""))
        }

    @property
    def coordinate_zone_supported(self) -> bool:
        """Return whether this loaded adapter has the verified Roborock V1 path."""
        loaded = self._loaded_vacuum_entity()
        return self.vendor == "roborock" and loaded is not None and hasattr(loaded, "_status_trait")

    def native_repetitions_supported(
        self, target_type: str, targets: tuple[str, ...] | list[str]
    ) -> bool:
        """Return whether one device command can carry the requested passes.

        Generic Home Assistant segment cleaning intentionally has no repeat
        argument. Native repetitions are therefore enabled only for execution
        paths whose concrete protocol is known to carry them. Unknown adapters
        fall back to restart-safe emulation in ExecutionManager.
        """
        loaded = self._loaded_vacuum_entity()
        if loaded is None or self.vendor != "roborock":
            return False
        if target_type == "zone":
            return self.coordinate_zone_supported
        if target_type == "segment":
            # Current Roborock V1 entities expose map-qualified segment ids and
            # a raw APP_SEGMENT_CLEAN command path. B01/Q10-style entities do
            # not use this protocol and therefore remain on emulated passes.
            return (
                getattr(loaded, "_maps_trait", None) is not None
                and callable(getattr(loaded, "async_send_command", None))
                and bool(targets)
                and all("_" in str(target) for target in targets)
            )
        return False

    @staticmethod
    def _physical_repeat_count(attempt: ExecutionAttempt) -> int:
        if attempt.repetition_mode is RepetitionMode.NATIVE:
            return max(1, int(attempt.requested_passes or 1))
        return 1

    def _roborock_current_map_segments(
        self, attempt: ExecutionAttempt
    ) -> list[int]:
        loaded = self._loaded_vacuum_entity()
        maps_trait = getattr(loaded, "_maps_trait", None) if loaded is not None else None
        current_map = getattr(maps_trait, "current_map", None)
        segments: list[int] = []
        for target in attempt.targets:
            text = str(target)
            if "_" not in text:
                raise PhysicalExecutionError("native_repetitions_not_supported")
            map_flag, room_id = text.split("_", 1)
            if current_map is not None and str(map_flag) != str(current_map):
                raise PhysicalExecutionError("target_map_not_active", text)
            try:
                segments.append(int(room_id))
            except ValueError as err:
                raise PhysicalExecutionError("invalid_robot_segment", text) from err
        if not segments:
            raise PhysicalExecutionError("robot_target_missing")
        return segments

    async def async_validate_targets(self, attempt: ExecutionAttempt) -> TargetValidation:
        if attempt.target_type == "segment":
            entity = self._loaded_vacuum_entity()
            getter = getattr(entity, "async_get_segments", None) if entity is not None else None
            cleaner = getattr(entity, "async_clean_segments", None) if entity is not None else None
            if getter is None or cleaner is None:
                raise PhysicalExecutionError("segment_cleaning_not_supported")
            try:
                current = await getter()
            except Exception as err:
                raise PhysicalExecutionError("segment_validation_failed", f"{type(err).__name__}: {err}") from err
            available = {str(getattr(item, "id", "")): item for item in (current or [])}
            missing = [target for target in attempt.targets if target not in available]
            if missing:
                raise PhysicalExecutionError("robot_target_missing", ",".join(missing))
            return TargetValidation(
                target_type="segment",
                targets=attempt.targets,
                metadata={
                    "segments": {
                        target: str(getattr(available[target], "name", target))
                        for target in attempt.targets
                    }
                },
            )
        if attempt.target_type == "zone":
            if not self.coordinate_zone_supported:
                raise PhysicalExecutionError("zone_cleaning_vendor_not_supported")
            return TargetValidation("zone", attempt.targets, {})
        raise PhysicalExecutionError("unsupported_execution_target")

    @staticmethod
    def _parse_zone_target(value: str) -> list[list[Any]]:
        text = str(value).strip()
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
        if isinstance(parsed, dict):
            parsed = parsed.get("coordinates") or parsed.get("zone") or parsed.get("points") or []
        if not isinstance(parsed, list):
            raise PhysicalExecutionError("invalid_robot_zone")
        zones = parsed if parsed and isinstance(parsed[0], list) else [parsed]
        normalized: list[list[Any]] = []
        for zone in zones:
            coords = list(zone)
            if len(coords) < 4:
                raise PhysicalExecutionError("invalid_robot_zone")
            normalized.append(coords[:4])
        return normalized

    async def async_final_guard(self, attempt: ExecutionAttempt, now: datetime) -> RobotExecutionObservation:
        observation = self.observe(now)
        attempt.metadata["final_guard_observation"] = observation.to_dict()
        if observation.phase is RobotExecutionPhase.UNAVAILABLE:
            raise PhysicalExecutionError("vacuum_unavailable")
        if observation.resource_blockers:
            raise PhysicalExecutionError(
                observation.resource_blockers[0],
                ",".join(observation.resource_blockers),
            )
        if observation.phase is RobotExecutionPhase.ERROR:
            raise PhysicalExecutionError(
                "vacuum_error",
                observation.vacuum_error or observation.dock_error,
            )
        if observation.session_active or observation.phase in {
            RobotExecutionPhase.CLEANING,
            RobotExecutionPhase.PAUSED,
            RobotExecutionPhase.SERVICE,
            RobotExecutionPhase.SERVICE_BLOCKED,
            RobotExecutionPhase.RETURNING,
        }:
            raise PhysicalExecutionError("vacuum_busy")
        return observation

    async def _async_wait_state_value(
        self, entity_id: str, expected: str, *, timeout: float = 45.0
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        expected_text = str(expected)
        while True:
            state = self.hass.states.get(entity_id)
            if state is not None and str(state.state) == expected_text:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.25)

    async def _async_wait_fan_speed(
        self, entity_id: str, expected: str, *, timeout: float = 45.0
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            state = self.hass.states.get(entity_id)
            current = state.attributes.get("fan_speed") if state is not None else None
            if str(current) == str(expected):
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.25)

    def _roborock_volume_entity_id(self) -> str | None:
        """Return the public HA Roborock volume number for this robot.

        Roborock exposes speaker volume as a ``number`` entity with the
        ``volume`` translation key on the same device as the primary vacuum.
        Keep sound suppression on the public Home Assistant entity/service
        boundary rather than depending on private Roborock command traits.
        """
        if self.vendor != "roborock" or not self.executor.vacuum_entity_id:
            return None
        try:
            registry = er.async_get(self.hass)
            vacuum_entry = registry.async_get(self.executor.vacuum_entity_id)
        except Exception:
            return None
        device_id = getattr(vacuum_entry, "device_id", None) if vacuum_entry is not None else None
        if not device_id:
            return None
        candidates: list[tuple[int, str]] = []
        try:
            entries = er.async_entries_for_device(
                registry, device_id, include_disabled_entities=False
            )
        except Exception:
            return None
        for entry in entries:
            entity_id = str(getattr(entry, "entity_id", "") or "")
            if not entity_id.startswith("number."):
                continue
            if str(getattr(entry, "platform", "") or "").lower() != "roborock":
                continue
            state = self.hass.states.get(entity_id)
            if state is None or str(state.state).lower() in {"unknown", "unavailable"}:
                continue
            translation_key = str(getattr(entry, "translation_key", "") or "").lower()
            unique_id = str(getattr(entry, "unique_id", "") or "").lower()
            original_name = str(getattr(entry, "original_name", "") or "").lower()
            # Exact HA metadata wins. The secondary evidence keeps compatibility
            # with older registry records where translation_key may be missing.
            score = 0
            if translation_key == "volume":
                score = 100
            elif unique_id.startswith("volume_") or unique_id.endswith("_volume"):
                score = 50
            elif original_name == "volume":
                score = 25
            if score:
                candidates.append((score, entity_id))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    @staticmethod
    def _numeric_state_value(state: Any) -> float | None:
        if state is None or str(getattr(state, "state", "")).lower() in {"unknown", "unavailable"}:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    async def _async_wait_number_value(
        self, entity_id: str, expected: float, *, timeout: float = 15.0
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            current = self._numeric_state_value(self.hass.states.get(entity_id))
            if current is not None and abs(current - float(expected)) < 0.01:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.25)

    async def _async_set_volume_number(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": float(value)},
            blocking=True,
        )
        if not await self._async_wait_number_value(entity_id, float(value)):
            raise PhysicalExecutionError(
                "execution_volume_not_confirmed", f"{entity_id}:{value}"
            )

    async def _async_persist_sound_barrier(self) -> None:
        if self._persist_callback is not None:
            await self._persist_callback()

    async def _async_restore_volume_best_effort(
        self, attempt: ExecutionAttempt, entity_id: str, value: float, record: dict[str, Any]
    ) -> bool:
        """Restore speaker volume, retrying without masking the primary operation."""
        last_error: Exception | None = None
        for retry in range(3):
            try:
                await self._async_set_volume_number(entity_id, value)
                record["restored"] = True
                record["restore_retries"] = retry
                attempt.metadata.pop("pending_volume_restore", None)
                return True
            except Exception as err:  # keep original parameter error/result authoritative
                last_error = err
                if retry < 2:
                    await asyncio.sleep(0.5)
        record["restore_error"] = (
            f"{type(last_error).__name__}: {last_error}" if last_error is not None else "unknown"
        )
        attempt.metadata["pending_volume_restore"] = {
            "entity_id": entity_id,
            "value": value,
        }
        return False

    async def _async_with_parameter_sound_suppressed(
        self,
        attempt: ExecutionAttempt,
        operation: str,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        """Mute Roborock while scheduler-owned cleaning parameters are written."""
        entity_id = self._roborock_volume_entity_id()
        history = attempt.metadata.setdefault("parameter_sound_suppression", [])
        record: dict[str, Any] = {"operation": operation}
        history.append(record)
        if entity_id is None:
            record["status"] = "volume_entity_not_available"
            await action()
            return

        before = self._numeric_state_value(self.hass.states.get(entity_id))
        record.update({"entity_id": entity_id, "volume_before": before})
        if before is None:
            record["status"] = "volume_state_not_available"
            await action()
            return

        muted_by_scheduler = abs(before) >= 0.01
        if muted_by_scheduler:
            # Persist the exact pre-mute volume before the first physical write.
            # If Home Assistant restarts in this tiny window, recovery can still
            # put the speaker back to the user's previous level.
            attempt.metadata["pending_volume_restore"] = {
                "entity_id": entity_id,
                "value": before,
            }
            await self._async_persist_sound_barrier()
            try:
                await self._async_set_volume_number(entity_id, 0.0)
            except Exception as err:
                attempt.metadata.pop("pending_volume_restore", None)
                record["mute_error"] = f"{type(err).__name__}: {err}"
                # Do not make the requested parameter writes loudly when a usable
                # Roborock volume entity exists but could not be muted.
                raise PhysicalExecutionError(
                    "execution_volume_mute_failed", entity_id
                ) from err
            record["muted"] = True
        else:
            record["muted"] = False
            record["already_muted"] = True

        action_error: BaseException | None = None
        try:
            await action()
        except BaseException as err:
            action_error = err
        finally:
            if muted_by_scheduler:
                restored = await self._async_restore_volume_best_effort(
                    attempt, entity_id, before, record
                )
                if not restored and action_error is None:
                    action_error = PhysicalExecutionError(
                        "execution_volume_restore_failed", entity_id
                    )
        if action_error is not None:
            raise action_error

    async def async_restore_pending_volume(self, attempt: ExecutionAttempt) -> None:
        """Retry a volume restore left pending by an interrupted parameter transaction."""
        pending = attempt.metadata.get("pending_volume_restore")
        if not isinstance(pending, dict):
            return
        entity_id = str(pending.get("entity_id", "") or "")
        try:
            value = float(pending.get("value"))
        except (TypeError, ValueError):
            return
        if not entity_id:
            return
        record = {"operation": "pending_volume_restore", "entity_id": entity_id}
        attempt.metadata.setdefault("parameter_sound_suppression", []).append(record)
        if not await self._async_restore_volume_best_effort(attempt, entity_id, value, record):
            raise PhysicalExecutionError("execution_volume_restore_failed", entity_id)

    async def async_prepare(self, attempt: ExecutionAttempt) -> None:
        """Apply scheduler parameters while suppressing Roborock setting chimes."""
        params = canonicalize_effective_cleaning_params(attempt.cleaning_params)
        has_explicit_override = any(
            str(params.get(f"{key}_entity_id", "") or "").strip()
            and params.get(key) not in (None, "")
            for key in ("cleaning_mode", "cleaning_route", "mop_mode", "water_mode")
        ) or bool(str(params.get("fan_mode", "") or "").strip())
        if not has_explicit_override:
            return

        async def _apply() -> None:
            # Preserve the original pre-job snapshot across emulated physical
            # passes. Native repetitions remain one attempt and restore once.
            snapshot: dict[str, Any] = dict(attempt.metadata.get("parameter_snapshot_before", {}))
            applied: dict[str, Any] = dict(attempt.metadata.get("parameters_applied", {}))
            # Store the same mutable dictionaries before the first physical write so
            # a mid-preparation failure can restore parameters that were already
            # changed successfully.
            attempt.metadata["parameter_snapshot_before"] = snapshot
            attempt.metadata["parameters_applied"] = applied
            order = ("cleaning_mode", "cleaning_route", "mop_mode", "water_mode")
            for key in order:
                entity_id = str(params.get(f"{key}_entity_id", "") or "").strip()
                value = params.get(key)
                if not entity_id or value in (None, ""):
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or str(state.state) in {"unknown", "unavailable"}:
                    raise PhysicalExecutionError("execution_parameter_unavailable", entity_id)
                options = state.attributes.get("options")
                if isinstance(options, (list, tuple)) and str(value) not in {str(item) for item in options}:
                    raise PhysicalExecutionError("execution_parameter_invalid", f"{entity_id}:{value}")
                snapshot.setdefault(entity_id, state.state)
                domain = entity_id.split(".", 1)[0]
                if domain in {"select", "input_select"}:
                    await self.hass.services.async_call(
                        domain,
                        "select_option",
                        {"entity_id": entity_id, "option": str(value)},
                        blocking=True,
                    )
                elif domain in {"number", "input_number"}:
                    await self.hass.services.async_call(
                        domain,
                        "set_value",
                        {"entity_id": entity_id, "value": float(value)},
                        blocking=True,
                    )
                else:
                    raise PhysicalExecutionError("execution_parameter_unsupported", entity_id)
                # A blocking service return means the write was accepted by Home
                # Assistant; record the intended value before read-back so a later
                # confirmation failure can still compare-and-restore safely.
                applied[entity_id] = str(value)
                if not await self._async_wait_state_value(entity_id, str(value)):
                    raise PhysicalExecutionError("execution_parameter_not_confirmed", f"{entity_id}:{value}")

            fan_mode = str(params.get("fan_mode", "") or "").strip()
            if fan_mode:
                entity_id = self.executor.vacuum_entity_id
                if not entity_id:
                    raise PhysicalExecutionError("vacuum_entity_missing")
                state = self.hass.states.get(entity_id)
                if state is None:
                    raise PhysicalExecutionError("vacuum_unavailable")
                snapshot.setdefault(f"{entity_id}#fan_speed", state.attributes.get("fan_speed"))
                fan_options = state.attributes.get("fan_speed_list")
                if isinstance(fan_options, (list, tuple)) and fan_mode not in {str(item) for item in fan_options}:
                    raise PhysicalExecutionError("execution_parameter_invalid", f"fan:{fan_mode}")
                await self.hass.services.async_call(
                    "vacuum",
                    "set_fan_speed",
                    {"entity_id": entity_id, "fan_speed": fan_mode},
                    blocking=True,
                )
                applied[f"{entity_id}#fan_speed"] = fan_mode
                if not await self._async_wait_fan_speed(entity_id, fan_mode):
                    raise PhysicalExecutionError(
                        "execution_parameter_not_confirmed", f"fan:{fan_mode}"
                    )

            attempt.metadata["parameter_snapshot_before"] = snapshot
            attempt.metadata["parameters_applied"] = applied

        await self._async_with_parameter_sound_suppressed(attempt, "prepare", _apply)

    async def async_restore_parameters(self, attempt: ExecutionAttempt) -> None:
        """Compare-and-restore settings while suppressing Roborock setting chimes."""
        snapshot = dict(attempt.metadata.get("parameter_snapshot_before", {}))
        applied = dict(attempt.metadata.get("parameters_applied", {}))
        if not snapshot or not applied:
            return

        async def _restore() -> None:
            restored: dict[str, Any] = {}
            skipped: dict[str, Any] = {}
            for token, old_value in snapshot.items():
                if token.endswith("#fan_speed"):
                    entity_id = token[:-10]
                    state = self.hass.states.get(entity_id)
                    current = state.attributes.get("fan_speed") if state is not None else None
                    if str(current) != str(applied.get(token)):
                        skipped[token] = "changed_externally"
                        continue
                    if old_value not in (None, ""):
                        await self.hass.services.async_call(
                            "vacuum", "set_fan_speed",
                            {"entity_id": entity_id, "fan_speed": str(old_value)},
                            blocking=True,
                        )
                        restored[token] = old_value
                    continue
                state = self.hass.states.get(token)
                if state is None or str(state.state) != str(applied.get(token)):
                    skipped[token] = "changed_externally"
                    continue
                domain = token.split(".", 1)[0]
                if old_value in (None, "", "unknown", "unavailable"):
                    skipped[token] = "old_value_unusable"
                    continue
                if domain in {"select", "input_select"}:
                    await self.hass.services.async_call(
                        domain, "select_option",
                        {"entity_id": token, "option": str(old_value)},
                        blocking=True,
                    )
                    restored[token] = old_value
                elif domain in {"number", "input_number"}:
                    await self.hass.services.async_call(
                        domain, "set_value",
                        {"entity_id": token, "value": float(old_value)},
                        blocking=True,
                    )
                    restored[token] = old_value
            attempt.metadata["parameters_restored"] = restored
            attempt.metadata["parameter_restore_skipped"] = skipped

        await self._async_with_parameter_sound_suppressed(attempt, "restore", _restore)

    async def async_start_cleaning(self, attempt: ExecutionAttempt) -> None:
        repeats = self._physical_repeat_count(attempt)
        attempt.metadata["physical_command_passes"] = repeats
        attempt.metadata["repetition_mode"] = attempt.repetition_mode.value

        if attempt.target_type == "segment":
            entity = self._loaded_vacuum_entity()
            cleaner = getattr(entity, "async_clean_segments", None) if entity is not None else None
            if cleaner is None:
                raise PhysicalExecutionError("segment_cleaning_not_supported")
            if attempt.repetition_mode is RepetitionMode.NATIVE:
                if not self.native_repetitions_supported(attempt.target_type, attempt.targets):
                    raise PhysicalExecutionError("native_repetitions_not_supported")
                sender = getattr(entity, "async_send_command", None)
                segments = self._roborock_current_map_segments(attempt)
                # Roborock APP_SEGMENT_CLEAN carries the repeat count in the
                # same command object as the merged segment list. This is one
                # physical cleaning session, not N scheduler-issued starts.
                await sender(
                    "app_segment_clean",
                    [{"segments": segments, "repeat": repeats}],
                )
            else:
                await cleaner(list(attempt.targets))
            return

        if attempt.target_type == "zone" and self.coordinate_zone_supported:
            entity_id = self.executor.vacuum_entity_id
            if not entity_id:
                raise PhysicalExecutionError("vacuum_entity_missing")
            zones: list[list[Any]] = []
            for raw in attempt.targets:
                zones.extend(self._parse_zone_target(raw))
            # Roborock APP_ZONED_CLEAN encodes repetitions in each target tuple.
            # All compatible coordinate targets are submitted in one command.
            await self.hass.services.async_call(
                "vacuum",
                "send_command",
                {
                    "entity_id": entity_id,
                    "command": "app_zoned_clean",
                    "params": [[*coords, repeats] for coords in zones],
                },
                blocking=True,
            )
            return
        raise PhysicalExecutionError("unsupported_execution_target")

    async def async_pause(self) -> None:
        await self.executor.async_pause()

    async def async_resume(self) -> None:
        await self.executor.async_start()

    async def async_stop(self) -> None:
        await self.executor.async_stop()

    async def async_return_home(self) -> None:
        await self.executor.async_return_home()
