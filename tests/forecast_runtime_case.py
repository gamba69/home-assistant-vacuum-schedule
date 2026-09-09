"""Isolated HA harness for the 0.12.33 forecast delivery integration test."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types

ROOT = Path(sys.argv[1])
MOD = ROOT / "custom_components" / "vacuum_schedule"
cc = types.ModuleType("custom_components"); cc.__path__ = [str(ROOT / "custom_components")]
pkg = types.ModuleType("custom_components.vacuum_schedule"); pkg.__path__ = [str(MOD)]
sys.modules.update({"custom_components": cc, "custom_components.vacuum_schedule": pkg})
for name in ("homeassistant", "homeassistant.const", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.device_registry", "homeassistant.helpers.entity_registry", "homeassistant.helpers.storage"):
    module = types.ModuleType(name); module.__path__ = []
    sys.modules[name] = module
const = sys.modules["homeassistant.const"]
const.STATE_HOME = "home"; const.STATE_UNKNOWN = "unknown"; const.STATE_UNAVAILABLE = "unavailable"
sys.modules["homeassistant.core"].HomeAssistant = object


class Store:
    data = {}
    def __init__(self, *args): self.key = args[-1]
    async def async_load(self): return deepcopy(self.data.get(self.key, {}))
    async def async_save(self, data): self.data[self.key] = deepcopy(data)


sys.modules["homeassistant.helpers.storage"].Store = Store
from custom_components.vacuum_schedule.notification_manager import NotificationManager
from custom_components.vacuum_schedule.notification_models import NotificationEventType
from custom_components.vacuum_schedule.notification_store import NotificationEventRecord
from custom_components.vacuum_schedule.notification_formatting import render_compact
from custom_components.vacuum_schedule.const import CONF_NOTIFICATION_SETTINGS
from custom_components.vacuum_schedule.job import JobInstance, JobState, ZoneJobState
from custom_components.vacuum_schedule.execution_models import ExecutionMode
from custom_components.vacuum_schedule.schedule import Occurrence

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


class Services:
    def __init__(self): self.calls = []
    def has_service(self, *args): return True
    async def async_call(self, domain, service, data, **kwargs):
        # Yield once so concurrent notification processing can exercise the
        # pending-vs-sent semantic coalescing boundary.
        await asyncio.sleep(0)
        self.calls.append((service, data))


def make_job(key):
    occurrence = Occurrence(occurrence_id=key, schedule_id="schedule", schedule_revision=1,
        schedule_name="Кухня", planned_start=NOW + timedelta(minutes=15), warning_at=NOW,
        deadline_at=NOW + timedelta(hours=2), next_planned_start=NOW + timedelta(days=1),
        target_type="cleaning_zones", targets=("kitchen",), cleaning_params={})
    job = JobInstance.from_occurrence(occurrence, NOW)
    job.advisory_checked_at = NOW
    job.advisory_blockers = ("clean_water_insufficient",)
    job.current_blockers = job.advisory_blockers
    job.current_preflight_decision = "WAIT"
    return job


async def main():
    services = Services()
    presence = types.SimpleNamespace(state="home")
    hass = types.SimpleNamespace(services=services, config=types.SimpleNamespace(language="ru", time_zone="UTC"),
        states=types.SimpleNamespace(get=lambda entity: presence))
    def channel(key, event_filter="all", custom_events=(), presence_policy="always"):
        return dict(channel_id=key, name=key, target="notify." + key, transport_type="generic_notify",
            event_filter=event_filter, custom_events=list(custom_events), presence_policy=presence_policy)
    policy = dict(prewarning_mode="always", wait_enter_mode="off", wait_reminder_enabled=False,
        start_mode="always", finish_mode="always", start_forecast_mode="important",
        start_forecast_interval_seconds=60, start_forecast_deadline_minutes=10)
    settings = dict(enabled=True, policy=policy, recipients=[dict(recipient_id="me", name="Me", language="ru",
        presence_entity_id="person.me", channels=[channel("all"), channel("forecast", "custom", ("start_forecast",)),
            channel("errors", "error_only"), channel("home", presence_policy="home_only")])])
    entry = types.SimpleNamespace(entry_id="forecast-test", options={CONF_NOTIFICATION_SETTINGS: settings})
    manager = NotificationManager(hass, entry)
    await manager.async_start()
    job = make_job("one")
    await manager.async_process_job(job, NOW)
    assert [s for s, _ in services.calls] == ["all", "home"], services.calls
    assert manager.next_due_at(job) == NOW + timedelta(minutes=2)
    await manager.async_process_job(job, NOW + timedelta(minutes=2))
    assert [s for s, _ in services.calls].count("all") == 1
    assert [s for s, _ in services.calls].count("forecast") == 1
    assert not any(s == "errors" for s, _ in services.calls)

    # Reload the actual persisted outbox and transition candidate; do not replay.
    manager = NotificationManager(hass, entry)
    await manager.async_start()
    count = len(services.calls)
    await manager.async_process_job(job, NOW + timedelta(minutes=3))
    assert len(services.calls) == count
    presence.state = "not_home"
    job.current_blockers = (); job.current_preflight_decision = "PASS"
    await manager.async_process_job(job, NOW + timedelta(minutes=4))
    await manager.async_process_job(job, NOW + timedelta(minutes=6))
    assert [s for s, _ in services.calls[-2:]] == ["all", "forecast"], services.calls
    assert all(data["message"] == "Готово к запуску" for _, data in services.calls[-2:])
    records = [r for r in manager.store.events.values() if r.forecast_kind == "ready"]
    restored = NotificationEventRecord.from_dict(records[-1].to_dict()).to_event()
    assert restored.forecast_kind == "ready" and restored.compact_v2
    assert render_compact(restored, "ru").message == "Готово к запуску"

    # A start immediately after the warning needs one actual start per route.
    other = make_job("two")
    await manager.async_process_job(other, NOW + timedelta(minutes=7))
    await manager.async_process_job(other, NOW + timedelta(minutes=9))
    count = len(services.calls)
    other.actual_start = NOW + timedelta(minutes=10); other.state = JobState.RUNNING
    other.current_blockers = (); other.current_preflight_decision = "PASS"
    for zone in other.zone_runs.values():
        zone.actual_start = other.actual_start; zone.state = ZoneJobState.RUNNING
    await manager.async_process_job(other, other.actual_start)
    new = services.calls[count:]
    assert [s for s, _ in new] == ["all", "forecast"], new
    assert all(data["message"] == "Уборка началась" for _, data in new)
    await manager.async_process_job(other, other.actual_start + timedelta(seconds=1))
    assert len(services.calls) == count + 2

    # Concurrent observations of the same waited start must not send both the
    # normal STARTED event ("Started after waiting") and the forecast closure
    # ("Cleaning started") to the same all-events route.  They are distinct
    # technical event families but one user-visible semantic transition.
    race = make_job("start-race")
    await manager.async_process_job(race, NOW + timedelta(minutes=11))
    race.wait_cycle = 1
    race.actual_start = NOW + timedelta(minutes=12); race.state = JobState.RUNNING
    race.current_blockers = (); race.current_preflight_decision = "PASS"
    for zone in race.zone_runs.values():
        zone.actual_start = race.actual_start; zone.state = ZoneJobState.RUNNING
    count = len(services.calls)
    await asyncio.gather(
        manager.async_process_job(race, race.actual_start),
        manager.async_process_job(race, race.actual_start),
    )
    new = services.calls[count:]
    all_messages = [data["message"] for service, data in new if service == "all"]
    assert all_messages == ["Началась после ожидания"], new
    # No second start-like push may reach the all-events route. Forecast-only
    # delivery is covered separately by the normal sequential case above.

    # Deadline and WAIT on the same tick are one message for ALL, one forecast
    # for the forecast-only route; filtering cannot swallow the latter.
    policy["wait_enter_mode"] = "immediate"
    closing = make_job("three")
    closing.state = JobState.WAIT
    closing.first_wait_at = closing.deadline_at - timedelta(minutes=10)
    closing.wait_cycle = 1
    closing.current_blockers = ("zone_busy",)
    for zone in closing.zone_runs.values():
        zone.transition(ZoneJobState.WAIT, closing.first_wait_at)
        zone.blockers = closing.current_blockers
    count = len(services.calls)
    await manager.async_process_job(closing, closing.first_wait_at)
    new = services.calls[count:]
    assert [s for s, _ in new] == ["all", "forecast"], new
    assert "10 мин" in new[0][1]["message"] and "10 мин" in new[1][1]["message"]
    assert manager.next_due_at(closing) is None
    await manager.async_process_job(closing, closing.first_wait_at + timedelta(minutes=1))
    assert len(services.calls) == count + 2

    # Persist forecast state and remove it on simulation reset.
    await manager.store.async_save()
    manager.store.clear_simulation_jobs({closing.job_id})
    assert closing.job_id not in manager.store.start_forecasts
    assert manager.store.get_event(records[-1].event_id) is not None

    # A recent prewarning and WAIT about the same blocker coalesce as well.
    soon = make_job("soon")
    soon.planned_start = NOW + timedelta(minutes=1)
    await manager.async_process_job(soon, NOW)
    count = len(services.calls)
    soon.state = JobState.WAIT; soon.first_wait_at = soon.planned_start; soon.wait_cycle = 1
    for zone in soon.zone_runs.values():
        zone.transition(ZoneJobState.WAIT, soon.first_wait_at)
        zone.blockers = soon.current_blockers
    await manager.async_process_job(soon, soon.first_wait_at)
    assert len(services.calls) == count

    settings["dry_run_delivery"] = "log_only"
    simulated = make_job("simulation")
    simulated.execution_mode = ExecutionMode.DRY_RUN
    await manager.async_process_job(simulated, NOW)
    await manager.async_process_job(simulated, NOW + timedelta(minutes=2))
    assert len(services.calls) == count
    assert any(d.suppression_reason == "dry_run_delivery_disabled" for d in manager.store.deliveries.values())
    settings["dry_run_delivery"] = "send"

    # Master switch and per-schedule disable override forecast delivery.
    settings["enabled"] = False
    count = len(services.calls)
    await manager.async_process_job(make_job("disabled"), NOW)
    assert len(services.calls) == count
    settings["enabled"] = True
    disabled = make_job("schedule-disabled")
    disabled.metadata["notification_policy_snapshot"] = {"mode": "disabled"}
    await manager.async_process_job(disabled, NOW)
    await manager.async_process_job(disabled, NOW + timedelta(minutes=2))
    assert len(services.calls) == count
    print("forecast runtime: passed")


asyncio.run(main())
