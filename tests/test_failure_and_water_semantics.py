"""Regression coverage for actionable execution failures and water-level UI semantics."""
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"


def test_generic_execution_failure_promotes_authoritative_resource_cause():
    script = textwrap.dedent(f"""
        from pathlib import Path
        from datetime import datetime, timedelta, timezone
        import sys, types
        ROOT=Path({str(ROOT)!r})
        MODULE=ROOT/'custom_components'/'vacuum_schedule'
        cc=types.ModuleType('custom_components'); cc.__path__=[str(ROOT/'custom_components')]
        sys.modules['custom_components']=cc
        pkg=types.ModuleType('custom_components.vacuum_schedule'); pkg.__path__=[str(MODULE)]
        sys.modules['custom_components.vacuum_schedule']=pkg
        stub=types.ModuleType('custom_components.vacuum_schedule.execution_backend')
        stub.REAL_START_TIMEOUT=timedelta(seconds=120)
        stub.DryRunExecutionBackend=type('DryRunExecutionBackend',(),{{}})
        stub.RealExecutionBackend=type('RealExecutionBackend',(),{{}})
        sys.modules[stub.__name__]=stub
        from custom_components.vacuum_schedule.execution_manager import ExecutionManager
        from custom_components.vacuum_schedule.execution_models import ExecutionAttempt,ExecutionAttemptState,ExecutionMode
        now=datetime(2026,9,11,12,0,tzinfo=timezone.utc)
        attempt=ExecutionAttempt.create(job_id='job',execution_mode=ExecutionMode.REAL,zone_ids=('room',),target_type='segment',targets=('16',),cleaning_params={{}},now=now)
        attempt.state=ExecutionAttemptState.FAILED
        attempt.failure_reason='robot_error_recovery_timeout'
        attempt.metadata['runtime_error_timeout_observation']={{'resource_blockers':['clean_water_insufficient']}}
        assert ExecutionManager._resolved_attempt_failure_reason(attempt)=='clean_water_insufficient'
        assert attempt.failure_reason=='robot_error_recovery_timeout'
        assert attempt.metadata['technical_failure_reason']=='robot_error_recovery_timeout'
        hard=ExecutionAttempt.create(job_id='job',execution_mode=ExecutionMode.REAL,zone_ids=('room',),target_type='segment',targets=('16',),cleaning_params={{}},now=now)
        hard.state=ExecutionAttemptState.FAILED
        hard.failure_reason='execution_preempted_external'
        hard.metadata['resource_blockers']=['clean_water_insufficient']
        assert ExecutionManager._resolved_attempt_failure_reason(hard)=='execution_preempted_external'
        print('ok')
    """)
    proc=subprocess.run(['python','-c',script],capture_output=True,text=True,check=False)
    assert proc.returncode==0,proc.stderr
    assert proc.stdout.strip()=='ok'


def test_water_level_editor_is_unambiguous_for_clean_and_dirty_tanks():
    panel=(MODULE/'frontend'/'panel.js').read_text(encoding='utf-8')
    for token in (
        'panel.set_clean_remaining',
        'panel.set_dirty_filled',
        'panel.clean_water_remaining_percent',
        'panel.dirty_water_filled_percent',
        'panel.clean_level_percent_help',
        'panel.dirty_level_percent_help',
        'panel.service_clean_level',
        'panel.service_dirty_level',
    ):
        assert token in panel
    assert 'fmt(dirty.filled_percent)' in panel
    assert 'percent(dirty.filled_percent)' in panel


def test_water_level_backend_semantics_match_ui_contract():
    source=(MODULE/'water_statistics.py').read_text(encoding='utf-8')
    apply_start=source.index('def _apply_interpretation')
    level_start=source.index('elif action == "level":',apply_start)
    level_end=source.index('elif action in {"add_ml", "remove_ml"}:',level_start)
    level=source[level_start:level_end]
    assert 'estimate = capacity * percent / 100.0' in level
    payload=source[source.index('def _balance_payload'):source.index('def _model_quality')]
    assert 'result["filled_percent"] = percent' in payload
    assert 'result["remaining_percent"] = percent' in payload


def test_failure_and_water_labels_have_en_ru_uk_parity():
    import json
    required={
        'panel.clean_water_remaining_percent','panel.dirty_water_filled_percent',
        'panel.clean_level_percent_help','panel.dirty_level_percent_help',
        'panel.set_clean_remaining','panel.set_dirty_filled',
        'panel.service_clean_level','panel.service_dirty_level',
        'panel.failure_explanation.clean_water_insufficient',
        'panel.failure_explanation.dirty_water_full',
        'panel.failure_explanation.detergent_unavailable',
        'panel.failure_explanation.unknown',
    }
    for lang in ('en','ru','uk'):
        data=json.loads((MODULE/'frontend'/'localization'/f'{lang}.json').read_text(encoding='utf-8'))
        assert required <= set(data)
