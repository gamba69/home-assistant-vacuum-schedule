"""Regression coverage for semantic partial START + WAIT coalescing in 0.12.53."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANAGER = MODULE / "notification_manager.py"
STORE = MODULE / "notification_store.py"
FORMATTING = MODULE / "notification_formatting.py"


def test_partial_start_wait_coalescing_is_durable_and_episode_specific():
    manager = MANAGER.read_text(encoding="utf-8")
    store = STORE.read_text(encoding="utf-8")
    formatting = FORMATTING.read_text(encoding="utf-8")
    assert "def _partial_start_wait_event_id(" in manager
    assert "def _partial_start_wait_route_suppression(" in manager
    assert 'return "partial_start_coalesced"' in manager
    assert "NotificationEventType.STARTED,\n                NotificationEventType.WAIT_ENTER" in manager
    assert "coalesced_wait_event_id" in formatting
    assert "coalesced_wait_event_id" in store


def test_partial_start_suppression_label_exists_in_all_panel_locales():
    values = {
        "en": "Merged into partial-start notification",
        "ru": "Объединено с уведомлением о частичном старте",
        "uk": "Об’єднано зі сповіщенням про частковий старт",
    }
    for language, expected in values.items():
        path = MODULE / "frontend" / "localization" / f"{language}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["common.suppression.partial_start_coalesced"] == expected


def test_runtime_sends_one_push_for_partial_start_and_same_wait_episode_but_not_for_reentry():
    script = f'''
import sys, types, asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
    def __init__(self,*a,**k): self.data=None
    async def async_load(self): return self.data
    async def async_save(self,data): self.data=data
storage.Store=Store; sys.modules['homeassistant.helpers.storage']=storage

from custom_components.vacuum_schedule.notification_manager import NotificationManager
from custom_components.vacuum_schedule.notification_store import NotificationStore
from custom_components.vacuum_schedule.notification_models import NotificationChannel, NotificationPolicy, NotificationPreset, NotificationRecipient, NotificationSettings, TransportType, PresencePolicy, ChannelEventFilter, PrewarningMode, WaitEnterMode, StartMode, FinishMode, StartForecastMode
from custom_components.vacuum_schedule.job import JobInstance, ZoneJobState, JobState
from custom_components.vacuum_schedule.schedule import Occurrence

now=datetime(2026,9,7,20,0,tzinfo=timezone.utc)
occ=Occurrence('o','s',1,'Test',now,now-timedelta(minutes=5),now+timedelta(hours=1),now+timedelta(days=1),'cleaning_zones',('k','b'),{{}}, zone_execution_policy='progressive')
job=JobInstance.from_occurrence(occ, now-timedelta(minutes=5))
job.zone_runs['k'].zone_name='Kitchen'; job.zone_runs['b'].zone_name='Bedroom'
job.zone_runs['k'].transition(ZoneJobState.RUNNING, now)
job.zone_runs['b'].blockers=('zone_busy',); job.zone_runs['b'].transition(ZoneJobState.WAIT, now)
job.state=JobState.RUNNING
channel=NotificationChannel('c','Phone',TransportType.GENERIC_NOTIFY,'notify.phone',enabled=True,presence_policy=PresencePolicy.ALWAYS,event_filter=ChannelEventFilter.ALL)
recipient=NotificationRecipient('r','R',channels=(channel,))
policy=NotificationPolicy(prewarning_mode=PrewarningMode.OFF,wait_enter_mode=WaitEnterMode.IMMEDIATE,wait_delay_seconds=0,wait_reminder_enabled=False,wait_reminder_interval_seconds=1800,wait_reminder_max_count=0,start_mode=StartMode.ALWAYS,finish_mode=FinishMode.OFF,start_forecast_mode=StartForecastMode.OFF)
settings=NotificationSettings(enabled=True,preset=NotificationPreset.CUSTOM,policy=policy,recipients=(recipient,),dry_run_delivery='send')
class Entry: entry_id='e'; options={{'notification_settings':settings.to_dict(),'schedules':[]}}
class Config: language='en'; external_url=''; internal_url=''
class States:
    def get(self,*a): return None
class Hass: config=Config(); states=States()
manager=NotificationManager(Hass(),Entry()); manager.store=NotificationStore(Hass(),'e')
manager._resolved_channel_transport=lambda c:c
sent=[]
async def send(channel,event,rendered,previous=None,language=None):
    sent.append(event.event_type.value)
    return {{'delivery_operation':'sent'}}
manager._async_send_channel=send
async def main():
    await manager.async_process_job(job,now)
    assert sent == ['started'], sent
    waits=[x for x in manager.store.deliveries.values() if x.event_type=='wait_enter']
    assert len(waits)==1 and waits[0].status=='suppressed' and waits[0].suppression_reason=='partial_start_coalesced', waits
    started=manager.store.get_event(f'{{job.job_id}}:started')
    assert started and started.coalesced_wait_event_id
    job.zone_runs['b'].transition(ZoneJobState.PLANNED, now+timedelta(minutes=1))
    job.zone_runs['b'].blockers=('zone_busy',)
    job.zone_runs['b'].transition(ZoneJobState.WAIT, now+timedelta(minutes=2))
    await manager.async_process_job(job,now+timedelta(minutes=2))
    assert sent == ['started','wait_enter'], sent
asyncio.run(main())
'''
    completed = subprocess.run(["python", "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr or completed.stdout
