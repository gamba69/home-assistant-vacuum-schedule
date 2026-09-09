"""Regressions for live completion/progress, statistics defaults and compact row actions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from execution_models import ExecutionAttempt, ExecutionMode  # noqa: E402


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt.create(
        job_id="job",
        execution_mode=ExecutionMode.REAL,
        zone_ids=("zone",),
        target_type="segment",
        targets=("17",),
        cleaning_params={"passes": 1},
        now=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
    )


def test_live_clean_percent_ignores_stale_previous_run_and_terminal_reset():
    attempt = _attempt()
    attempt.metadata["statistics_observations"] = [
        {"phase": "IDLE", "clean_percent": 100},  # stale previous run
        {"phase": "CLEANING", "task_kind": "segment", "clean_percent": 0},
        {"phase": "CLEANING", "task_kind": "segment", "clean_percent": 31},
        {"phase": "CLEANING", "task_kind": "segment", "clean_percent": 74},
        {"phase": "SERVICE", "clean_percent": 0},  # dock reset
        {"phase": "IDLE", "clean_percent": None},
    ]
    assert attempt.stable_clean_percent() == 74.0


def test_live_clean_percent_is_unknown_before_current_cleaning_is_observed():
    attempt = _attempt()
    attempt.metadata["statistics_observations"] = [
        {"phase": "IDLE", "clean_percent": 100},
        {"phase": "SERVICE", "clean_percent": 0},
    ]
    assert attempt.stable_clean_percent() is None


def test_statistics_opens_on_current_month_quick_range():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    ensure = panel[panel.index("_statisticsEnsureDateRange()") : panel.index("_statisticsRangeIso", panel.index("_statisticsEnsureDateRange()"))]
    assert "new Date(today.getFullYear(), today.getMonth(), 1" in ensure
    assert "start.setDate(start.getDate() - 29)" not in ensure


def test_live_percent_frontend_does_not_convert_null_to_zero():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert "const robotPercentRaw=live.robot_clean_percent;" in panel
    assert "robotPercentRaw===null||robotPercentRaw===undefined" in panel
    assert "Number(live.robot_clean_percent??obs.clean_percent)" not in panel


def test_table_row_actions_use_compact_square_controls():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert "button.icon-only.compact-icon-action {" in panel
    assert "width:var(--vs-compact-action-size)!important;" in panel
    assert "height:var(--vs-compact-action-size)!important;" in panel
    assert "--vs-compact-action-size:28px;" in panel
    assert "--vs-compact-action-radius:7px;" in panel
    assert "border-radius:var(--vs-compact-action-radius)!important;" in panel
    assert 'class="primary icon-only compact-icon-action override-apply"' in panel
