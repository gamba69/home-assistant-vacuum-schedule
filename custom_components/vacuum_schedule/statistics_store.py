"""Monthly immutable Statistics Ledger persistence for Vacuum Schedule."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .statistics_models import STATISTICS_SCHEMA_VERSION, aggregate_records, record_matches

_INDEX_VERSION = 1
_CHUNK_VERSION = 1
_CACHE_VERSION = 1


class StatisticsStore:
    """Persist monthly ledger chunks plus a fully rebuildable aggregate cache."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._index_store = Store(hass, _INDEX_VERSION, f"vacuum_schedule.{entry_id}.statistics.index")
        self._cache_store = Store(hass, _CACHE_VERSION, f"vacuum_schedule.{entry_id}.statistics.cache")
        self._months: list[str] = []
        self._job_month: dict[str, str] = {}
        self._loaded_chunks: dict[str, list[dict[str, Any]]] = {}
        self.aggregate_cache: dict[str, Any] = {}
        self._applied_repairs: set[str] = set()

    @staticmethod
    def month_for_record(record: Mapping[str, Any]) -> str:
        raw = record.get("planned_start") or record.get("finished_at") or record.get("recorded_at")
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            value = datetime.now().astimezone()
        return value.strftime("%Y-%m")

    def _chunk_store(self, month: str) -> Store:
        return Store(self.hass, _CHUNK_VERSION, f"vacuum_schedule.{self.entry_id}.statistics.{month}")

    async def async_load(self) -> None:
        raw = await self._index_store.async_load() or {}
        self._months = sorted({str(value) for value in raw.get("months", []) if value})
        self._job_month = {str(key): str(value) for key, value in dict(raw.get("job_month", {})).items()}
        self._applied_repairs = {str(value) for value in raw.get("applied_repairs", []) if value}
        cache = await self._cache_store.async_load() or {}
        self.aggregate_cache = dict(cache.get("aggregate") or {})

    async def _async_load_month(self, month: str) -> list[dict[str, Any]]:
        month = str(month)
        if month in self._loaded_chunks:
            return self._loaded_chunks[month]
        raw = await self._chunk_store(month).async_load() or {}
        records = [dict(item) for item in raw.get("records", []) if isinstance(item, Mapping)]
        self._loaded_chunks[month] = records
        return records

    async def _async_save_index(self) -> None:
        await self._index_store.async_save(
            {
                "schema_version": STATISTICS_SCHEMA_VERSION,
                "months": list(self._months),
                "job_month": dict(self._job_month),
                "applied_repairs": sorted(self._applied_repairs),
            }
        )

    def contains_job(self, job_id: str) -> bool:
        return str(job_id) in self._job_month

    @property
    def months(self) -> tuple[str, ...]:
        """Return known ledger months without exposing mutable store internals."""
        return tuple(self._months)

    def repair_applied(self, repair_id: str) -> bool:
        return str(repair_id) in self._applied_repairs

    async def async_mark_repair_applied(self, repair_id: str) -> None:
        self._applied_repairs.add(str(repair_id))
        await self._async_save_index()

    async def async_replace_records(self, replacements: Mapping[str, Mapping[str, Any]]) -> int:
        """Replace existing records by Job ID for an explicit repair migration.

        Normal ingestion remains append-only.  This narrow API is intentionally
        separate so deterministic bug repairs can preserve the original ledger
        identity while rewriting only fields reconstructed from durable Job data.
        """
        pending = {str(job_id): dict(record) for job_id, record in replacements.items() if str(job_id)}
        if not pending:
            return 0
        changed_count = 0
        by_month: dict[str, dict[str, dict[str, Any]]] = {}
        for job_id, record in pending.items():
            month = self._job_month.get(job_id)
            if month:
                by_month.setdefault(month, {})[job_id] = record
        for month, month_replacements in by_month.items():
            records = await self._async_load_month(month)
            changed = False
            rewritten: list[dict[str, Any]] = []
            for record in records:
                job_id = str(record.get("job_id") or "")
                replacement = month_replacements.get(job_id)
                if replacement is None:
                    rewritten.append(record)
                    continue
                # Repair must not silently move or re-identify a ledger row.
                fixed = dict(replacement)
                fixed["record_id"] = record.get("record_id")
                fixed["recorded_at"] = record.get("recorded_at")
                fixed["job_id"] = job_id
                rewritten.append(fixed)
                changed = True
                changed_count += 1
            if changed:
                self._loaded_chunks[month] = rewritten
                await self._chunk_store(month).async_save(
                    {"schema_version": STATISTICS_SCHEMA_VERSION, "month": month, "records": rewritten}
                )
        return changed_count

    async def async_append(self, record: Mapping[str, Any]) -> bool:
        job_id = str(record.get("job_id") or "")
        if not job_id or self.contains_job(job_id):
            return False
        month = self.month_for_record(record)
        records = await self._async_load_month(month)
        records.append(dict(record))
        await self._chunk_store(month).async_save(
            {"schema_version": STATISTICS_SCHEMA_VERSION, "month": month, "records": records}
        )
        if month not in self._months:
            self._months.append(month)
            self._months.sort()
        self._job_month[job_id] = month
        await self._async_save_index()
        return True

    async def async_records(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        result: list[dict[str, Any]] = []
        for month in self._months:
            for record in await self._async_load_month(month):
                if record_matches(record, filters):
                    result.append(dict(record))
        result.sort(key=lambda item: str(item.get("planned_start") or item.get("finished_at") or ""), reverse=True)
        return result

    async def async_rebuild_cache(self) -> dict[str, Any]:
        records = await self.async_records()
        aggregate = aggregate_records(records)
        self.aggregate_cache = aggregate
        await self._cache_store.async_save(
            {
                "schema_version": STATISTICS_SCHEMA_VERSION,
                "rebuilt_at": datetime.now().astimezone().isoformat(),
                "aggregate": aggregate,
            }
        )
        return aggregate


    async def async_export_layer(self) -> dict[str, Any]:
        """Export the Statistics Ledger as one logical backup layer."""
        return {
            "schema_version": STATISTICS_SCHEMA_VERSION,
            "records": await self.async_records(),
            "applied_repairs": sorted(self._applied_repairs),
        }

    async def async_clear(self) -> int:
        """Remove every ledger row and rebuildable cache/index metadata."""
        count = len(self._job_month)
        months = list(self._months)
        for month in months:
            store = self._chunk_store(month)
            remover = getattr(store, "async_remove", None)
            if remover is not None:
                await remover()
            else:
                await store.async_save({"schema_version": STATISTICS_SCHEMA_VERSION, "month": month, "records": []})
        self._months = []
        self._job_month = {}
        self._loaded_chunks = {}
        self.aggregate_cache = {}
        self._applied_repairs = set()
        await self._async_save_index()
        await self._cache_store.async_save({
            "schema_version": STATISTICS_SCHEMA_VERSION,
            "rebuilt_at": datetime.now().astimezone().isoformat(),
            "aggregate": aggregate_records([]),
        })
        return count

    async def async_restore_layer(self, payload: Mapping[str, Any]) -> int:
        """Replace the whole Statistics Ledger from a validated backup layer."""
        await self.async_clear()
        self._applied_repairs = {str(value) for value in payload.get("applied_repairs", []) if value}
        count = 0
        for item in payload.get("records", []):
            if not isinstance(item, Mapping):
                continue
            if await self.async_append(dict(item)):
                count += 1
        await self._async_save_index()
        await self.async_rebuild_cache()
        return count

    async def async_query(self, filters: Mapping[str, Any] | None = None, *, limit: int = 200) -> dict[str, Any]:
        records = await self.async_records(filters)
        aggregate = aggregate_records(records)
        return {
            "schema_version": STATISTICS_SCHEMA_VERSION,
            "filters": dict(filters or {}),
            "aggregate": aggregate,
            "records": records[: max(0, min(1000, int(limit)))],
            "total_records": len(records),
            "months": list(self._months),
        }
