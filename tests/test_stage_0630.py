"""Regression tests for wait timer is per continuous wait cycle and persists, prewarning contains cleaning params advisory and current readiness, and deviation only detects wait history and partial zone start.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobInstance, JobState  # noqa: E402
from schedule import Occurrence  # noqa: E402
from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    render_plain,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402

MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
ENGINE = MODULE / "scheduler_engine.py"
MANAGER = MODULE / "notification_manager.py"
INIT = MODULE / "__init__.py"
FORMATTING = MODULE / "notification_formatting.py"


def _job(now: datetime) -> JobInstance:
    occurrence = Occurrence(
        occurrence_id="occ-0630",
        schedule_id="schedule-0630",
        schedule_revision=1,
        schedule_name="Утро",
        planned_start=now + timedelta(minutes=10),
        warning_at=now,
        deadline_at=now + timedelta(hours=1),
        next_planned_start=now + timedelta(days=1),
        target_type="cleaning_zones",
        targets=("kitchen",),
        cleaning_params={"cleaning_mode": "vac_and_mop", "fan_mode": "balanced", "passes": 2},
    )
    return JobInstance.from_occurrence(occurrence, now)




def test_wait_timer_is_per_continuous_wait_cycle_and_persists_cycle_number():
    now = datetime(2026, 8, 14, 10, 0)
    job = _job(now)
    first = now + timedelta(minutes=10)
    second = first + timedelta(minutes=5)
    job.transition(JobState.WAIT, first)
    assert job.first_wait_at == first
    assert job.wait_cycle == 1
    job.transition(JobState.PLANNED, first + timedelta(seconds=20))
    assert job.first_wait_at is None
    job.transition(JobState.WAIT, second)
    assert job.first_wait_at == second
    assert job.wait_cycle == 2
    restored = JobInstance.from_dict(job.to_dict())
    assert restored.first_wait_at == second
    assert restored.wait_cycle == 2
    manager = MANAGER.read_text(encoding="utf-8")
    assert "def _wait_context(" in manager
    assert "run.state is ZoneJobState.WAIT" in manager
    assert "getattr(run, 'wait_cycle', 0)" in manager
    assert 'signature = context[0] if context is not None else "no-wait"' in manager


def test_prewarning_contains_cleaning_params_advisory_and_current_readiness():
    now = datetime(2026, 8, 14, 9, 0)
    event = SemanticNotificationEvent(
        event_id="job:prewarning",
        semantic_type="vacuum.job.preflight_blocked",
        event_type=NotificationEventType.PREWARNING,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=now,
        schedule_name="Утро",
        zones=("Кухня",),
        planned_at=now + timedelta(minutes=10),
        deadline_at=now + timedelta(hours=1),
        advisory_decision="WAIT",
        advisory_blockers=("zone_busy",),
        current_decision="PASS",
        current_blockers=(),
        cleaning_params={"cleaning_mode":"vac_and_mop","fan_mode":"balanced","passes":2,"water_mode":"standard"},
        dry_run=True,
    )
    rendered = render_plain(event, "ru")
    assert "Параметры уборки:" in rendered.message
    assert "Пылесос + мойка" in rendered.message
    assert "Сбалансированный" in rendered.message
    assert "Проходы: 2" in rendered.message
    assert "Предварительная проверка: Нужно подождать" in rendered.message
    assert "Препятствия при проверке:" in rendered.message
    assert "Состояние сейчас: Готово к запуску" in rendered.message
    assert "Препятствия сейчас: нет" in rendered.message


def test_deviation_only_detects_wait_history_and_partial_zone_start():
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'int(getattr(job, "wait_cycle", 0)) > 0' in manager
    assert "if any(run.actual_start is None for run in job.zone_runs.values()):" in manager
    assert "partial/deviating start" in manager


def test_panel_and_ha_services_share_scheduler_action_layer():
    engine = ENGINE.read_text(encoding="utf-8")
    frontend = FRONTEND.read_text(encoding="utf-8")
    init = INIT.read_text(encoding="utf-8")
    for token in ("async_set_schedule_enabled", "async_set_execution_gate", "async_update_preflight_policy"):
        assert token in engine
    assert "await scheduler.async_set_schedule_enabled(" in frontend
    assert "await scheduler.async_update_preflight_policy(" in frontend
    assert "await scheduler.async_set_schedule_enabled(" in init
    assert "await scheduler.async_set_execution_gate(" in init
    set_enabled = frontend[frontend.index("async def websocket_schedule_set_enabled"):frontend.index("async def websocket_debug_advance_time")]
    assert "async_reload" not in set_enabled


def test_notification_buttons_record_recipient_identity_and_audit_is_visible():
    job_source = (MODULE / "job.py").read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    engine = ENGINE.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert '"recipient_id": recipient_id' in job_source
    assert '"actor_name": actor_name' in job_source
    assert "parse_mobile_action" in manager
    assert "recipient_from_action_token" in manager
    assert "recipient_id=recipient_id" in engine
    assert '_jobAuditHtml(job)' in panel
    assert 'this._tr("panel.user_actions")' in panel
    assert "actor_display" in panel


def test_notify_entities_do_not_advertise_unsupported_mobile_or_pushover_rich_features():
    manager = MANAGER.read_text(encoding="utf-8")
    assert "provider_neutral_entity = True" in manager
    assert "transport_type=TransportType.GENERIC_NOTIFY" in manager
    assert "HA notify entities expose only notify.send_message" in manager
    candidate_block = manager[manager.index("for state in self.hass.states.async_all()"):manager.index("return {\n            \"channels\"")]
    assert "if transport in {TransportType.MOBILE_APP, TransportType.PUSHOVER}:" in candidate_block
    assert "transport = TransportType.GENERIC_NOTIFY" in candidate_block


def test_disabled_until_requires_a_valid_datetime_for_frontend_and_ha_action():
    engine = ENGINE.read_text(encoding="utf-8")
    init = INIT.read_text(encoding="utf-8")
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'raise ValueError("disabled_until_required")' in engine
    assert 'raise ValueError("invalid_disabled_until")' in engine
    assert "datetime.fromisoformat(raw)" in engine
    assert 'translation_key=key' in init
    assert '_send_localizable_error(connection, msg["id"], "invalid_preflight_policy", str(err))' in frontend


def test_prewarning_policy_prefers_live_blockers_and_preserves_advisory_details():
    manager = MANAGER.read_text(encoding="utf-8")
    assert "return bool(job.current_blockers if job.current_preflight_decision else job.advisory_blockers)" in manager
    formatting = FORMATTING.read_text(encoding="utf-8")
    assert "notification.field.advisory_blockers" in formatting
    assert "notification.field.current_blockers" in formatting
