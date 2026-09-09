"""Administrative suppression and combined Recent Jobs filters for 0.11.9."""
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
from custom_components.vacuum_schedule.job import JobResult
from custom_components.vacuum_schedule.statistics_models import aggregate_records




def test_job_result_has_non_error_suppressed_terminal_value():
    assert JobResult.SUPPRESSED.value == "SUPPRESSED"


def test_scheduler_uses_variant_b_gate_suppression_and_never_returns_ready_work():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert "_execution_gate_suppression_reason" in source
    assert "job.origin is JobOrigin.SCHEDULED" in source
    assert "job.manual_release_at is None" in source
    assert "self._suppress_unstarted_zones(job, suppression_reason, now)" in source
    assert "self._finish(job, JobResult.SUPPRESSED, suppression_reason, now)" in source
    assert "return changed, ()" in source
    assert "ZoneResult.SKIPPED, reason, now" in source


def test_partial_progress_preserves_gate_reason_without_turning_it_into_failure():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    aggregate = source[source.index("def _aggregate_job_if_complete"):source.index("def _sync_job_state_from_zones")]
    assert "JobReason.GLOBAL_DISABLED.value" in aggregate
    assert "JobReason.GLOBAL_DISABLED_UNTIL.value" in aggregate
    assert "JobResult.PARTIAL_SUCCESS" in aggregate


def test_statistics_counts_suppressed_separately_and_as_scheduled_uncompleted():
    record = {
        "result": "SUPPRESSED",
        "origin": "SCHEDULED",
        "schedule_id": "schedule",
        "schedule_name": "Kitchen",
        "cleaning_params_snapshot": {"passes": 1},
        "zones": [],
        "time": {"wait": {}},
        "battery": {},
        "area": {},
        "water_usage": {},
    }
    result = aggregate_records([record])
    execution = result["execution"]
    assert execution["suppressed"] == 1
    assert execution["failed"] == 0
    assert execution["suppressed_rate"] == 100.0
    assert execution["failure_rate"] == 0.0
    assert execution["scheduled_uncompleted"] == 1
    assert execution["scheduled_uncompleted_rate"] == 100.0


def test_suppressed_real_job_does_not_train_resource_forecast():
    record = {
        "job_id": "suppressed-job",
        "execution_mode": "REAL",
        "result": "SUPPRESSED",
        "finished_at": "2026-08-31T09:00:00+03:00",
        "cleaning_params_snapshot": {"passes": 1},
        "zones": [],
        "time": {"physical_execution_seconds": 600},
        "battery": {"consumed_percent": 10},
    }
    assert compact_forecast_sample(record, "time") is None
    assert compact_forecast_sample(record, "battery") is None


def test_suppressed_notifications_are_attention_not_error_and_have_dedicated_semantics():
    manager = (MODULE / "notification_manager.py").read_text(encoding="utf-8")
    formatting = (MODULE / "notification_formatting.py").read_text(encoding="utf-8")
    assert "{JobResult.PARTIAL_SUCCESS, JobResult.SUPPRESSED}" in manager
    assert 'if result == "SUPPRESSED":\n            return "vacuum.job.suppressed"' in formatting
    assert 'notification.title.finished.suppressed' in formatting
    assert 'notification.compact.title.suppressed' in formatting


def test_recent_jobs_has_one_combined_filter_with_four_dimensions_and_active_chips():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert 'this._historyFilters = {execution_mode:"all", result:"all", source:"all", schedule_id:"all"}' in panel
    assert 'class="history-filter-popover"' in panel
    assert 'data-history-filter-group="${this._escape(group)}"' in panel
    assert 'option("schedule_id",id,name)' in panel
    assert 'class="history-active-filter"' in panel
    assert 'history-filter-reset' in panel
    assert 'this._historyFilterMatches("result",this._historyResultCategory(job))' in panel
    assert 'this._historyFilterMatches("source",this._historySource(job))' in panel


def test_history_source_distinguishes_scheduled_force_and_manual():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    source_fn = panel[panel.index("_historySource(job)"):panel.index("_historyResultCategory(job)")]
    assert 'return "MANUAL"' in source_fn
    assert 'return "FORCE"' in source_fn
    assert 'return "SCHEDULED"' in source_fn
    assert 'panel.source_force' in source_fn


def test_suppressed_result_is_visually_non_error():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert 'normalized === "SUPPRESSED"' in panel
    assert '? "warning"' in panel
    assert '.quiet-status-warning .quiet-status-dot { background:var(--warning-color' in panel
    assert '.state-FINISHED-SUPPRESSED { background:var(--vs-warning-quiet-fill)' in panel


