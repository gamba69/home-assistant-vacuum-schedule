"""0.12.3 transient pre-command blocker regression tests."""
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_start_path_defers_retryable_guard_before_failed_attempt_can_finish_zone():
    source = (MODULE / "execution_manager.py").read_text(encoding="utf-8")
    section = source[source.index("async def _start_attempt"):source.index("async def async_start_ready_zones")]
    assert "if self._is_retryable_precommand_wait(attempt):" in section
    assert "return self._defer_precommand_attempt(job, attempt, now)" in section
    assert section.index("_is_retryable_precommand_wait") < section.index("_sync_attempt_to_zones")
    for reason in (
        "vacuum_busy",
        "vacuum_unavailable",
        "clean_water_insufficient",
        "dirty_water_full",
        "detergent_unavailable",
    ):
        assert f'"{reason}"' in source
    assert "attempt.command_intent_at is None" in source


def test_vacuum_busy_rollback_behavior_in_isolated_process():
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
        from custom_components.vacuum_schedule.job import JobInstance,JobOrigin,JobState,ZoneExecution,ZoneJobState
        now=datetime(2026,8,31,16,30,tzinfo=timezone.utc)
        zone=ZoneExecution(zone_id='kitchen',zone_name='Kitchen',robot_target_type='segment',robot_target_id='16',state=ZoneJobState.STARTING,starting_at=now,metadata={{'execution_attempt_id':'attempt'}})
        job=JobInstance(job_id='job',occurrence_id='occ',schedule_id='schedule',schedule_revision=1,schedule_name='Kitchen',created_at=now-timedelta(minutes=5),warning_at=now-timedelta(minutes=2),planned_start=now-timedelta(minutes=1),deadline_at=now+timedelta(hours=1),next_planned_start=now+timedelta(days=1),target_type='cleaning_zones',targets=('kitchen',),cleaning_params={{}},origin=JobOrigin.SCHEDULED,state=JobState.STARTING,starting_at=now,execution_mode=ExecutionMode.REAL,simulation=False,zone_runs={{'kitchen':zone}})
        attempt=ExecutionAttempt.create(job_id='job',execution_mode=ExecutionMode.REAL,zone_ids=('kitchen',),target_type='segment',targets=('16',),cleaning_params={{}},now=now)
        attempt.attempt_id='attempt'; attempt.state=ExecutionAttemptState.FAILED; attempt.preparing_at=now; attempt.completed_at=now; attempt.failure_reason='vacuum_busy'; attempt.metadata['start_error']='late final guard'
        job.execution_attempts[attempt.attempt_id]=attempt
        assert ExecutionManager._is_retryable_precommand_wait(attempt)
        assert ExecutionManager._defer_precommand_attempt(job,attempt,now)
        assert not job.terminal and job.state is JobState.WAIT and job.result is None and job.reason_code is None
        assert job.execution_attempts=={{}} and job.starting_at is None
        assert job.blockers==('vacuum_busy',) and job.current_blockers==('vacuum_busy',) and job.current_preflight_decision=='WAIT'
        zone=job.zone_runs['kitchen']
        assert not zone.terminal and zone.state is ZoneJobState.WAIT and zone.result is None and zone.reason_code is None
        assert zone.blockers==('vacuum_busy',) and zone.starting_at is None and 'execution_attempt_id' not in zone.metadata
        assert job.metadata['precommand_deferred_starts'][-1]['reason']=='vacuum_busy'
        # Crossing COMMAND_INTENT must disable rollback semantics.
        attempt2=ExecutionAttempt.create(job_id='job',execution_mode=ExecutionMode.REAL,zone_ids=('kitchen',),target_type='segment',targets=('16',),cleaning_params={{}},now=now)
        attempt2.state=ExecutionAttemptState.FAILED; attempt2.failure_reason='vacuum_busy'; attempt2.command_intent_at=now
        assert not ExecutionManager._is_retryable_precommand_wait(attempt2)
        # Hard preparation errors remain failures.
        attempt3=ExecutionAttempt.create(job_id='job',execution_mode=ExecutionMode.REAL,zone_ids=('kitchen',),target_type='segment',targets=('16',),cleaning_params={{}},now=now)
        attempt3.state=ExecutionAttemptState.FAILED; attempt3.failure_reason='execution_preparation_failed'
        assert not ExecutionManager._is_retryable_precommand_wait(attempt3)
        print('ok')
    """)
    proc = subprocess.run(["python", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
