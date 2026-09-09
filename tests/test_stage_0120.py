"""External physical execution ingestion and water protection for 0.12.1."""
from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))
pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)

from custom_components.vacuum_schedule.forecast_models import compact_forecast_sample
from custom_components.vacuum_schedule.job import JobOrigin




def test_external_origin_is_distinct_from_scheduler_manual():
    assert JobOrigin.EXTERNAL.value == "EXTERNAL"
    assert JobOrigin.MANUAL.value == "MANUAL"


def test_external_tracker_is_observation_only_restart_persistent_and_floor_gated():
    source = (MODULE / "external_execution.py").read_text(encoding="utf-8")
    assert "class ExternalExecutionTracker" in source
    assert "RobotExecutionPhase.CLEANING" in source
    assert 'status in {"mapping", "remote_control_active", "manual_mode", "going_to_target", "relocating"}' in source
    assert 'f"vacuum_schedule.{self.entry_id}.external_execution"' in source
    assert "scheduler_owned" in source
    assert "ExecutionMode.REAL" in source
    assert "JobOrigin.EXTERNAL" in source
    assert "statistics_observations" in source
    assert "forecast_eligible\": False" in source


def test_unresolved_external_run_never_trains_any_forecast_metric():
    record = {
        "job_id": "external-unknown-water",
        "origin": "EXTERNAL",
        "execution_mode": "REAL",
        "forecast_eligible": False,
        "result": "SUCCESS",
        "finished_at": "2026-08-31T08:20:00+00:00",
        "cleaning_params_snapshot": {},
        "zones": [],
        "time": {"physical_execution_seconds": 600.0},
        "battery": {"consumed_percent": 10.0},
    }
    assert compact_forecast_sample(record, "time") is None
    assert compact_forecast_sample(record, "battery") is None
    assert compact_forecast_sample(
        record, "clean_water", water_usage={"available": True, "clean_used_ml_eq": 100}
    ) is None
    assert compact_forecast_sample(
        record, "dirty_water", water_usage={"available": True, "dirty_gained_ml_eq": 100}
    ) is None


def test_water_pipeline_records_external_uncertainty_and_invalidates_calibration_cycle():
    source = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    record_block = source[source.index("def _record_job_usage"):source.index("async def async_record_job")]
    replay_block = source[source.index("def _apply_uncertainty_replay"):source.index("def _rebuild_from_ledger")]
    assert 'str(record.get("origin") or "").upper() == "EXTERNAL"' in record_block
    assert 'external.get("water_uncertain_clean_floor")' in record_block
    assert 'self._append_uncertainty(' in record_block
    assert '"event_type": "WATER_UNCERTAINTY"' in source
    assert '"known": False' in replay_block
    assert '"cycle_start_anchor": None' in replay_block
    assert '"usage_since_anchor_ml_eq": 0.0' in replay_block
    assert '"last_anchor": "external_usage_uncertain"' in replay_block
    assert 'event_type == "WATER_UNCERTAINTY"' in source


def test_history_and_statistics_ui_offer_external_source_filter():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    source_fn = panel[panel.index("_historySource(job)"):panel.index("_historyResultCategory(job)")]
    assert 'return "EXTERNAL"' in source_fn
    assert 'option("source","EXTERNAL",this._tr("panel.source_external"))' in panel
    assert '<option value="EXTERNAL" ${f.execution_source==="EXTERNAL"?"selected":""}>${this._tr("panel.source_external")}</option>' in panel
    assert 'this._tr("panel.external_cleaning")' in panel
    assert 'this._tr("panel.external_water_partial")' in panel


def test_scheduler_observes_external_before_arbitration_and_does_not_notify_it():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    reconcile = source[source.index("async def async_reconcile"):source.index("async def async_recheck_jobs")]
    assert "changed |= await self._async_observe_external_execution(now)" in reconcile
    assert reconcile.index("_async_observe_external_execution(now)") < reconcile.index("_prepare_job(job, now)")
    assert "job.origin is not JobOrigin.EXTERNAL" in reconcile
    assert "attempt.execution_mode is ExecutionMode.REAL and not attempt.terminal" in source


def test_external_statistics_are_saved_but_marked_not_forecast_eligible_by_default():
    source = (MODULE / "statistics_models.py").read_text(encoding="utf-8")
    assert '"external_execution": external_meta or None' in source
    assert '"forecast_eligible": bool(external_meta.get("forecast_eligible"))' in source
    assert '"external_observed_0.12"' in source


