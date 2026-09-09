"""Future planned-job protection contracts for Vacuum Schedule 0.10.7."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from force_protection import evaluate_force_plan_protection  # noqa: E402
from statistics_models import build_force_duration_profiles, estimate_force_duration  # noqa: E402


def _record(*, seconds: float, mode: str = "REAL", result: str = "SUCCESS", zones=("kitchen",), params=None):
    return {
        "job_id": f"{mode}-{result}-{seconds}-{zones}",
        "execution_mode": mode,
        "result": result,
        "cleaning_params_snapshot": dict(params or {"cleaning_mode": "vacuum_mop", "passes": 1}),
        "time": {"physical_execution_seconds": seconds},
        "zones": [
            {
                "zone_id": zone_id,
                "result": "SUCCESS" if result in {"SUCCESS", "PARTIAL"} else "FAILED",
                "attributed_cleaning_seconds": seconds / max(1, len(zones)),
            }
            for zone_id in zones
        ],
    }




def test_duration_estimate_uses_real_success_p90_and_requires_three_samples():
    params = {"cleaning_mode": "vacuum_mop", "passes": 1}
    records = [
        _record(seconds=1200, params=params),
        _record(seconds=1500, params=params),
        _record(seconds=1800, params=params),
        _record(seconds=10, mode="DRY_RUN", params=params),
        _record(seconds=100, result="FAILED", params=params),
    ]
    profiles = build_force_duration_profiles(records)
    estimate = estimate_force_duration(
        profiles,
        zone_ids=("kitchen",),
        cleaning_params=params,
        minimum_samples=3,
    )
    assert estimate["available"] is True
    assert estimate["basis"] == "zones_and_parameters"
    assert estimate["sample_count"] == 3
    assert estimate["p90_seconds"] == 1740.0

    insufficient = build_force_duration_profiles(records[:2])
    estimate = estimate_force_duration(
        insufficient,
        zone_ids=("kitchen",),
        cleaning_params=params,
        minimum_samples=3,
    )
    assert estimate["available"] is False


def test_duration_estimate_can_conservatively_sum_per_zone_p90():
    params = {"cleaning_mode": "vacuum", "passes": 1}
    records = []
    for seconds in (300, 360, 420):
        records.append(_record(seconds=seconds, zones=("a",), params=params))
    for seconds in (600, 660, 720):
        records.append(_record(seconds=seconds, zones=("b",), params=params))
    estimate = estimate_force_duration(
        build_force_duration_profiles(records),
        zone_ids=("a", "b"),
        cleaning_params=params,
        minimum_samples=3,
    )
    assert estimate["available"] is True
    assert estimate["basis"] == "zone_composite_and_parameters"
    assert estimate["p90_seconds"] == 1116.0


def test_plan_protection_uses_final_forecast_value_without_hidden_buffer():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    estimate = {
        "enabled": True, "available": True, "basis": "zones_and_parameters",
        "sample_count": 3, "percentile": 90, "delta_percent": 10,
        "raw_percentile_value": 2700, "forecast_value": 2970,
    }
    blocked = evaluate_force_plan_protection(
        now=now, next_planned_start=now + timedelta(seconds=2969), duration_estimate=estimate,
    )
    assert blocked["allowed"] is False
    assert blocked["required_gap_seconds"] == 2970
    assert blocked["safety_buffer_seconds"] == 0

    allowed = evaluate_force_plan_protection(
        now=now, next_planned_start=now + timedelta(seconds=2970), duration_estimate=estimate,
    )
    assert allowed["allowed"] is True


def test_unknown_or_disabled_time_forecast_is_fail_open():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    for estimate in (
        {"enabled": True, "available": False, "forecast_value": None},
        {"enabled": False, "available": True, "forecast_value": 99999},
    ):
        result = evaluate_force_plan_protection(
            now=now, next_planned_start=now + timedelta(seconds=1), duration_estimate=estimate,
        )
        assert result["allowed"] is True
        assert result["reason"] == "forecast_disabled_or_unavailable"
        assert result["required_gap_seconds"] == 0
        assert result["safety_buffer_seconds"] == 0


def test_no_future_plan_never_blocks_force():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    result = evaluate_force_plan_protection(
        now=now,
        next_planned_start=None,
        duration_estimate={"available": False},
    )
    assert result["allowed"] is True
    assert result["reason"] == "no_future_planned_job"


def test_engine_applies_protection_before_force_becomes_eligible_and_never_waits():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    evaluate = source[source.index("def _evaluate_force_candidates"):source.index("def _set_execution_lease_wait")]
    assert "self._force_plan_protection(job, ready_zone_ids, now)" in evaluate
    assert 'and (protection is None or bool(protection.get("allowed")))' in evaluate
    assert ".transition(ZoneJobState.WAIT" not in evaluate
    assert 'existing_force.get("plan_protection", {})' in source
    assert "DEFAULT_UNKNOWN_DURATION_MIN_GAP_SECONDS = 0" in (MODULE / "force_protection.py").read_text(encoding="utf-8")
    assert "DEFAULT_SAFETY_BUFFER_SECONDS = 0" in (MODULE / "force_protection.py").read_text(encoding="utf-8")
    assert "pending_terminal_jobs=tuple(self.store.terminal_archive)" in source
    assert "self._job_has_unstarted_zone(owner)" in source
    assert '"minimum_real_samples": 3' in source
    assert "DEFAULT_UNKNOWN_DURATION_MIN_GAP_SECONDS" in source


def test_frontend_explains_plan_protection_deferral():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    ru = (MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8")
    assert "force.plan_protection.allowed===false" in panel
    assert "panel.force_plan_protection_blocked_estimated" in panel
    assert "panel.force_plan_protection_blocked_unknown" not in panel
    assert "Досрочный запуск отложен" in ru
