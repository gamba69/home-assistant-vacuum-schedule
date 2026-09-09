"""Start forecast policy, transition, routing and compact presentation regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))
from start_forecast import forecast_candidate, readiness
from notification_models import NotificationPolicy, NotificationSettings, StartForecastMode
from notification_formatting import SemanticNotificationEvent, render_compact
from notification_models import NotificationClass, NotificationEventType

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def _job(codes=(), **kwargs):
    values = dict(terminal=False, actual_start=None, zone_runs={},
        warning_at=NOW, planned_start=NOW + timedelta(minutes=15),
        deadline_at=NOW + timedelta(hours=2), current_blockers=codes,
        current_preflight_decision="WAIT" if codes else "PASS")
    values.update(kwargs)
    return SimpleNamespace(**values)


def _policy(mode="important", interval=600, closing=10):
    return NotificationPolicy.from_dict(dict(start_forecast_mode=mode,
        start_forecast_interval_seconds=interval, start_forecast_deadline_minutes=closing))


def test_forecast_upgrade_disabled_and_sparse_schedule_override():
    assert NotificationPolicy.from_dict({}).start_forecast_mode is StartForecastMode.OFF
    settings = NotificationSettings.from_dict({"policy": _policy().to_dict()})
    policy = settings.resolved_policy({"mode": "custom", "overrides": {"start_forecast_interval_seconds": 180}})
    assert policy.start_forecast_mode is StartForecastMode.IMPORTANT
    assert policy.start_forecast_interval_seconds == 180
    assert settings.resolved_policy({"mode": "disabled"}).start_forecast_mode is StartForecastMode.OFF
    assert _policy(closing=0).start_forecast_deadline_minutes == 0


def test_readiness_is_read_only_and_does_not_warn_about_distant_jobs():
    job = _job(("clean_water_insufficient",))
    before = vars(job).copy()
    assert readiness(job, NOW - timedelta(seconds=1)) is None
    assert readiness(job, NOW)["status"] == "attention"
    assert vars(job) == before


def test_started_terminal_and_administrative_jobs_have_no_forecast_push():
    for kwargs in ({"actual_start": NOW}, {"terminal": True}, {"current_blockers": ("schedule_paused",)}):
        state = {}
        job = _job(**kwargs)
        assert forecast_candidate(job, NOW, _policy(), state) is None
        assert "due_at" not in state


def test_unknown_sensor_is_not_reported_as_empty_tank():
    assert readiness(_job(("dock_clean_water_unavailable",)), NOW)["status"] == "unknown"
    assert readiness(_job(current_preflight_decision=None), NOW)["status"] == "unknown"


def test_stable_problem_emits_once_after_two_minutes():
    state = {}; job = _job(("clean_water_insufficient",)); policy = _policy()
    assert forecast_candidate(job, NOW, policy, state) is None
    assert forecast_candidate(job, NOW + timedelta(seconds=119), policy, state) is None
    event = forecast_candidate(job, NOW + timedelta(seconds=120), policy, state)
    assert event["kind"] == "attention" and event["sequence"] == 1
    assert forecast_candidate(job, NOW + timedelta(minutes=30), policy, state) is None


def test_flicker_does_not_emit_obsolete_problem():
    state = {}; job = _job(("clean_water_insufficient",)); policy = _policy()
    forecast_candidate(job, NOW, policy, state)
    job.current_blockers = (); job.current_preflight_decision = "PASS"
    assert forecast_candidate(job, NOW + timedelta(seconds=30), policy, state) is None
    event = forecast_candidate(job, NOW + timedelta(seconds=150), policy, state)
    assert event["kind"] == "ready"
    # Delivery separately requires a previously sent warning; no false recovery push.


def test_cooldown_keeps_latest_state_and_survives_json_restart():
    state = {}; job = _job(("clean_water_insufficient",)); policy = _policy()
    forecast_candidate(job, NOW, policy, state)
    forecast_candidate(job, NOW + timedelta(minutes=2), policy, state)
    state = json.loads(json.dumps(state))
    job.current_blockers = ("dirty_water_full",)
    assert forecast_candidate(job, NOW + timedelta(minutes=3), policy, state) is None
    job.current_blockers = ("vacuum_error",)
    assert forecast_candidate(job, NOW + timedelta(minutes=6), policy, state) is None
    assert forecast_candidate(job, NOW + timedelta(minutes=11), policy, state) is None
    event = forecast_candidate(job, NOW + timedelta(minutes=12), policy, state)
    assert event["blockers"] == ["vacuum_error"] and event["sequence"] == 2


def test_important_ignores_temporary_blockers_but_all_announces_them():
    job = _job(("zone_busy",))
    for mode in ("off", "important", "all"):
        state = {}; policy = _policy(mode)
        forecast_candidate(job, NOW, policy, state)
        event = forecast_candidate(job, NOW + timedelta(minutes=2), policy, state)
        assert bool(event) == (mode == "all")


def test_busy_codes_collapse_without_generating_a_change():
    state = {}; job = _job(("vacuum_busy",)); policy = _policy("all")
    forecast_candidate(job, NOW, policy, state)
    forecast_candidate(job, NOW + timedelta(minutes=2), policy, state)
    job.current_blockers = ("execution_lease_busy", "vacuum_busy")
    assert forecast_candidate(job, NOW + timedelta(minutes=20), policy, state) is None


def test_important_reports_partial_recovery_from_actionable_problem():
    state = {}; job = _job(("clean_water_insufficient", "zone_busy")); policy = _policy(interval=60)
    forecast_candidate(job, NOW, policy, state)
    forecast_candidate(job, NOW + timedelta(minutes=2), policy, state)
    job.current_blockers = ("zone_busy",)
    forecast_candidate(job, NOW + timedelta(minutes=3), policy, state)
    event = forecast_candidate(job, NOW + timedelta(minutes=5), policy, state)
    assert event["kind"] == "improved"
    assert forecast_candidate(job, NOW + timedelta(minutes=10), policy, state) is None


def test_closing_warning_once_and_no_message_after_deadline():
    state = {}; job = _job(("zone_busy",)); policy = _policy()
    now = job.deadline_at - timedelta(minutes=10)
    event = forecast_candidate(job, now, policy, state)
    assert event["kind"] == "closing"
    assert forecast_candidate(job, now + timedelta(minutes=1), policy, state) is None
    assert forecast_candidate(job, job.deadline_at, policy, state) is None


def test_closing_disabled_and_cooldown_cannot_outlive_window():
    job = _job(("zone_busy",))
    assert forecast_candidate(job, job.deadline_at - timedelta(minutes=5), _policy(closing=0), {}) is None
    state = {"last_at": (job.deadline_at - timedelta(minutes=1)).isoformat()}
    assert forecast_candidate(job, job.deadline_at - timedelta(seconds=30), _policy(), state) is None
    assert "due_at" not in state


def _event(kind="attention", codes=("clean_water_insufficient",)):
    return SemanticNotificationEvent(event_id="job:forecast:1", job_id="job", schedule_name="Кухня",
        semantic_type="vacuum.job.start_forecast", event_type=NotificationEventType.START_FORECAST,
        notification_class=NotificationClass.ATTENTION, severity="warning", created_at=NOW,
        planned_at=NOW + timedelta(minutes=15), deadline_at=NOW + timedelta(minutes=10),
        blockers=codes, forecast_kind=kind, compact_v2=True)


def test_short_localized_message_has_one_body_line_and_no_duplicate_name():
    ru = render_compact(_event(), "ru")
    en = render_compact(_event(), "en")
    assert ru.title == "Кухня · 12:15" and ru.message == "Долейте воду"
    assert en.message == "Add clean water"
    for item in (ru, en):
        assert "\n" not in item.message and len(item.message) < 40
        assert "Кухня" not in item.message


def test_forecast_estimate_is_not_presented_as_measured_fact():
    msg = render_compact(_event(codes=("forecast_clean_water_insufficient",)), "ru").message
    assert msg == "Воды может не хватить"
    assert render_compact(_event("closing"), "ru").message == "На запуск — 10 мин"


def test_short_lifecycle_and_complete_job_duration():
    started = replace(_event(), event_type=NotificationEventType.STARTED, forecast_kind=None,
        blockers=(), start_source="early", dry_run=True)
    assert render_compact(started, "ru").message == "Началась досрочно"
    assert render_compact(started, "ru").title.startswith("Dry-Run · ")
    done = replace(started, event_type=NotificationEventType.FINISHED, result="SUCCESS", duration_seconds=1440)
    assert render_compact(done, "ru").message == "Выполнено · 24 мин"


def test_current_ready_does_not_reuse_old_advisory_water_problem():
    event = replace(_event(), event_type=NotificationEventType.PREWARNING,
        forecast_kind=None, blockers=(), current_blockers=(), current_decision="PASS",
        advisory_blockers=("clean_water_insufficient",))
    assert render_compact(event, "ru").message == "Готово к запуску"


def test_ui_category_global_override_filter_and_websocket_are_wired():
    panel = (MODULE / "frontend/panel.js").read_text()
    backend = (MODULE / "frontend.py").read_text()
    for field in ("start_forecast_mode", "start_forecast_interval_seconds", "start_forecast_deadline_minutes"):
        assert panel.count(field) >= 3
    assert '"finished","start_forecast"' in panel
    assert '"finished", "start_forecast", "test"' in backend
    for language in ("ru", "en"):
        strings = json.loads((MODULE / f"frontend/localization/{language}.json").read_text())
        assert strings["panel.start_forecast"]


def test_runtime_routing_persistence_timers_and_start_coalescing():
    """Exercise the real manager/outbox against isolated HA service stubs."""
    script = (ROOT / "tests" / "forecast_runtime_case.py").read_text()
    result = subprocess.run([sys.executable, "-c", script, str(ROOT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
