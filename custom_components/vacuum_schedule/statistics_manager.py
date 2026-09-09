"""Coordinator for immutable job statistics and synthetic water accounting."""

from __future__ import annotations

from datetime import datetime
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4
import zipfile

from homeassistant.helpers.storage import Store

from .cleaning_scope import cleaning_scope
from .statistics_models import (
    STATISTICS_SCHEMA_VERSION,
    aggregate_records,
    build_job_comparison,
    build_statistics_record,
)
from .forecast_models import (
    FORECAST_METRICS,
    FORECAST_MODEL_SCHEMA_VERSION,
    ForecastPolicyConfig,
    archive_branch,
    compact_forecast_sample,
    estimate_from_samples,
    filtered_samples,
    model_tables,
    new_active_branch,
    normalize_branch,
)
from .statistics_store import StatisticsStore
from .charging_statistics import ChargingStatistics
from .water_statistics import WaterStatistics
from .const import VERSION
from .storage_migrations import (
    async_repair_statistics_area_history,
    async_repair_statistics_execution_sources,
    async_backfill_statistics_terminal_snapshots,
)


_DATA_STORE_VERSION = 1
_BACKUP_SCHEMA_VERSION = 1
_DATA_LAYERS = ("execution_history", "statistics", "models", "maintenance")
_WATER_SCOPE_REPAIR_ID = "0.12.46_water_cleaning_scope"


class StatisticsManager:
    """Own long-term ledgers without participating in scheduling decisions."""

    def __init__(self, hass: Any, entry: Any, input_provider: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.input_provider = input_provider
        self.store = StatisticsStore(hass, entry.entry_id)
        self.water = WaterStatistics(hass, entry, input_provider)
        self.charging = ChargingStatistics(hass, entry.entry_id, input_provider)
        self._forecast_branches: dict[str, dict[str, Any]] = {}
        self._control_store = Store(hass, _DATA_STORE_VERSION, f"vacuum_schedule.{entry.entry_id}.statistics.control")
        self._models_store = Store(hass, _DATA_STORE_VERSION, f"vacuum_schedule.{entry.entry_id}.statistics.models")
        self._statistics_reset_at: str | None = None
        self._statistics_epoch = 0
        self._models_initialized = False

    def _forecast_policy(self, metric: str) -> ForecastPolicyConfig:
        raw = self.input_provider.policy.forecasts.get(metric) if metric in FORECAST_METRICS else None
        return raw if isinstance(raw, ForecastPolicyConfig) else ForecastPolicyConfig.from_dict(raw)

    def _statistics_percentiles(self) -> dict[str, int]:
        """Return presentation percentiles aligned with current Forecast policies.

        Area has no independent Forecast policy.  Its Statistics percentile follows
        the Time policy because cleaned-area distributions describe the same execution
        sample population and are presented alongside Time in comparative tables.
        """
        values = {metric: int(self._forecast_policy(metric).percentile) for metric in FORECAST_METRICS}
        values["area"] = values["time"]
        return values

    def _ensure_forecast_branches(self, now: datetime | None = None) -> None:
        now = now or datetime.now().astimezone()
        for metric in FORECAST_METRICS:
            if metric not in self._forecast_branches:
                self._forecast_branches[metric] = new_active_branch(now=now)

    def _forecast_model_sample_count(self, *, include_archives: bool = True) -> int:
        """Count disposable forecast observations, including archived generations."""
        self._ensure_forecast_branches()
        total = 0
        for branch in self._forecast_branches.values():
            total += len(branch.get("samples") or [])
            if include_archives:
                for archive in branch.get("archives") or []:
                    if isinstance(archive, Mapping):
                        samples = archive.get("samples") or []
                        total += len(samples) if isinstance(samples, list) else int(archive.get("sample_count") or 0)
        return total

    async def _async_save_models(self) -> None:
        self._ensure_forecast_branches()
        await self._models_store.async_save({
            "initialized": True,
            "schema_version": FORECAST_MODEL_SCHEMA_VERSION,
            "updated_at": datetime.now().astimezone().isoformat(),
            "forecast_models": {metric: dict(self._forecast_branches[metric]) for metric in FORECAST_METRICS},
        })
        self._models_initialized = True

    async def _async_train_forecasts(
        self, record: Mapping[str, Any], *, persist: bool = True, water_usage: Mapping[str, Any] | None = None
    ) -> bool:
        if str(record.get("execution_mode") or "").upper() != "REAL":
            return False
        self._ensure_forecast_branches()
        job_id = str(record.get("job_id") or "")
        if water_usage is None:
            water_usage = self.water.job_usage_summary(job_id)
        changed = False
        now = datetime.now().astimezone().isoformat()
        for metric in FORECAST_METRICS:
            branch = self._forecast_branches[metric]
            if job_id and any(str(item.get("job_id") or "") == job_id for item in branch.get("samples") or []):
                continue
            sample = compact_forecast_sample(record, metric, water_usage=water_usage)
            if sample is None:
                continue
            branch.setdefault("samples", []).append(sample)
            branch["samples"] = list(branch["samples"])[-10000:]
            branch["updated_at"] = now
            changed = True
        if changed and persist:
            await self._async_save_models()
        return changed

    async def _async_bootstrap_forecasts(self, records: Sequence[Mapping[str, Any]]) -> None:
        now = datetime.now().astimezone()
        self._forecast_branches = {metric: new_active_branch(now=now) for metric in FORECAST_METRICS}
        real_records = [record for record in records if str(record.get("execution_mode") or "").upper() == "REAL"]
        water_by_job = self.water.job_usage_summaries([str(record.get("job_id") or "") for record in real_records])
        for record in real_records:
            job_id = str(record.get("job_id") or "")
            water_usage = water_by_job.get(job_id, {})
            for metric in FORECAST_METRICS:
                sample = compact_forecast_sample(record, metric, water_usage=water_usage)
                if sample is not None:
                    self._forecast_branches[metric]["samples"].append(sample)
        for branch in self._forecast_branches.values():
            rows = list(branch.get("samples") or [])[-10000:]
            branch["samples"] = rows
            times = [self._parse_time(item.get("at")) for item in rows]
            times = [item for item in times if item is not None]
            if times:
                branch["started_at"] = min(times).isoformat()
            branch["updated_at"] = now.isoformat()
        await self._async_save_models()

    async def _async_rebuild_water_forecasts_after_calibration_repair(
        self, records: Sequence[Mapping[str, Any]], *, reason: str = "water_calibration_v3",
        discard_archives: bool = False,
    ) -> None:
        """Rebuild active water forecast generations from repaired water facts.

        Time/battery models are unrelated and remain untouched. Water samples are
        disposable model observations, so rebuilding them is safer than carrying
        numeric values trained from the rejected v2 calibration into v3.
        """
        self._ensure_forecast_branches()
        now = datetime.now().astimezone()
        real_records = [record for record in records if str(record.get("execution_mode") or "").upper() == "REAL"]
        water_by_job = self.water.job_usage_summaries([str(record.get("job_id") or "") for record in real_records])
        for metric in ("clean_water", "dirty_water"):
            previous = self._forecast_branches[metric]
            branch = new_active_branch(now=now, generation=int(previous.get("generation") or 1) + 1)
            branch["archives"] = [] if discard_archives else [dict(item) for item in previous.get("archives") or [] if isinstance(item, Mapping)][-100:]
            branch["repair_reason"] = str(reason)
            for record in real_records:
                job_id = str(record.get("job_id") or "")
                sample = compact_forecast_sample(
                    record, metric, water_usage=water_by_job.get(job_id, {})
                )
                if sample is not None:
                    branch["samples"].append(sample)
            branch["samples"] = list(branch.get("samples") or [])[-10000:]
            times = [self._parse_time(item.get("at")) for item in branch["samples"]]
            times = [item for item in times if item is not None]
            if times:
                branch["started_at"] = min(times).isoformat()
            branch["updated_at"] = now.isoformat()
            self._forecast_branches[metric] = branch
        await self._async_save_models()

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def _refresh_water_samples(self, metric: str, samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Apply the current calibrated Synthetic Water Meter scale to stored observations.

        Water usage events are replayed when calibration changes. Forecast branches
        remain independently archivable, but their active numeric view must follow
        the current calibrated water ledger rather than freeze an obsolete scale.
        """
        if metric not in {"clean_water", "dirty_water"}:
            return [dict(item) for item in samples]
        key = "clean_used_ml_eq" if metric == "clean_water" else "dirty_gained_ml_eq"
        refreshed: list[dict[str, Any]] = []
        water_by_job = self.water.job_usage_summaries([str(item.get("job_id") or "") for item in samples])
        for item in samples:
            row = dict(item)
            if cleaning_scope(row.get("params") or {}).kind != "wet":
                # Defensive purge for pre-0.12.46 branches restored from backup
                # or archives: dry/unknown observations cannot train water.
                continue
            job_id = str(row.get("job_id") or "")
            current = water_by_job.get(job_id, {}) if job_id else {}
            current_total = current.get(key) if current.get("available") else None
            try:
                current_total = float(current_total) if current_total is not None else None
            except (TypeError, ValueError):
                current_total = None
            old_job = row.get("job_value")
            try:
                old_total = float(old_job) if old_job is not None else sum(float(z.get("value") or 0.0) for z in row.get("zones") or [])
            except (TypeError, ValueError):
                old_total = 0.0
            if current_total is not None and old_total > 0:
                ratio = current_total / old_total
                if old_job is not None:
                    row["job_value"] = float(old_job) * ratio
                zones = []
                for zone in row.get("zones") or []:
                    current_zone = dict(zone)
                    try:
                        current_zone["value"] = float(current_zone.get("value") or 0.0) * ratio
                    except (TypeError, ValueError):
                        pass
                    zones.append(current_zone)
                row["zones"] = zones
            refreshed.append(row)
        return refreshed

    def _job_after_statistics_reset(self, job: Any) -> bool:
        reset = self._parse_time(self._statistics_reset_at)
        if reset is None:
            return True
        at = getattr(job, "finished_at", None) or getattr(job, "planned_start", None)
        if at is None:
            return True
        try:
            return at > reset
        except TypeError:
            return True

    async def _async_repair_area_history(self, terminal_jobs: list[Any], events_for_job) -> int:
        """Delegate the one-time area-history repair."""
        return await async_repair_statistics_area_history(
            self, terminal_jobs, events_for_job
        )

    async def _async_repair_execution_sources(self, terminal_jobs: list[Any]) -> int:
        """Delegate the one-time execution-source repair."""
        return await async_repair_statistics_execution_sources(self, terminal_jobs)

    async def async_start(self, terminal_jobs: list[Any], events_for_job) -> None:
        control = await self._control_store.async_load() or {}
        self._statistics_reset_at = str(control.get("statistics_reset_at") or "") or None
        self._statistics_epoch = int(control.get("statistics_epoch") or 0)
        model_raw = await self._models_store.async_load()
        if isinstance(model_raw, Mapping) and model_raw.get("initialized") and isinstance(model_raw.get("forecast_models"), Mapping):
            self._models_initialized = True
            now = datetime.now().astimezone()
            self._forecast_branches = {
                metric: normalize_branch((model_raw.get("forecast_models") or {}).get(metric), now=now)
                for metric in FORECAST_METRICS
            }
        await self.store.async_load()
        await self.water.async_start()
        await self.charging.async_start()
        repaired = await self._async_repair_area_history(terminal_jobs, events_for_job)
        source_repaired = await self._async_repair_execution_sources(terminal_jobs)
        changed = bool(repaired or source_repaired)
        changed |= await async_backfill_statistics_terminal_snapshots(
            self, terminal_jobs, events_for_job
        )
        all_records = await self.store.async_records()
        water_scope_repaired = False
        if self.water.profile is not None and not self.store.repair_applied(_WATER_SCOPE_REPAIR_ID):
            # 0.12.46: regenerate every derived FLOOR_MOP/MOP_WASH fact from the
            # immutable Statistics Ledger. Older versions could count inherited
            # mop/water fields on an explicit vacuum-only weekday override.
            await self.water.async_repair_job_records(all_records)
            await self.store.async_mark_repair_applied(_WATER_SCOPE_REPAIR_ID)
            water_scope_repaired = True
        # Repair the narrow crash window where an immutable statistics record
        # was persisted but the separate water ledger was not. Water ingestion
        # is idempotent per Job ID.
        for record in all_records:
            await self.water.async_record_job(record)
        if changed or not self.store.aggregate_cache:
            await self.store.async_rebuild_cache()
        existing_real = await self.store.async_records({"execution_mode": "REAL"})
        if not self._models_initialized:
            await self._async_bootstrap_forecasts(existing_real)
        else:
            # Reconcile the independent Forecast Store against immutable REAL
            # ledger facts. This closes the crash window where Statistics Ledger
            # was committed but model training was not yet persisted. Training is
            # idempotent per Job ID and metric.
            self._ensure_forecast_branches()
            reconciled = False
            water_by_job = self.water.job_usage_summaries([str(record.get("job_id") or "") for record in existing_real])
            for record in existing_real:
                job_id = str(record.get("job_id") or "")
                reconciled = await self._async_train_forecasts(
                    record, persist=False, water_usage=water_by_job.get(job_id, {})
                ) or reconciled
            if water_scope_repaired:
                await self._async_rebuild_water_forecasts_after_calibration_repair(
                    existing_real, reason="water_cleaning_scope_v4", discard_archives=True
                )
            elif self.water.model_repair_applied:
                await self._async_rebuild_water_forecasts_after_calibration_repair(existing_real)
            elif reconciled:
                await self._async_save_models()


    async def async_sync_terminal_jobs(self, terminal_jobs: list[Any], events_for_job) -> int:
        """Append every terminal Job not yet present in the immutable ledger."""
        appended = 0
        for job in terminal_jobs:
            if self.store.contains_job(job.job_id) or not self._job_after_statistics_reset(job):
                continue
            record = build_statistics_record(
                job.to_dict(),
                events_for_job(job.job_id),
                recorded_at=datetime.now().astimezone(),
            )
            if await self.store.async_append(record):
                await self.water.async_record_job(record)
                await self._async_train_forecasts(record)
                appended += 1
        if appended:
            await self.store.async_rebuild_cache()
        return appended

    async def async_stop(self) -> None:
        await self.charging.async_stop()
        await self.water.async_stop()

    async def async_commit_job(self, job: Any, lifecycle_events: list[Mapping[str, Any]]) -> bool:
        if self.store.contains_job(job.job_id):
            return False
        record = build_statistics_record(
            job.to_dict(),
            lifecycle_events,
            recorded_at=datetime.now().astimezone(),
        )
        appended = await self.store.async_append(record)
        if appended:
            await self.water.async_record_job(record)
            await self._async_train_forecasts(record)
            await self.store.async_rebuild_cache()
        return appended

    def forecast_estimate(
        self,
        metric: str,
        *,
        zone_ids: list[str] | tuple[str, ...],
        cleaning_params: Mapping[str, Any] | None,
        minimum_samples: int = 3,
        now: datetime | None = None,
        pending_terminal_jobs: list[Any] | tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        """Return one policy-aware forecast without storage I/O."""
        if metric not in FORECAST_METRICS:
            raise ValueError("invalid_forecast_metric")
        self._ensure_forecast_branches(now)
        now = now or datetime.now().astimezone()
        policy = self._forecast_policy(metric)
        branch = self._forecast_branches[metric]
        samples = self._refresh_water_samples(metric, filtered_samples(branch, policy, now=now))
        # Terminal REAL jobs from this reconciliation may not be committed yet.
        # Fold them into the temporary decision set where the metric can be
        # derived without mutating the trained branch.
        for job in pending_terminal_jobs:
            job_id = str(getattr(job, "job_id", ""))
            if self.store.contains_job(job_id):
                continue
            mode = str(getattr(getattr(job, "execution_mode", None), "value", getattr(job, "execution_mode", ""))).upper()
            if mode != "REAL":
                continue
            recorded_at = getattr(job, "finished_at", None) or now
            record = build_statistics_record(job.to_dict(), (), recorded_at=recorded_at)
            sample = compact_forecast_sample(record, metric, water_usage=self.water.job_usage_summary(job_id))
            if sample is not None:
                samples.append(sample)
        estimate = estimate_from_samples(
            samples, zone_ids=zone_ids, cleaning_params=cleaning_params, policy=policy,
            minimum_samples=minimum_samples, metric=metric,
        )
        estimate.update({
            "metric": metric,
            "enabled": policy.enabled,
            "lookback_days": policy.lookback_days,
            "generation": int(branch.get("generation") or 1),
            "generation_started_at": branch.get("started_at"),
            "active_sample_count": len(samples),
        })
        return estimate

    def force_duration_estimate(
        self, *, zone_ids: list[str] | tuple[str, ...], cleaning_params: Mapping[str, Any] | None,
        minimum_samples: int = 3, pending_terminal_jobs: list[Any] | tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        """Compatibility wrapper: Force now consumes the time Forecast Model."""
        result = self.forecast_estimate(
            "time", zone_ids=zone_ids, cleaning_params=cleaning_params,
            minimum_samples=minimum_samples, pending_terminal_jobs=pending_terminal_jobs,
        )
        result["p90_seconds"] = result.get("raw_percentile_value") if int(result.get("percentile") or 0) == 90 else None
        return result

    def forecast_resource_state(self, metric: str) -> dict[str, Any]:
        """Return current synthetic resource capacity used by predictive checks.

        A recovered physical tank sensor can tell us that the blocking condition
        is gone before the user has explained *what service was performed*. That
        pending interpretation belongs to model/accounting, not operational
        readiness. Until it is confirmed the synthetic quantity is deliberately
        UNKNOWN so predictive water checks fail open, while the binary physical
        sensor remains the authoritative immediate blocker.
        """
        if metric == "clean_water":
            balance = dict(self.water.clean or {})
            pending = "clean" in self.water.pending_service
            estimate = balance.get("estimate_ml_eq")
            return {
                "known": (not pending) and bool(balance.get("known")) and estimate is not None,
                "available_value": None if pending else (float(estimate) if estimate is not None else None),
                "state_confidence": "pending_maintenance" if pending else balance.get("state_confidence"),
                "capacity_ml": balance.get("capacity_ml"),
                "maintenance_pending": pending,
            }
        if metric == "dirty_water":
            balance = dict(self.water.dirty or {})
            pending = "dirty" in self.water.pending_service
            capacity = balance.get("capacity_ml")
            estimate = balance.get("estimate_ml_eq")
            try:
                free = max(0.0, float(capacity) - float(estimate)) if capacity is not None and estimate is not None else None
            except (TypeError, ValueError):
                free = None
            return {
                "known": (not pending) and bool(balance.get("known")) and free is not None,
                "available_value": None if pending else free,
                "state_confidence": "pending_maintenance" if pending else balance.get("state_confidence"),
                "capacity_ml": capacity,
                "filled_ml_eq": estimate,
                "maintenance_pending": pending,
            }
        return {"known": False, "available_value": None}

    def forecast_payload(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return trained active branches and archives for the Forecast UI."""
        now = now or datetime.now().astimezone()
        self._ensure_forecast_branches(now)
        models: dict[str, Any] = {}
        for metric in FORECAST_METRICS:
            policy = self._forecast_policy(metric)
            branch = self._forecast_branches[metric]
            samples = self._refresh_water_samples(metric, filtered_samples(branch, policy, now=now))
            tables = model_tables(samples, policy, minimum_samples=3, metric=metric)
            archives = [
                {key: value for key, value in dict(item).items() if key != "samples"}
                for item in branch.get("archives") or [] if isinstance(item, Mapping)
            ]
            models[metric] = {
                "policy": policy.to_dict(),
                "generation": int(branch.get("generation") or 1),
                "started_at": branch.get("started_at"),
                "updated_at": branch.get("updated_at"),
                "stored_sample_count": len(branch.get("samples") or []),
                "active_sample_count": len(samples),
                "trained": any(row.get("trained") for row in tables["by_schedule"]) or any(row.get("trained") for row in tables["by_zone"]),
                "by_schedule": tables["by_schedule"],
                "by_zone": tables["by_zone"],
                "archives": archives,
            }
        return {"schema_version": FORECAST_MODEL_SCHEMA_VERSION, "minimum_samples": 3, "models": models}

    def forecast_archive_preview(self, metric: str, retain_days: int, *, now: datetime | None = None) -> dict[str, Any]:
        """Preview how many trained observations a partial archive will move/retain."""
        if metric not in FORECAST_METRICS:
            raise ValueError("invalid_forecast_metric")
        self._ensure_forecast_branches(now)
        now = now or datetime.now().astimezone()
        preview_branch, archive = archive_branch(self._forecast_branches[metric], retain_days=retain_days, now=now)
        policy = self._forecast_policy(metric)
        retained_active = self._refresh_water_samples(metric, filtered_samples(preview_branch, policy, now=now))
        tables = model_tables(retained_active, policy, minimum_samples=3, metric=metric)
        return {
            "metric": metric,
            "retain_days": max(0, min(3650, int(retain_days))),
            "archive_sample_count": int((archive or {}).get("sample_count") or 0),
            "retained_sample_count": len(preview_branch.get("samples") or []),
            "active_after_count": len(retained_active),
            "trained_after": any(row.get("trained") for row in tables["by_schedule"]) or any(row.get("trained") for row in tables["by_zone"]),
            "will_rotate_generation": archive is not None or int(retain_days) == 0,
        }

    async def async_archive_forecast_model(self, metric: str, retain_days: int) -> dict[str, Any]:
        if metric not in FORECAST_METRICS:
            raise ValueError("invalid_forecast_metric")
        self._ensure_forecast_branches()
        now = datetime.now().astimezone()
        branch, archive = archive_branch(self._forecast_branches[metric], retain_days=retain_days, now=now)
        self._forecast_branches[metric] = branch
        await self._async_save_models()
        return {"metric": metric, "archive": ({key: value for key, value in archive.items() if key != "samples"} if archive else None), "forecast": self.forecast_payload(now=now)}

    async def async_delete_forecast_archive(self, metric: str, archive_id: str) -> dict[str, Any]:
        if metric not in FORECAST_METRICS:
            raise ValueError("invalid_forecast_metric")
        self._ensure_forecast_branches()
        branch = self._forecast_branches[metric]
        before = len(branch.get("archives") or [])
        branch["archives"] = [dict(item) for item in branch.get("archives") or [] if str(item.get("archive_id") or "") != str(archive_id)]
        deleted = len(branch["archives"]) != before
        if deleted:
            branch["updated_at"] = datetime.now().astimezone().isoformat()
            await self._async_save_models()
        return {"deleted": deleted, "metric": metric, "archive_id": archive_id, "forecast": self.forecast_payload()}

    async def async_restore_forecast_archive(self, metric: str, archive_id: str) -> dict[str, Any]:
        if metric not in FORECAST_METRICS:
            raise ValueError("invalid_forecast_metric")
        self._ensure_forecast_branches()
        branch = self._forecast_branches[metric]
        archive = next((dict(item) for item in branch.get("archives") or [] if str(item.get("archive_id") or "") == str(archive_id)), None)
        if archive is None:
            raise ValueError("forecast_archive_not_found")
        now = datetime.now().astimezone()
        restored_samples = [dict(item) for item in archive.get("samples") or [] if isinstance(item, Mapping)]
        # Restoring an archive means returning that archived historical segment
        # to the currently active trained branch, not discarding newer retained
        # observations. De-duplicate by stable sample_id (or Job ID fallback).
        combined: dict[str, dict[str, Any]] = {}
        for item in [*(branch.get("samples") or []), *restored_samples]:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            identity = str(row.get("sample_id") or row.get("job_id") or uuid4().hex)
            combined[identity] = row
        merged = list(combined.values())
        merged.sort(key=lambda item: str(item.get("at") or ""))
        times = [self._parse_time(item.get("at")) for item in merged]
        times = [item for item in times if item is not None]
        self._forecast_branches[metric] = {
            "generation": int(branch.get("generation") or 1) + 1,
            "started_at": (min(times).isoformat() if times else now.isoformat()),
            "updated_at": now.isoformat(),
            "samples": merged[-10000:],
            "archives": [dict(item) for item in branch.get("archives") or []],
            "restored_from_archive_id": archive_id,
        }
        await self._async_save_models()
        return {"restored": True, "metric": metric, "archive_id": archive_id, "forecast": self.forecast_payload(now=now)}

    async def async_query(self, filters: Mapping[str, Any] | None = None, *, limit: int = 200) -> dict[str, Any]:
        # Water is persisted in a separate synthetic ledger. Enrich the complete
        # filtered record set before aggregation so schedule/zone profile tables
        # and global /m² values use the same Job population as the other metrics.
        filters = dict(filters or {})
        records = await self.store.async_records(filters)
        enriched: list[dict[str, Any]] = []
        water_by_job = self.water.job_usage_summaries([str(record.get("job_id") or "") for record in records])
        for record in records:
            current = dict(record)
            current["water_usage"] = water_by_job.get(str(record.get("job_id") or ""), {})
            enriched.append(current)
        current_zone_nominal_areas = {
            str(zone.zone_id): zone.nominal_area_m2
            for zone in self.input_provider.cleaning_zones
            if zone.nominal_area_m2 is not None
        }
        metric_percentiles = self._statistics_percentiles()
        aggregate = aggregate_records(
            enriched,
            current_zone_nominal_areas=current_zone_nominal_areas,
            metric_percentiles=metric_percentiles,
        )
        payload = {
            "schema_version": STATISTICS_SCHEMA_VERSION,
            "filters": filters,
            "aggregate": aggregate,
            "records": enriched[: max(0, min(1000, int(limit)))],
            "total_records": len(enriched),
            "months": list(self.store.months),
            "water": self.water.payload(),
            "percentiles": metric_percentiles,
        }
        charging = self.charging.payload(filters, percentile=metric_percentiles["battery"])
        payload["charging"] = charging
        # 0.10.1: the user-facing observed charge rate is device-level telemetry
        # gathered continuously on the dock, not a rare side-effect of a Job.
        battery = aggregate.setdefault("battery", {})
        battery["job_charge_rate_percent_per_minute"] = battery.get("charge_rate_percent_per_minute")
        if (charging.get("rate_percent_per_minute") or {}).get("count"):
            battery["charge_rate_percent_per_minute"] = charging["rate_percent_per_minute"]
            battery["charge_rate_source"] = "continuous_dock_observation"
        else:
            battery["charge_rate_source"] = "job_observation_fallback"
        battery["charge_session_count"] = int(charging.get("session_count") or 0)
        return payload


    async def _async_save_control(self) -> None:
        await self._control_store.async_save({
            "statistics_reset_at": self._statistics_reset_at,
            "statistics_epoch": self._statistics_epoch,
            "updated_at": datetime.now().astimezone().isoformat(),
        })

    def _backup_directory(self) -> Path:
        config = getattr(self.hass, "config", None)
        if config is not None and callable(getattr(config, "path", None)):
            return Path(config.path("vacuum_schedule_backups"))
        return Path("/config/vacuum_schedule_backups")

    @staticmethod
    def _json_bytes(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    async def _async_run_blocking(self, func, *args):
        runner = getattr(self.hass, "async_add_executor_job", None)
        if callable(runner):
            return await runner(func, *args)
        return await asyncio.to_thread(func, *args)

    @staticmethod
    def _write_backup_sync(directory: Path, filename: str, manifest: dict[str, Any], payloads: dict[str, bytes]) -> str:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            for name, data in payloads.items():
                archive.writestr(name, data)
        return str(path)

    @staticmethod
    def _read_backup_sync(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        with zipfile.ZipFile(path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            layers: dict[str, Any] = {}
            for layer in manifest.get("layers", []):
                name = str(layer)
                member = f"{name}.json"
                data = archive.read(member)
                expected = str((manifest.get("sha256") or {}).get(member) or "")
                if not expected:
                    raise ValueError(f"backup_checksum_missing:{member}")
                actual = hashlib.sha256(data).hexdigest()
                if expected != actual:
                    raise ValueError(f"backup_checksum_mismatch:{member}")
                layers[name] = json.loads(data.decode("utf-8"))
            return manifest, layers

    @staticmethod
    def _manifest_from_backup_sync(path: Path) -> dict[str, Any] | None:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                return json.loads(archive.read("manifest.json").decode("utf-8"))
        except Exception:
            return None

    @classmethod
    def _list_backups_sync(cls, directory: Path, entry_id: str) -> list[dict[str, Any]]:
        if not directory.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.glob("vacuum-schedule-*.zip"), reverse=True):
            manifest = cls._manifest_from_backup_sync(path)
            if not manifest or str(manifest.get("entry_id") or "") != str(entry_id):
                continue
            rows.append({
                "backup_id": path.name,
                "created_at": manifest.get("created_at"),
                "version": manifest.get("integration_version"),
                "reason": manifest.get("reason"),
                "layers": list(manifest.get("layers") or []),
                "counts": dict(manifest.get("counts") or {}),
                "size_bytes": path.stat().st_size,
            })
            if len(rows) >= 100:
                break
        return rows

    async def async_data_management_status(self, job_store: Any) -> dict[str, Any]:
        records = await self.store.async_records()
        maintenance = self.water.maintenance_payload(limit=1)
        backups = await self._async_run_blocking(
            self._list_backups_sync, self._backup_directory(), str(self.entry.entry_id)
        )
        return {
            "statistics_reset_at": self._statistics_reset_at,
            "statistics_epoch": self._statistics_epoch,
            "counts": {
                "execution_history": len(job_store.terminal_archive),
                "statistics": len(records),
                "models": self._forecast_model_sample_count()
                + int(self.water.calibration.get("clean_full_cycles", 0) or 0)
                + int(self.water.calibration.get("dirty_full_cycles", 0) or 0),
                "maintenance": int((maintenance.get("summary") or {}).get("confirmed_count") or len([s for s in self.water.maintenance_sessions if s.get("status") == "confirmed"])),
            },
            "backups": backups,
        }

    async def async_create_backup(self, job_store: Any, *, reason: str = "manual") -> dict[str, Any]:
        statistics_layer = {
            "ledger": await self.store.async_export_layer(),
            "charging": self.charging.export_layer(),
            "water": self.water.export_statistics_layer(),
            "control": {
                "statistics_reset_at": self._statistics_reset_at,
                "statistics_epoch": self._statistics_epoch,
            },
        }
        self._ensure_forecast_branches()
        model_layer = {
            "forecast": {
                "schema_version": FORECAST_MODEL_SCHEMA_VERSION,
                "branches": {metric: dict(self._forecast_branches[metric]) for metric in FORECAST_METRICS},
            },
            "water": self.water.export_model_layer(),
        }
        layers = {
            "execution_history": job_store.export_execution_history(),
            "statistics": statistics_layer,
            "models": model_layer,
            "maintenance": self.water.export_maintenance_layer(),
        }
        payloads = {f"{name}.json": self._json_bytes(payload) for name, payload in layers.items()}
        now = datetime.now().astimezone()
        stamp = now.strftime("%Y%m%d-%H%M%S")
        filename = f"vacuum-schedule-{self.entry.entry_id}-{stamp}-{uuid4().hex[:6]}.zip"
        counts = {
            "execution_history": len(job_store.terminal_archive),
            "statistics": len((statistics_layer.get("ledger") or {}).get("records") or []),
            "models": self._forecast_model_sample_count()
            + int(self.water.calibration.get("clean_full_cycles", 0) or 0)
            + int(self.water.calibration.get("dirty_full_cycles", 0) or 0),
            "maintenance": len((layers.get("maintenance") or {}).get("confirmed_sessions") or []),
        }
        manifest = {
            "schema_version": _BACKUP_SCHEMA_VERSION,
            "backup_id": filename,
            "created_at": now.isoformat(),
            "integration_version": VERSION,
            "entry_id": self.entry.entry_id,
            "reason": reason,
            "layers": list(_DATA_LAYERS),
            "counts": counts,
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
        }
        path = await self._async_run_blocking(self._write_backup_sync, self._backup_directory(), filename, manifest, payloads)
        return {"backup_id": filename, "path": path, "manifest": manifest}

    @staticmethod
    def _normalize_layers(layers: Sequence[str]) -> list[str]:
        selected: list[str] = []
        for value in layers:
            key = str(value)
            if key not in _DATA_LAYERS:
                raise ValueError(f"invalid_data_layer:{key}")
            if key not in selected:
                selected.append(key)
        if not selected:
            raise ValueError("no_data_layers_selected")
        return selected

    async def async_clear_data(self, job_store: Any, layers: Sequence[str]) -> dict[str, Any]:
        selected = self._normalize_layers(layers)
        backup = await self.async_create_backup(job_store, reason="before_clear")
        result: dict[str, Any] = {}
        if "statistics" in selected:
            self._statistics_epoch += 1
            self._statistics_reset_at = datetime.now().astimezone().isoformat()
            await self._async_save_control()
            result["statistics_records"] = await self.store.async_clear()
            result["charging_sessions"] = await self.charging.async_clear_history()
            result["water_statistics"] = await self.water.async_clear_statistics_layer()
        if "execution_history" in selected:
            result["execution_history"] = await job_store.async_clear_execution_history()
        if "models" in selected:
            result["forecast_model_samples"] = self._forecast_model_sample_count()
            now = datetime.now().astimezone()
            self._forecast_branches = {metric: new_active_branch(now=now) for metric in FORECAST_METRICS}
            await self._async_save_models()
            await self.water.async_clear_model_layer()
        if "maintenance" in selected:
            result["maintenance_sessions"] = await self.water.async_clear_maintenance_history()
        return {"cleared": selected, "backup": backup, "result": result}

    def _backup_path(self, backup_id: str) -> Path:
        name = Path(str(backup_id or "")).name
        if not name.startswith("vacuum-schedule-") or not name.endswith(".zip"):
            raise ValueError("invalid_backup_id")
        return self._backup_directory() / name

    async def async_restore_backup(self, job_store: Any, backup_id: str, layers: Sequence[str]) -> dict[str, Any]:
        selected = self._normalize_layers(layers)
        path = self._backup_path(backup_id)
        if not await self._async_run_blocking(path.exists):
            raise ValueError("backup_not_found")
        manifest, payloads = await self._async_run_blocking(self._read_backup_sync, path)
        if str(manifest.get("entry_id") or "") != str(self.entry.entry_id):
            raise ValueError("backup_entry_mismatch")
        missing = [layer for layer in selected if layer not in payloads]
        if missing:
            raise ValueError(f"backup_layer_missing:{','.join(missing)}")
        safety_backup = await self.async_create_backup(job_store, reason="before_restore")
        restored: dict[str, Any] = {}
        if "execution_history" in selected:
            restored["execution_history"] = await job_store.async_restore_execution_history(payloads["execution_history"])
        if "statistics" in selected:
            layer = dict(payloads["statistics"] or {})
            restored["statistics_records"] = await self.store.async_restore_layer(layer.get("ledger") or {})
            restored["charging_sessions"] = await self.charging.async_restore_layer(layer.get("charging") or {})
            restored["water_statistics"] = await self.water.async_restore_statistics_layer(layer.get("water") or {})
            control = dict(layer.get("control") or {})
            self._statistics_reset_at = str(control.get("statistics_reset_at") or "") or None
            self._statistics_epoch = int(control.get("statistics_epoch") or 0)
            await self._async_save_control()
        if "models" in selected:
            layer = dict(payloads["models"] or {})
            forecast = dict(layer.get("forecast") or {})
            branches = dict(forecast.get("branches") or {})
            now = datetime.now().astimezone()
            self._forecast_branches = {metric: normalize_branch(branches.get(metric), now=now) for metric in FORECAST_METRICS}
            self._models_initialized = True
            await self._async_save_models()
            await self.water.async_restore_model_layer(layer.get("water") or {})
            restored["forecast_model_samples"] = self._forecast_model_sample_count()
        if "maintenance" in selected:
            restored["maintenance_sessions"] = await self.water.async_restore_maintenance_layer(payloads["maintenance"])

        water_restore_repaired = False
        if "statistics" in selected and self.water.profile is not None:
            # Backups created before 0.12.46 can contain derived FLOOR_MOP facts
            # for explicit vacuum-only Jobs. Re-derive them from the immutable
            # restored Statistics Ledger immediately instead of waiting for a restart.
            restored_records = await self.store.async_records()
            await self.water.async_repair_job_records(restored_records)
            water_restore_repaired = True
        if ("models" in selected or water_restore_repaired) and self.water.profile is not None:
            # Time/battery branches keep the user's restored generations. Water
            # branches are disposable and must be rebuilt from corrected facts.
            existing_real = await self.store.async_records({"execution_mode": "REAL"})
            await self._async_rebuild_water_forecasts_after_calibration_repair(
                existing_real, reason="water_restore_v4", discard_archives=True
            )
            restored["water_forecast_rebuilt"] = True
        return {"restored": selected, "source_backup": backup_id, "safety_backup": safety_backup, "result": restored}

    async def async_delete_backup(self, backup_id: str) -> bool:
        path = self._backup_path(backup_id)
        if not await self._async_run_blocking(path.exists):
            return False
        await self._async_run_blocking(path.unlink)
        return True


    async def async_job_context(self, job_id: str) -> dict[str, Any]:
        """Return one immutable Job record plus presentation-only history context."""
        job_id = str(job_id or "")
        records = await self.store.async_records()
        current = next((row for row in records if str(row.get("job_id") or "") == job_id), None)
        if current is None:
            return {"record": None, "comparison": {"available": False, "reason": "record_unavailable", "minimum_samples": 3}, "water_usage": self.water.job_usage_summary(job_id)}
        return {
            "record": dict(current),
            "comparison": build_job_comparison(
                current, records, minimum_samples=3,
                metric_percentiles=self._statistics_percentiles(),
            ),
            "water_usage": self.water.job_usage_summary(job_id),
        }

    async def async_service_water(self, tank: str, action: str, *, value: float | None = None, pending_id: str | None = None) -> dict[str, Any]:
        return await self.water.async_service(tank, action, value=value, pending_id=pending_id)

    def maintenance_payload(self, *, limit: int = 100) -> dict[str, Any]:
        return self.water.maintenance_payload(limit=limit)

    async def async_save_maintenance(
        self,
        interpretations: Mapping[str, Any],
        *,
        session_id: str | None = None,
        occurred_at: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        return await self.water.async_save_maintenance(
            interpretations,
            session_id=session_id,
            occurred_at=occurred_at,
            note=note,
        )
