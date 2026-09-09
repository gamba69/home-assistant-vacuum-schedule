"""Regression tests for balanced policy is the accepted default, sparse schedule overrides only replace named fields, and one recipient can have pushover always and mobile away.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE_DIR))

from job import JobInstance, JobOrigin  # noqa: E402
from notification_models import (  # noqa: E402
    ChannelEventFilter,
    FinishMode,
    NotificationChannel,
    NotificationPolicy,
    NotificationPreset,
    NotificationRecipient,
    NotificationSettings,
    PresencePolicy,
    PrewarningMode,
    StartMode,
    TransportType,
    WaitEnterMode,
    normalize_notification_settings,
)
from planner import OccurrencePlanner  # noqa: E402
from schedule import ScheduleDefinition  # noqa: E402


class Stage060NotificationModelTests(unittest.TestCase):
    def test_balanced_policy_is_the_accepted_default(self) -> None:
        policy = NotificationPolicy.balanced()
        self.assertEqual(policy.prewarning_mode, PrewarningMode.BLOCKERS_ONLY)
        self.assertEqual(policy.wait_enter_mode, WaitEnterMode.DELAYED)
        self.assertEqual(policy.wait_delay_seconds, 120)
        self.assertTrue(policy.wait_reminder_enabled)
        self.assertEqual(policy.wait_reminder_interval_seconds, 1800)
        self.assertEqual(policy.wait_reminder_max_count, 3)
        self.assertEqual(policy.start_mode, StartMode.DEVIATION_ONLY)
        self.assertEqual(policy.finish_mode, FinishMode.INCOMPLETE_ONLY)

    def test_sparse_schedule_overrides_only_replace_named_fields(self) -> None:
        settings = NotificationSettings()
        resolved = settings.resolved_policy({
            "mode": "custom",
            "overrides": {"finish_mode": "always", "wait_reminder_max_count": 1},
        })
        self.assertEqual(resolved.finish_mode, FinishMode.ALWAYS)
        self.assertEqual(resolved.wait_reminder_max_count, 1)
        self.assertEqual(resolved.prewarning_mode, PrewarningMode.BLOCKERS_ONLY)
        self.assertEqual(resolved.wait_delay_seconds, 120)

    def test_one_recipient_can_have_pushover_always_and_mobile_away(self) -> None:
        settings = NotificationSettings(
            preset=NotificationPreset.BALANCED,
            recipients=(NotificationRecipient(
                recipient_id="igor",
                name="Igor",
                presence_entity_id="person.igor",
                channels=(
                    NotificationChannel(
                        channel_id="pushover",
                        name="Pushover",
                        transport_type=TransportType.PUSHOVER,
                        target="notify.pushover",
                        presence_policy=PresencePolicy.ALWAYS,
                    ),
                    NotificationChannel(
                        channel_id="iphone",
                        name="iPhone",
                        transport_type=TransportType.MOBILE_APP,
                        target="notify.mobile_app_igor",
                        presence_policy=PresencePolicy.AWAY_ONLY,
                        event_filter=ChannelEventFilter.ALL,
                        actionable=True,
                    ),
                ),
            ),),
        )
        restored = NotificationSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.to_dict(), settings.to_dict())
        self.assertEqual(restored.recipients[0].channels[0].presence_policy, PresencePolicy.ALWAYS)
        self.assertEqual(restored.recipients[0].channels[1].presence_policy, PresencePolicy.AWAY_ONLY)

    def test_pushover_emergency_requires_retry_and_expire(self) -> None:
        raw = {
            "recipients": [{
                "recipient_id": "igor", "name": "Igor",
                "channels": [{
                    "channel_id": "po", "name": "Pushover", "transport_type": "pushover",
                    "target": "notify.pushover", "target_kind": "service",
                    "options": {"priority": 2},
                }],
            }],
        }
        with self.assertRaisesRegex(ValueError, "pushover_emergency_retry_required"):
            normalize_notification_settings(raw)
        raw["recipients"][0]["channels"][0]["options"].update({"retry": 30, "expire": 300})
        normalized = normalize_notification_settings(raw)
        self.assertEqual(normalized["recipients"][0]["channels"][0]["options"]["priority"], 2)


class Stage060ScheduleAndJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedule = ScheduleDefinition.create(
            schedule_id="schedule-a",
            revision=4,
            name="Morning",
            enabled=True,
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            dates=[],
            local_time="09:00",
            targets=["kitchen"],
            notification_policy={"mode": "inherit"},
        )

    def test_name_and_notification_policy_do_not_change_execution_revision(self) -> None:
        renamed = self.schedule.revised(name="Morning clean")
        rerouted = self.schedule.revised(notification_policy={"mode": "custom", "overrides": {"finish_mode": "always"}})
        self.assertEqual(renamed.revision, 4)
        self.assertEqual(rerouted.revision, 4)

    def test_execution_change_still_increments_revision(self) -> None:
        changed = self.schedule.revised(local_time="10:00")
        self.assertEqual(changed.revision, 5)

    def test_manual_job_fields_roundtrip(self) -> None:
        zone = ZoneInfo("Europe/Kyiv")
        planner = OccurrencePlanner(zone)
        now = datetime(2026, 8, 13, 8, 0, tzinfo=zone)
        occurrence = planner.next_for_schedule(self.schedule, now)
        self.assertIsNotNone(occurrence)
        job = JobInstance.from_occurrence(occurrence, now)  # type: ignore[arg-type]
        job.origin = JobOrigin.MANUAL
        job.manual_triggered_at = now
        job.manual_release_at = now
        job.requested_by = "user-1"
        job.record_user_action("run_schedule_now", now, "frontend", "user-1")
        restored = JobInstance.from_dict(job.to_dict())
        self.assertEqual(restored.origin, JobOrigin.MANUAL)
        self.assertEqual(restored.manual_release_at, now)
        self.assertEqual(restored.user_action_history[-1]["source"], "frontend")


class Stage060ReleaseBoundaryTests(unittest.TestCase):

    def test_user_actions_and_notification_websockets_are_registered(self) -> None:
        frontend = (MODULE_DIR / "frontend.py").read_text(encoding="utf-8")
        init = (MODULE_DIR / "__init__.py").read_text(encoding="utf-8")
        for token in (
            "notifications/save",
            "notifications/test_channel",
            "notifications/test_event",
            "jobs/action",
            "schedules/run_now",
        ):
            self.assertIn(token, frontend)
        for service in (
            "SERVICE_RUN_SCHEDULE_NOW", "SERVICE_START_JOB_NOW", "SERVICE_SKIP_JOB",
            "SERVICE_CANCEL_JOB", "SERVICE_RECHECK_JOB", "SERVICE_SET_SCHEDULE_ENABLED",
            "SERVICE_SET_EXECUTION_GATE",
        ):
            self.assertIn(service, init)

    def test_physical_vacuum_execution_remains_out_of_scheduler(self) -> None:
        scheduler = (MODULE_DIR / "scheduler_engine.py").read_text(encoding="utf-8")
        for forbidden in ("vacuum.start", "vacuum.send_command", "executor.async_start", "executor.async_stop"):
            self.assertNotIn(forbidden, scheduler)


if __name__ == "__main__":
    unittest.main()
