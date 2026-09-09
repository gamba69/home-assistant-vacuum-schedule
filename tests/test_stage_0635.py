"""Regression tests for zone wait cycle is durable and reentry creates a, started notification reports actual multizone snapshot not all targets, and wait notification is valid while another zone is running.

Covers retained behavioral contracts from earlier development stages.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from job import JobInstance, ZoneExecution, ZoneJobState  # noqa: E402
from notification_formatting import (  # noqa: E402
    SemanticNotificationEvent,
    SemanticZoneSnapshot,
    render_plain,
)
from notification_models import NotificationClass, NotificationEventType  # noqa: E402
from schedule import Occurrence  # noqa: E402

MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANAGER = MODULE / "notification_manager.py"
STORE = MODULE / "notification_store.py"
ENGINE = MODULE / "scheduler_engine.py"
EN = MODULE / "frontend" / "localization" / "en.json"
RU = MODULE / "frontend" / "localization" / "ru.json"


def _job(now: datetime) -> JobInstance:
    occurrence = Occurrence(
        occurrence_id="occ-0635",
        schedule_id="schedule-0635",
        schedule_revision=1,
        schedule_name="Утро",
        planned_start=now,
        warning_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(hours=1),
        next_planned_start=now + timedelta(days=1),
        target_type="cleaning_zones",
        targets=("kitchen", "bedroom"),
        cleaning_params={},
    )
    return JobInstance.from_occurrence(occurrence, now)


def _event(kind: NotificationEventType, snapshots: tuple[SemanticZoneSnapshot, ...], **extra):
    payload = dict(
        event_id=f"job:{kind.value}",
        semantic_type=f"vacuum.job.{kind.value}",
        event_type=kind,
        notification_class=NotificationClass.ATTENTION,
        severity="warning",
        created_at=datetime(2026, 8, 17, 9, 0),
        job_id="job-0635",
        schedule_name="Утро",
        zones=tuple(item.name for item in snapshots),
        zone_snapshots=snapshots,
        dry_run=True,
    )
    payload.update(extra)
    return SemanticNotificationEvent(**payload)




def test_zone_wait_cycle_is_durable_and_reentry_creates_a_new_episode():
    now = datetime(2026, 8, 17, 9, 0)
    zone = ZoneExecution(zone_id="bedroom", zone_name="Спальня")
    zone.transition(ZoneJobState.WAIT, now)
    assert zone.wait_cycle == 1
    zone.transition(ZoneJobState.PLANNED, now + timedelta(seconds=10))
    assert zone.first_wait_at is None
    zone.transition(ZoneJobState.WAIT, now + timedelta(minutes=1))
    assert zone.wait_cycle == 2
    restored = ZoneExecution.from_dict(zone.to_dict())
    assert restored.wait_cycle == 2
    assert restored.first_wait_at == now + timedelta(minutes=1)


def test_started_notification_reports_actual_multizone_snapshot_not_all_targets_as_started():
    event = _event(
        NotificationEventType.STARTED,
        (
            SemanticZoneSnapshot("kitchen", "Кухня", "RUNNING", actual_start=datetime(2026, 8, 17, 9, 0)),
            SemanticZoneSnapshot("bedroom", "Спальня", "WAIT", blockers=("zone_busy",)),
            SemanticZoneSnapshot("hall", "Коридор", "FINISHED", result="SKIPPED", reason_code="zone_disabled"),
        ),
    )
    message = render_plain(event, "ru").message
    assert "Начато: Кухня" in message
    assert "Ожидают: Спальня" in message
    assert "Не будут выполнены: Коридор — пропущено:" in message
    assert "Зоны: Кухня, Спальня, Коридор" not in message


def test_wait_notification_is_valid_while_another_zone_is_running():
    event = _event(
        NotificationEventType.WAIT_ENTER,
        (
            SemanticZoneSnapshot("kitchen", "Кухня", "RUNNING", actual_start=datetime(2026, 8, 17, 8, 58)),
            SemanticZoneSnapshot("bedroom", "Спальня", "WAIT", blockers=("zone_busy",)),
        ),
        blockers=("zone_busy",),
        wait_elapsed_seconds=180,
    )
    message = render_plain(event, "ru").message
    assert "Ожидают: Спальня" in message
    assert "Выполняются: Кухня" in message
    assert "Ожидание: 3 мин" in message


def test_finished_notification_contains_per_zone_result_and_reason():
    event = _event(
        NotificationEventType.FINISHED,
        (
            SemanticZoneSnapshot("kitchen", "Кухня", "FINISHED", result="SUCCESS", reason_code="simulated_success"),
            SemanticZoneSnapshot("bedroom", "Спальня", "FINISHED", result="FAILED", reason_code="zone_busy"),
        ),
        result="PARTIAL_SUCCESS",
        reason_code="partial_success",
    )
    rendered = render_plain(event, "ru")
    assert "Результаты по зонам:" in rendered.message
    assert "Кухня — выполнено" in rendered.message
    assert "Спальня — не выполнено:" in rendered.message
    assert "Зона уборки занята" in rendered.message


def test_partial_user_cancel_keeps_cancel_semantics_in_aggregate_and_renderer():
    source = ENGINE.read_text(encoding="utf-8")
    assert "user_reason = next(" in source
    assert "JobReason.USER_CANCELLED.value" in source
    assert "user_reason or JobReason.PARTIAL_SUCCESS.value" in source

    event = _event(
        NotificationEventType.FINISHED,
        (
            SemanticZoneSnapshot("kitchen", "Кухня", "FINISHED", result="SUCCESS"),
            SemanticZoneSnapshot("bedroom", "Спальня", "FINISHED", result="FAILED", reason_code="user_cancelled"),
        ),
        result="PARTIAL_SUCCESS",
        reason_code="user_cancelled",
    )
    rendered = render_plain(event, "ru")
    assert rendered.title == "⏹️ Уборка отменена"
    assert "Спальня — не выполнено: Отменено пользователем" in rendered.message


def test_wait_policy_tracks_waiting_zone_lifecycle_even_when_job_is_running():
    source = MANAGER.read_text(encoding="utf-8")
    assert "def _waiting_zone_runs(" in source
    assert "def _wait_context(" in source
    assert "run.state is ZoneJobState.WAIT" in source
    assert "getattr(run, 'wait_cycle', 0)" in source
    assert "wait_context = self._wait_context(job)" in source
    # Partial WAIT actions must remain valid for aggregate RUNNING jobs.
    assert 'command = "CANCEL" if has_started_work else "SKIP"' in source


def test_delivery_claim_is_persisted_before_provider_await_and_is_atomic():
    source = MANAGER.read_text(encoding="utf-8")
    assert "self._delivery_claim_lock = asyncio.Lock()" in source
    claim = source.index('item.status = "pending"')
    persist = source.index("await self.store.async_save()", claim)
    provider = source.index("metadata = await self._async_send_channel(", persist)
    assert claim < persist < provider
    assert "for _attempt in range(2)" not in source
    assert "Do not auto-retry an ambiguous provider failure" in source


def test_duplicate_logical_routes_to_one_physical_endpoint_are_suppressed():
    manager = MANAGER.read_text(encoding="utf-8")
    store = STORE.read_text(encoding="utf-8")
    assert "endpoint_claim = self.store.endpoint_claim(" in manager
    assert 'item.suppression_reason = "duplicate_endpoint_route"' in manager
    assert "item.target_kind == target_kind" in store
    assert 'item.status in {"pending", "sent"}' in store

    en = json.loads(EN.read_text(encoding="utf-8"))
    ru = json.loads(RU.read_text(encoding="utf-8"))
    key = "common.suppression.duplicate_endpoint_route"
    assert en[key]
    assert ru[key]


def test_user_facing_push_taxonomy_remains_job_level_only():
    # Zone transitions remain durable JobHistory detail, but there is no public
    # ZONE_STARTED/ZONE_FINISHED push event family that could recreate one push
    # per zone.
    values = {item.value for item in NotificationEventType}
    assert "zone_started" not in values
    assert "zone_finished" not in values
    assert "zone_failed" not in values


def test_atomic_outbox_runtime_coalesces_concurrent_event_and_duplicate_endpoint_routes():
    script = f"""
import sys, types, asyncio
from pathlib import Path
ROOT=Path({str(ROOT)!r}); MOD=ROOT/'custom_components'/'vacuum_schedule'
cc=types.ModuleType('custom_components'); cc.__path__=[str(ROOT/'custom_components')]; sys.modules['custom_components']=cc
pkg=types.ModuleType('custom_components.vacuum_schedule'); pkg.__path__=[str(MOD)]; sys.modules['custom_components.vacuum_schedule']=pkg
ha=types.ModuleType('homeassistant'); ha.__path__=[]; sys.modules['homeassistant']=ha
const=types.ModuleType('homeassistant.const'); const.STATE_HOME='home'; const.STATE_UNKNOWN='unknown'; const.STATE_UNAVAILABLE='unavailable'; sys.modules['homeassistant.const']=const
core=types.ModuleType('homeassistant.core'); core.HomeAssistant=object; sys.modules['homeassistant.core']=core
helpers=types.ModuleType('homeassistant.helpers'); helpers.__path__=[]; sys.modules['homeassistant.helpers']=helpers
for name in ['device_registry','entity_registry']:
    module=types.ModuleType('homeassistant.helpers.'+name); sys.modules['homeassistant.helpers.'+name]=module; setattr(helpers,name,module)
storage=types.ModuleType('homeassistant.helpers.storage')
class Store:
    def __init__(self,*args,**kwargs): pass
storage.Store=Store; sys.modules['homeassistant.helpers.storage']=storage
from custom_components.vacuum_schedule.notification_manager import NotificationManager
from custom_components.vacuum_schedule.notification_models import NotificationSettings, NotificationRecipient, NotificationChannel, TransportType, PresencePolicy, ChannelEventFilter, NotificationEventType, NotificationClass
from custom_components.vacuum_schedule.notification_formatting import SemanticNotificationEvent
from datetime import datetime
class FakeStore:
    def __init__(self): self.deliveries={{}}; self.history=[]
    def get(self,key): return self.deliveries.get(key)
    def put(self,item):
        self.deliveries[item.delivery_id]=item
        if item.delivery_id not in self.history: self.history.append(item.delivery_id)
    def endpoint_claim(self,event_id,target_kind,target):
        for key in reversed(self.history):
            item=self.deliveries[key]
            if item.event_id==event_id and item.target_kind==target_kind and item.target==target and (item.status in {{'pending','sent'}} or (item.status=='failed' and item.attempts>0)):
                return item
        return None
    async def async_save(self): pass
    def latest_for_job_channel(self,*args,**kwargs): return None
channels=(
    NotificationChannel('c1','C1',TransportType.GENERIC_NOTIFY,'notify.same',enabled=True,presence_policy=PresencePolicy.ALWAYS,event_filter=ChannelEventFilter.ALL),
    NotificationChannel('c2','C2',TransportType.GENERIC_NOTIFY,'notify.same',enabled=True,presence_policy=PresencePolicy.ALWAYS,event_filter=ChannelEventFilter.ALL),
)
settings=NotificationSettings(recipients=(NotificationRecipient('r','R',channels=channels),))
class Entry: entry_id='e'; options={{'notification_settings':settings.to_dict()}}
class Config: language='en'
class States:
    def get(self,_entity_id): return None
class Hass: config=Config(); states=States()
manager=NotificationManager(Hass(),Entry()); manager.store=FakeStore()
manager._resolved_channel_transport=lambda channel: channel
manager._channel_suppression=lambda *args: None
calls=0
async def send(*args,**kwargs):
    global calls
    calls += 1
    await asyncio.sleep(0.02)
    return {{'delivery_operation':'sent'}}
manager._async_send_channel=send
event=SemanticNotificationEvent(event_id='job:started',semantic_type='vacuum.job.started',event_type=NotificationEventType.STARTED,notification_class=NotificationClass.INFO,severity='info',created_at=datetime.now(),job_id='j',schedule_name='X')
async def main():
    first,second=await asyncio.gather(manager.async_deliver_event(event),manager.async_deliver_event(event))
    assert calls == 1, calls
    records=list(manager.store.deliveries.values())
    assert sum(item.status == 'sent' for item in records) == 1
    assert any(item.suppression_reason == 'duplicate_endpoint_route' for item in records)
    assert first['sent'] + second['sent'] == 1
asyncio.run(main())
"""
    completed = subprocess.run(["python", "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr or completed.stdout
