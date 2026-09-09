"""Regression contracts for Vacuum Schedule 0.8.12 zone batching and occupancy override."""

from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobInstance  # noqa: E402
from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


def _schedule(**changes):
    payload = dict(
        name="Multi-zone",
        enabled=True,
        weekdays=(0,),
        dates=(),
        local_time="10:00",
        targets=("z1", "z2"),
        cleaning_params={"passes": 1},
    )
    payload.update(changes)
    return ScheduleDefinition.create(**payload)




def test_new_schedules_default_to_combined_but_legacy_schedules_remain_progressive():
    current = _schedule()
    assert current.zone_execution_policy == "combined"

    legacy = current.to_dict()
    legacy.pop("zone_execution_policy")
    restored = ScheduleDefinition.from_dict(legacy)
    assert restored.zone_execution_policy == "progressive"


def test_zone_execution_policy_is_revisioned_execution_intent():
    current = _schedule(zone_execution_policy="combined")
    revised = current.revised(zone_execution_policy="progressive")
    assert revised.revision == current.revision + 1
    assert revised.zone_execution_policy == "progressive"


def test_occurrence_and_job_snapshot_zone_execution_policy():
    schedule = _schedule(zone_execution_policy="combined")
    planner = OccurrencePlanner("UTC")
    occurrence = planner.next_occurrence(
        [schedule], datetime(2026, 8, 17, 0, 0).astimezone(), inclusive=True
    )
    assert occurrence is not None
    assert occurrence.zone_execution_policy == "combined"
    job = JobInstance.from_occurrence(occurrence, occurrence.warning_at)
    assert job.metadata["zone_execution_policy"] == "combined"


def test_combined_policy_waits_for_every_zone_before_starting_ready_batch():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'if self._zone_execution_policy(job) == ZONE_EXECUTION_POLICY_COMBINED:' in engine
    assert 'if any(zone.zone_id not in ready_ids for zone in active_unstarted):' in engine
    assert 'ready = []' in engine
    assert 'failed_zones and not has_started' in engine


def test_combined_policy_rejects_mixed_physical_target_types():
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'combined_execution_mixed_target_types' in frontend
    assert 'len(active_target_types) > 1' in engine


def test_manual_occupancy_override_is_server_derived_and_job_scoped():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    assert 'ignore_busy_zones: bool = False' in engine
    assert 'if "zone_busy" in report.blocker_codes:' in engine
    assert 'overrides["ignore_busy_zones"] = sorted(existing)' in engine
    assert '"manual_occupancy_override"' in engine
    assert 'start_job_now_ignore_busy' in engine


def test_preflight_override_suppresses_only_zone_busy():
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    assert 'item.code != "zone_busy"' in preflight
    assert 'unknown presence, access, DND, resources and every other' in preflight


def test_frontend_offers_occupancy_override_only_for_current_busy_zones():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert '(zone?.blockers||[]).includes("zone_busy")' in panel
    assert 'action="start_now_ignore_busy"' in panel
    assert 'panel.start_ignoring_occupancy_confirm' in panel
    assert 'data-field="zone_execution_policy"' in panel
    assert 'panel.zone_execution_combined' in panel
    assert 'panel.zone_execution_progressive' in panel


def test_085_localization_is_complete_for_both_languages():
    required = {
        "panel.zone_execution_policy",
        "panel.zone_execution_combined",
        "panel.zone_execution_progressive",
        "panel.help.zone_execution_policy",
        "panel.occupied_zones",
        "panel.start_ignoring_occupancy",
        "panel.start_ignoring_occupancy_confirm",
        "panel.manual_occupancy_override",
        "notification.field.occupancy_override",
        "error.combined_execution_mixed_target_types",
    }
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert required <= set(data)


def test_manual_override_behaviorally_filters_busy_but_not_access_blocker():
    from zoneinfo import ZoneInfo
    sys.path.insert(0, str(ROOT / "tests"))
    from test_stage_050 import FakeProvider  # noqa: E402
    from preflight import PreflightEngine, SimulationPreflightProvider  # noqa: E402
    from preflight_models import PreflightPhase  # noqa: E402

    tz = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 8, 18, 12, 0, tzinfo=tz)
    schedule = ScheduleDefinition.create(
        schedule_id="override",
        revision=1,
        name="Override",
        enabled=True,
        weekdays=(0, 1, 2, 3, 4, 5, 6),
        dates=(),
        local_time="12:00",
        targets=("kitchen", "hall"),
        zone_execution_policy="combined",
    )
    occurrence = OccurrencePlanner(tz).next_for_schedule(schedule, now, inclusive=True)
    assert occurrence is not None
    job = JobInstance.from_occurrence(occurrence, now)
    provider = FakeProvider(now)
    provider.zone_snapshots["kitchen"].busy.effective_value = True
    provider.zone_snapshots["kitchen"].accessible.effective_value = False
    engine = PreflightEngine(provider, SimulationPreflightProvider())

    before = engine.evaluate_zone(job, "kitchen", PreflightPhase.AUTHORITATIVE, now)
    assert "zone_busy" in before.blocker_codes
    assert "zone_access_blocked" in before.blocker_codes

    job.metadata["manual_overrides"] = {"ignore_busy_zones": ["kitchen"]}
    after = engine.evaluate_zone(job, "kitchen", PreflightPhase.AUTHORITATIVE, now)
    assert "zone_busy" not in after.blocker_codes
    assert "zone_access_blocked" in after.blocker_codes



def test_started_notification_surfaces_occupancy_override_zones():
    from notification_formatting import (  # noqa: E402
        SemanticNotificationEvent,
        SemanticZoneSnapshot,
        render_plain,
    )
    from notification_models import NotificationClass, NotificationEventType  # noqa: E402

    now = datetime(2026, 8, 18, 12, 0)
    event = SemanticNotificationEvent(
        event_id="override-started",
        semantic_type="started",
        event_type=NotificationEventType.STARTED,
        notification_class=NotificationClass.INFO,
        severity="info",
        created_at=now,
        schedule_name="Multi-zone",
        zone_snapshots=(
            SemanticZoneSnapshot(
                zone_id="kitchen",
                name="Кухня",
                state="RUNNING",
                actual_start=now,
            ),
        ),
        occupancy_override_zones=("Кухня", "Гостиная"),
    )
    rendered = render_plain(event, "ru")
    assert "Кухня, Гостиная" in rendered.message
    assert "занятост" in rendered.message.lower()
