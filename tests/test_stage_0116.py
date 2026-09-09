"""Regression tests for planned job keeps live preflight after advisory until start, advisory snapshot is not reused as live current report, and prestart live preview tracks pending zones and updates dependency.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
ENGINE = MODULE / "scheduler_engine.py"




def test_planned_job_keeps_live_preflight_after_advisory_until_start():
    source = ENGINE.read_text(encoding="utf-8")
    start = source.index("async def _prepare_job")
    end = source.index("def _force_readiness", start)
    prepare = source[start:end]

    advisory = "changed |= self._capture_advisory_snapshot(job, report, now)"
    early_return = "if instant_lt(now, job.effective_start):"
    live_eval = "PreflightPhase.CURRENT_PREVIEW"
    live_update = "changed |= self._update_current_preflight(job, live_report)"

    assert advisory in prepare
    assert early_return in prepare
    assert live_eval in prepare
    assert live_update in prepare
    # Critical regression contract: current preview must execute inside the
    # pre-start branch before the branch returns.
    branch = prepare[prepare.index(early_return):prepare.index("# Zone-level disable", prepare.index(early_return))]
    assert live_eval in branch
    assert live_update in branch
    assert branch.index(live_eval) < branch.index("return changed, ()")


def test_advisory_snapshot_is_not_reused_as_live_current_report():
    source = ENGINE.read_text(encoding="utf-8")
    start = source.index("async def _prepare_job")
    end = source.index("def _force_readiness", start)
    prepare = source[start:end]
    advisory_block = prepare[
        prepare.index("if (\n            job.state is JobState.PLANNED"):
        prepare.index("if instant_lt(now, job.effective_start):")
    ]
    assert "PreflightPhase.ADVISORY" in advisory_block
    assert "_capture_advisory_snapshot" in advisory_block
    assert "_update_current_preflight(job, report)" not in advisory_block


def test_prestart_live_preview_tracks_pending_zones_and_updates_dependency_index():
    source = ENGINE.read_text(encoding="utf-8")
    start = source.index("if instant_lt(now, job.effective_start):")
    end = source.index("# Zone-level disable", start)
    branch = source[start:end]
    assert "job.advisory_checked_at is not None" in branch
    assert "zone_run.state in (ZoneJobState.PLANNED, ZoneJobState.WAIT)" in branch
    assert "zone_ids=pending_zone_ids" in branch
    # _update_current_preflight is the canonical path that refreshes current
    # blockers, current report, next timer and DependencyIndex.
    update_start = source.index("def _update_current_preflight")
    update_end = source.index("@staticmethod\n    def _zone_execution_policy", update_start)
    update = source[update_start:update_end]
    assert "self.dependency_index.update_job(job.job_id, report.dependencies)" in update
    assert 'job.metadata["current_preflight_next_recheck_at"]' in update


