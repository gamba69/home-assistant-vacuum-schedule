"""Acceptance contracts for Vacuum Schedule 0.7.x execution architecture."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from execution_models import ExecutionAttempt, ExecutionAttemptState, ExecutionMode  # noqa: E402
from job import JobInstance  # noqa: E402
from notification_models import NotificationSettings  # noqa: E402




def test_execution_mode_and_attempt_round_trip():
    now = datetime(2026, 8, 17, 10, 0)
    attempt = ExecutionAttempt.create(
        job_id="j", execution_mode=ExecutionMode.DRY_RUN,
        zone_ids=("a", "b"), target_type="segment", targets=("16",),
        cleaning_params={"passes": 2}, now=now,
    )
    restored = ExecutionAttempt.from_dict(attempt.to_dict())
    assert restored.execution_mode is ExecutionMode.DRY_RUN
    assert restored.state is ExecutionAttemptState.CREATED
    assert restored.zone_ids == ("a", "b")
    assert restored.targets == ("16",)


def test_dry_run_backend_has_no_physical_command_handle():
    source = (MODULE / "execution_backend.py").read_text()
    dry = source[source.index("class DryRunExecutionBackend"):source.index("class RealExecutionBackend")]
    assert "services.async_call" not in dry
    assert "self.executor" not in dry
    assert "self.hass" not in dry
    assert "START_REQUESTED" in dry and "RUNNING" in dry and "COMPLETED" in dry


def test_real_backend_is_only_physical_service_boundary():
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    manager = (MODULE / "execution_manager.py").read_text()
    backend = (MODULE / "execution_backend.py").read_text()
    assert "hass.services.async_call" not in scheduler
    assert "hass.services.async_call" not in manager
    real = backend[backend.index("class RealExecutionBackend"):]
    adapter = (MODULE / "execution_adapter.py").read_text()
    assert "VacuumExecutionAdapter" in real
    assert "services.async_call" in adapter
    assert "physical_command_rejected_non_real_attempt" in real
    assert "physical_command_rejected_test_clock_active" in real


def test_legacy_job_without_execution_mode_migrates_to_dry_run():
    raw = {
        "job_id":"j","occurrence_id":"o","schedule_id":"s","schedule_name":"S",
        "schedule_revision":1,"origin":"SCHEDULED","simulation":True,"state":"PLANNED",
        "target_type":"cleaning_zones","targets":[],"cleaning_params":{},
        "planned_start":"2026-08-17T10:00:00","warning_at":"2026-08-17T09:50:00",
        "deadline_at":"2026-08-17T11:00:00","created_at":"2026-08-17T09:50:00",
    }
    job = JobInstance.from_dict(raw)
    assert job.execution_mode is ExecutionMode.DRY_RUN
    assert job.to_dict()["execution_mode"] == "DRY_RUN"


def test_occurrence_lookup_is_execution_mode_aware_and_multiple_scheduled_jobs_are_allowed():
    store = (MODULE / "job_store.py").read_text()
    assert "execution_mode: ExecutionMode | None" in store
    assert "DRY_RUN never consumes the future REAL occurrence" in store
    assert "active_scheduled_job_exists" not in store


def test_materializer_keeps_one_future_placeholder_without_blocking_old_waits():
    engine = (MODULE / "scheduler_engine.py").read_text()
    section = engine[engine.index("def _materialize_missing"):engine.index("def _finish(")]
    assert "Keep one future placeholder while preserving independent current Jobs" in section
    assert "any(instant_gt(job.planned_start, now) for job in existing)" in section
    assert "if existing:" not in section
    assert "instant_lt(now, occurrence.warning_at)" not in section


def test_execution_plan_dedupes_shared_physical_target():
    manager = (MODULE / "execution_manager.py").read_text()
    assert "dict.fromkeys(zone.robot_target_id" in manager
    assert "zone_ids=tuple(zone.zone_id for zone in selected)" in manager
    assert "duplicate_robot_target" not in (MODULE / "preflight.py").read_text()
    assert "item.target_key == zone.target_key" not in (MODULE / "frontend.py").read_text()


def test_real_mode_disables_test_tools_and_overrides_at_runtime_boundaries():
    engine = (MODULE / "scheduler_engine.py").read_text()
    preflight = (MODULE / "preflight.py").read_text()
    assert 'if self.execution_mode is not ExecutionMode.DRY_RUN' in engine
    assert "allow_test_overrides=job.execution_mode is ExecutionMode.DRY_RUN" in preflight
    assert "self.clock.reset()" in engine


def test_dry_run_faults_use_normal_execution_reason_codes():
    backend = (MODULE / "execution_backend.py").read_text()
    job = (MODULE / "job.py").read_text()
    for code in ("execution_start_rejected", "execution_start_timeout", "execution_failed", "execution_lost"):
        assert code in backend
        assert code in job
    scheduler = (MODULE / "scheduler_engine.py").read_text()
    assert "async_simulate_completion" not in scheduler
    assert "async_simulate_failure" not in scheduler


def test_dry_run_notifications_support_send_or_log_only():
    default = NotificationSettings.from_dict({})
    assert default.dry_run_delivery == "send"
    log_only = NotificationSettings.from_dict({"dry_run_delivery":"log_only"})
    assert log_only.to_dict()["dry_run_delivery"] == "log_only"
    manager = (MODULE / "notification_manager.py").read_text()
    assert "dry_run_delivery_disabled" in manager
    assert "job.execution_mode is ExecutionMode.DRY_RUN" in manager


def test_removed_runtime_synthetic_and_manual_completion_endpoints():
    frontend = (MODULE / "frontend.py").read_text()
    panel = (MODULE / "frontend" / "panel.js").read_text()
    registrations = frontend[frontend.index("async def async_setup_frontend"):]
    assert "websocket_debug_set_blocker" not in registrations
    assert "websocket_debug_simulate_completion" not in registrations
    assert "websocket_debug_simulate_failure" not in registrations
    assert "vacuum_schedule/debug/set_blocker" not in panel
    assert "vacuum_schedule/debug/simulate_completion" not in panel
    assert "vacuum_schedule/debug/simulate_failure" not in panel


def test_lifecycle_job_and_delivery_history_mark_execution_mode():
    store = (MODULE / "job_store.py").read_text()
    notification_store = (MODULE / "notification_store.py").read_text()
    panel = (MODULE / "frontend" / "panel.js").read_text()
    assert '"execution_mode": job.execution_mode.value' in store
    assert 'clock_offset_seconds' in (MODULE / "scheduler_engine.py").read_text()
    assert "execution_mode: str" in notification_store
    assert "data-history-filter-group" in panel
    assert "history-filter-schedule-options" in panel
    assert "execution_attempts:job.execution_attempts" in panel
