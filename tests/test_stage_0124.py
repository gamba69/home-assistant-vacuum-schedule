"""Execution source semantics for Vacuum Schedule."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))
pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)

from custom_components.vacuum_schedule.execution_models import ExecutionMode
from custom_components.vacuum_schedule.job import JobExecutionSource, JobInstance, JobOrigin
from custom_components.vacuum_schedule.schedule import Occurrence
from custom_components.vacuum_schedule.statistics_models import build_statistics_record, record_matches


def _job():
    now=datetime(2026,8,31,14,0,tzinfo=timezone.utc)
    occ=Occurrence(occurrence_id="o",schedule_id="s",schedule_revision=1,schedule_name="Kitchen",planned_start=now+timedelta(hours=1),warning_at=now,deadline_at=now+timedelta(hours=3),next_planned_start=None,target_type="cleaning_zones",targets=("z",),cleaning_params={},zone_execution_policy="together")
    job=JobInstance.from_occurrence(occ,now)
    job.execution_mode=ExecutionMode.REAL
    job.simulation=False
    return job, now


def test_manual_release_does_not_change_occurrence_origin_but_changes_execution_source():
    job, now=_job()
    assert job.origin is JobOrigin.SCHEDULED
    assert job.execution_source is JobExecutionSource.SCHEDULED
    job.manual_release_at=now
    assert job.origin is JobOrigin.SCHEDULED
    assert job.execution_source is JobExecutionSource.MANUAL
    payload=job.to_dict()
    assert payload["origin"] == "SCHEDULED"
    assert payload["execution_source"] == "MANUAL"


def test_force_and_external_sources_are_distinct():
    job, now=_job()
    job.metadata["force_execution"]={"committed":True,"selected_at":now.isoformat()}
    assert job.execution_source is JobExecutionSource.FORCE
    job.origin=JobOrigin.EXTERNAL
    assert job.execution_source is JobExecutionSource.EXTERNAL


def test_statistics_record_and_filter_use_execution_source():
    job, now=_job()
    job.manual_release_at=now
    record=build_statistics_record(job.to_dict(),[],recorded_at=now)
    assert record["origin"] == "SCHEDULED"
    assert record["execution_source"] == "MANUAL"
    assert record_matches(record,{"execution_source":"MANUAL"})
    assert not record_matches(record,{"execution_source":"SCHEDULED"})


def test_frontend_uses_four_actual_execution_sources():
    panel=(MODULE/'frontend/panel.js').read_text(encoding='utf-8')
    fn=panel[panel.index('_historySource(job)'):panel.index('_historyResultCategory(job)')]
    assert 'job?.manual_release_at' in fn
    assert 'job?.execution_source' in fn
    assert 'return "FORCE"' in fn
    assert 'id="statistics-source"' in panel
    assert 'execution_source:f.execution_source||"all"' in panel
    ru=__import__('json').loads((MODULE/'frontend/localization/ru.json').read_text(encoding='utf-8'))
    assert ru['panel.source_scheduled']=='По расписанию'
    assert ru['panel.source_manual']=='Вручную'
    assert ru['panel.source_external']=='Внешний'
    assert ru['panel.source_force']=='Досрочно'


