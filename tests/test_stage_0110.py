"""Forecasting contracts for Vacuum Schedule 0.11.9."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from bindings import PreflightPolicy  # noqa: E402
from forecast_models import (  # noqa: E402
    ForecastPolicyConfig,
    archive_branch,
    compact_forecast_sample,
    estimate_from_samples,
    filtered_samples,
    new_active_branch,
    model_tables,
)
from force_protection import evaluate_force_plan_protection  # noqa: E402


def _sample(at: datetime, value: float, *, job: str, zone: str = "kitchen", params=None):
    return {
        "sample_id": job,
        "job_id": job,
        "schedule_id": "s1",
        "schedule_name": "Daily",
        "at": at.isoformat(),
        "result": "SUCCESS",
        "params": dict(params or {"cleaning_mode": "vacuum_mop", "passes": 1}),
        "job_value": value,
        "zone_ids": [zone],
        "zones": [{"zone_id": zone, "zone_name": "Kitchen", "value": value, "result": "SUCCESS"}],
    }


def _record(*, mode="REAL", result="SUCCESS", seconds=1200, battery=10, zones=("kitchen",)):
    return {
        "job_id": f"{mode}-{result}-{seconds}-{battery}-{zones}",
        "schedule_id": "s1",
        "schedule_name": "Daily",
        "execution_mode": mode,
        "result": result,
        "finished_at": "2026-08-27T10:00:00+03:00",
        "cleaning_params_snapshot": {"cleaning_mode": "vacuum_mop", "passes": 1},
        "time": {"physical_execution_seconds": seconds},
        "battery": {"consumed_percent": battery},
        "zones": [
            {
                "zone_id": z,
                "zone_name": z.title(),
                "result": "SUCCESS" if result in {"SUCCESS", "PARTIAL"} else "FAILED",
                "attributed_cleaning_seconds": seconds / len(zones),
                "attributed_battery_percent": battery / len(zones),
                "nominal_area_m2": 10,
            }
            for z in zones
        ],
    }




def test_four_forecast_policies_are_independent():
    policy = PreflightPolicy.from_dict({"forecasts": {
        "time": {"enabled": True, "percentile": 80, "delta_percent": 5, "lookback_days": 30},
        "battery": {"enabled": True, "percentile": 95, "delta_percent": 17, "lookback_days": 90},
        "clean_water": {"enabled": False, "percentile": 85, "delta_percent": 22, "lookback_days": 120},
        "dirty_water": {"enabled": True, "percentile": 91, "delta_percent": 7, "lookback_days": 45},
    }})
    encoded = policy.to_dict()["forecasts"]
    assert encoded["time"] == {"enabled": True, "percentile": 80, "delta_percent": 5.0, "lookback_days": 30}
    assert encoded["battery"]["percentile"] == 95 and encoded["battery"]["delta_percent"] == 17.0
    assert encoded["clean_water"]["lookback_days"] == 120 and encoded["clean_water"]["enabled"] is False
    assert encoded["dirty_water"]["percentile"] == 91 and encoded["dirty_water"]["lookback_days"] == 45


def test_delta_is_percentage_of_selected_percentile_value():
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=15, lookback_days=90)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    samples = [_sample(now - timedelta(days=i), value, job=f"j{i}") for i, value in enumerate((10, 20, 30))]
    estimate = estimate_from_samples(samples, zone_ids=["kitchen"], cleaning_params={"cleaning_mode": "vacuum_mop", "passes": 1}, policy=policy)
    assert estimate["available"] is True
    assert estimate["raw_percentile_value"] == 28.0
    assert round(float(estimate["forecast_value"]), 6) == 32.2
    assert estimate["delta_percent"] == 15


def test_untrained_profile_is_unavailable_and_not_invented():
    policy = ForecastPolicyConfig(enabled=True, percentile=95, delta_percent=20, lookback_days=90)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    samples = [_sample(now, 10, job="a"), _sample(now, 12, job="b")]
    estimate = estimate_from_samples(samples, zone_ids=["kitchen"], cleaning_params={"cleaning_mode": "vacuum_mop", "passes": 1}, policy=policy)
    assert estimate["available"] is False
    assert estimate["forecast_value"] is None
    assert estimate["minimum_samples"] == 3


def test_only_real_execution_trains_and_partial_successful_zone_remains_usable():
    assert compact_forecast_sample(_record(mode="DRY_RUN"), "time") is None
    partial = compact_forecast_sample(_record(result="PARTIAL", zones=("kitchen", "hall")), "battery")
    assert partial is not None
    assert partial["job_value"] is None
    assert {z["zone_id"] for z in partial["zones"]} == {"kitchen", "hall"}


def test_lookback_and_generation_boundary_both_limit_training_set():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    branch = new_active_branch(now=now - timedelta(days=20), samples=[
        _sample(now - timedelta(days=15), 10, job="old"),
        _sample(now - timedelta(days=8), 20, job="inside-lookback"),
        _sample(now - timedelta(days=2), 30, job="new"),
    ])
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=10, lookback_days=10)
    assert [x["job_id"] for x in filtered_samples(branch, policy, now=now)] == ["inside-lookback", "new"]
    branch["started_at"] = (now - timedelta(days=5)).isoformat()
    assert [x["job_id"] for x in filtered_samples(branch, policy, now=now)] == ["new"]


def test_partial_archive_moves_only_older_data_and_keeps_recent_tail():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    branch = new_active_branch(now=now - timedelta(days=100), generation=3, samples=[
        _sample(now - timedelta(days=20), 10, job="old"),
        _sample(now - timedelta(days=4), 20, job="recent1"),
        _sample(now - timedelta(days=1), 30, job="recent2"),
    ])
    rotated, archive = archive_branch(branch, retain_days=5, now=now)
    assert archive is not None
    assert archive["sample_count"] == 1
    assert [x["job_id"] for x in archive["samples"]] == ["old"]
    assert [x["job_id"] for x in rotated["samples"]] == ["recent1", "recent2"]
    assert rotated["generation"] == 4
    assert rotated["started_at"] == (now - timedelta(days=5)).isoformat()


def test_zero_day_archive_is_full_model_reset_boundary():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    branch = new_active_branch(now=now - timedelta(days=20), samples=[_sample(now - timedelta(days=1), 10, job="a")])
    rotated, archive = archive_branch(branch, retain_days=0, now=now)
    assert archive and archive["sample_count"] == 1
    assert rotated["samples"] == []
    assert rotated["started_at"] == now.isoformat()


def test_force_uses_final_forecast_value_without_hidden_buffer():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    estimate = {"enabled": True, "available": True, "percentile": 90, "delta_percent": 10, "raw_percentile_value": 2700, "forecast_value": 2970}
    blocked = evaluate_force_plan_protection(now=now, next_planned_start=now + timedelta(seconds=2969), duration_estimate=estimate)
    assert blocked["allowed"] is False
    assert blocked["required_gap_seconds"] == 2970
    assert blocked["safety_buffer_seconds"] == 0
    allowed = evaluate_force_plan_protection(now=now, next_planned_start=now + timedelta(seconds=2970), duration_estimate=estimate)
    assert allowed["allowed"] is True


def test_force_is_fail_open_when_time_forecast_disabled_or_untrained():
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    for estimate in (
        {"enabled": False, "available": True, "forecast_value": 99999},
        {"enabled": True, "available": False, "forecast_value": None},
    ):
        result = evaluate_force_plan_protection(now=now, next_planned_start=now + timedelta(minutes=1), duration_estimate=estimate)
        assert result["allowed"] is True
        assert result["reason"] == "forecast_disabled_or_unavailable"
        assert result["required_gap_seconds"] == 0


def test_frontend_separates_historical_statistics_from_trained_forecast_data():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    stats = panel[panel.index("  _statisticsHtml() {"):panel.index("\n\n  _maintenanceActionLabel", panel.index("  _statisticsHtml() {"))]
    forecast = panel[panel.index("  _statisticsForecastHtml("):panel.index("\n\n  _statisticsHtml() {", panel.index("  _statisticsForecastHtml("))]
    assert 'this._tr("panel.median")' in stats and 'this._formatPercentileLabel(configuredPercentile("time"))' in stats
    assert 'this._formatPercentileLabel(percentile)' in forecast and 'class="metric-header">Δ</th>' in forecast
    assert '["forecast","chart-bell-curve-cumulative"' in stats
    assert 'active==="forecast"?"":filters' in stats
    assert 'active==="forecast"?"":periodNav' in stats


def test_forecast_settings_ui_has_four_independent_controls_and_archive_preview():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    for metric in ("time", "battery", "clean_water", "dirty_water"):
        assert f'["{metric}",this._tr("panel.forecast_' in panel
        assert f'forecast-${{key}}-percentile' in panel
        assert f'forecast-${{key}}-delta' in panel
        assert f'forecast-${{key}}-days' in panel
    assert 'statistics/forecast/archive_preview' in panel and 'statistics/forecast/archive_preview' in frontend
    assert 'archive_sample_count' in panel and 'retained_sample_count' in panel and 'active_after_count' in panel


def test_predictive_preflight_and_audit_are_wired_for_all_four_resources():
    source = (MODULE / "preflight.py").read_text(encoding="utf-8")
    assert 'for metric in ("time", "battery", "clean_water", "dirty_water")' in source
    assert '"forecast": forecast_checks' in source
    for code in (
        "forecast_time_insufficient", "forecast_battery_insufficient",
        "forecast_clean_water_insufficient", "forecast_dirty_water_insufficient",
    ):
        assert code in source
    assert '"untrained_or_unavailable"' in source


def test_forecast_storage_is_separate_generation_scoped_and_archivable():
    source = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    assert '.statistics.models' in source
    assert 'forecast_models' in source and '_forecast_branches' in source
    assert 'async_archive_forecast_model' in source
    assert 'async_restore_forecast_archive' in source
    assert 'async_delete_forecast_archive' in source
    assert 'forecast_archive_preview' in source


def test_localization_catalogs_remain_parallel():
    en = json.loads((MODULE / "frontend" / "localization" / "en.json").read_text(encoding="utf-8"))
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    assert set(en) == set(ru)
    for key in (
        "panel.forecast_tab", "panel.forecasting", "panel.use_forecast", "panel.forecast_period_days",
        "panel.archive_model_preview", "common.blocker.forecast_time_insufficient",
        "common.blocker.forecast_battery_insufficient", "common.blocker.forecast_clean_water_insufficient",
        "common.blocker.forecast_dirty_water_insufficient",
    ):
        assert en.get(key) and ru.get(key)


def test_legacy_naive_generation_timestamp_does_not_break_forecast_filtering():
    aware_now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    branch = new_active_branch(now=aware_now - timedelta(days=30), samples=[
        _sample(aware_now - timedelta(days=2), 10, job="recent"),
    ])
    # Simulate a generation boundary bootstrapped from old statistics that did
    # not carry an explicit UTC offset.
    branch["started_at"] = "2026-08-20T12:00:00"
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=10, lookback_days=30)
    rows = filtered_samples(branch, policy, now=aware_now)
    assert [row["job_id"] for row in rows] == ["recent"]


def test_forecast_job_table_uses_effective_zone_fallback_like_scheduler():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=90, delta_percent=10, lookback_days=90)
    params = {"cleaning_mode": "vacuum_mop", "passes": 2, "fan_speed": "turbo"}
    # Only one direct sample for schedule s1, but three matching successful zone
    # samples exist. Two of them are PARTIAL-like samples with no job_value.
    direct = _sample(now - timedelta(days=1), 4, job="direct", zone="bath", params=params)
    rows = [direct]
    for i, value in enumerate((3, 5), start=2):
        row = _sample(now - timedelta(days=i), value, job=f"zone-{i}", zone="bath", params=params)
        row["job_value"] = None
        rows.append(row)
    tables = model_tables(rows, policy, minimum_samples=3)
    job = tables["by_schedule"][0]
    effective = estimate_from_samples(rows, zone_ids=["bath"], cleaning_params=params, policy=policy, minimum_samples=3)
    assert job["direct_sample_count"] == 1
    assert job["trained"] is True
    assert job["basis"] == "zone_composite_and_parameters"
    assert job["sample_count"] == 3
    assert job["raw_percentile_value"] == effective["raw_percentile_value"]
    assert job["forecast_value"] == effective["forecast_value"]


def test_forecast_frontend_uses_shared_table_appearance_and_keeps_mobile_cards():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert '<col class="forecast-col-object">' not in panel
    assert '.forecast-model-table{width:100%;min-width:0;table-layout:fixed}' not in panel
    assert 'table { width:100%; border-collapse:collapse;' in panel
    assert 'th.metric-header,td.metric-number { text-align:right;' in panel
    assert '@media (max-width:760px)' in panel
    assert '.mobile-card-table{display:block!important;width:100%!important;min-width:0!important' in panel
    assert 'class="mobile-card-table forecast-model-table"' in panel
    assert 'panel.forecast_direct_data_count' in panel
    assert 'panel.forecast_basis_zone_composite_and_parameters' in panel
