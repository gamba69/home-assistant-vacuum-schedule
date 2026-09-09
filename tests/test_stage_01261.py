"""Forecast profile-isolation fixes for Vacuum Schedule 0.13.0."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(MODULE))

from forecast_models import ForecastPolicyConfig, estimate_from_samples, model_tables, parameter_signature  # noqa: E402


def _sample(now, value, *, job, params, schedule="living", zones=("living", "balcony")):
    each = value / len(zones)
    return {
        "sample_id": job,
        "job_id": job,
        "schedule_id": schedule,
        "schedule_name": "Гостиная",
        "at": now.isoformat(),
        "result": "SUCCESS",
        "params": dict(params),
        "job_value": float(value),
        "zone_ids": list(zones),
        "zones": [
            {"zone_id": zone, "zone_name": zone, "value": each, "result": "SUCCESS"}
            for zone in zones
        ],
    }


def test_same_zones_must_not_mix_materially_different_time_profiles():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=80, delta_percent=5, lookback_days=20)
    dry = {"cleaning_mode": "vacuum", "passes": 1, "fan_mode": "turbo"}
    wet = {"cleaning_mode": "vacuum_mop", "passes": 2, "fan_mode": "turbo", "mop_mode": "standard", "water_mode": "weak"}
    other = {"cleaning_mode": "vacuum_mop", "passes": 1, "fan_mode": "quiet", "mop_mode": "standard", "water_mode": "weak"}
    samples = [
        _sample(now - timedelta(hours=1), 1200, job="dry-1", params=dry),
        _sample(now - timedelta(hours=2), 2400, job="wet-1", params=wet),
        _sample(now - timedelta(hours=3), 2500, job="wet-2", params=wet),
    ]
    samples += [_sample(now - timedelta(hours=4+i), 1800+i*30, job=f"other-{i}", params=other) for i in range(8)]

    dry_est = estimate_from_samples(samples, zone_ids=("living", "balcony"), cleaning_params=dry, policy=policy, metric="time")
    wet_est = estimate_from_samples(samples, zone_ids=("living", "balcony"), cleaning_params=wet, policy=policy, metric="time")
    assert dry_est["available"] is False
    assert dry_est["basis"] == "insufficient_matching_profile"
    assert dry_est["sample_count"] == 1
    assert wet_est["available"] is False
    assert wet_est["basis"] == "insufficient_matching_profile"
    assert wet_est["sample_count"] == 2

    tables = model_tables(samples, policy, minimum_samples=3, metric="time")
    rows = {tuple(sorted(row["profile_params"].items())): row for row in tables["by_schedule"]}
    dry_row = rows[tuple(sorted(parameter_signature(dry, "time")))]
    wet_row = rows[tuple(sorted(parameter_signature(wet, "time")))]
    assert dry_row["sample_count"] == 1 and dry_row["direct_sample_count"] == 1 and dry_row["trained"] is False
    assert wet_row["sample_count"] == 2 and wet_row["direct_sample_count"] == 2 and wet_row["trained"] is False
    assert dry_row["forecast_value"] is None and wet_row["forecast_value"] is None



def test_battery_forecast_also_refuses_parameter_agnostic_same_zone_fallback():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=80, delta_percent=5, lookback_days=20)
    dry = {"cleaning_mode": "vacuum", "passes": 1, "fan_mode": "turbo"}
    wet = {"cleaning_mode": "vacuum_mop", "passes": 2, "fan_mode": "turbo", "mop_mode": "standard", "water_mode": "weak"}
    samples = [_sample(now - timedelta(hours=1), 9, job="dry", params=dry)]
    samples += [_sample(now - timedelta(hours=2+i), 20+i, job=f"wet-{i}", params=wet) for i in range(5)]
    estimate = estimate_from_samples(samples, zone_ids=("living", "balcony"), cleaning_params=dry, policy=policy, metric="battery")
    assert estimate["available"] is False
    assert estimate["basis"] == "insufficient_matching_profile"
    assert estimate["sample_count"] == 1

def test_matching_profiles_train_independently_instead_of_sharing_zone_distribution():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    policy = ForecastPolicyConfig(enabled=True, percentile=80, delta_percent=5, lookback_days=20)
    dry = {"cleaning_mode": "vacuum", "passes": 1, "fan_mode": "turbo"}
    wet = {"cleaning_mode": "vacuum_mop", "passes": 2, "fan_mode": "turbo", "mop_mode": "standard", "water_mode": "weak"}
    samples = []
    for i, value in enumerate((1000, 1100, 1200)):
        samples.append(_sample(now - timedelta(hours=i), value, job=f"dry-{i}", params=dry))
    for i, value in enumerate((2400, 2600, 2800), start=4):
        samples.append(_sample(now - timedelta(hours=i), value, job=f"wet-{i}", params=wet))

    dry_est = estimate_from_samples(samples, zone_ids=("living", "balcony"), cleaning_params=dry, policy=policy, metric="time")
    wet_est = estimate_from_samples(samples, zone_ids=("living", "balcony"), cleaning_params=wet, policy=policy, metric="time")
    assert dry_est["available"] and wet_est["available"]
    assert dry_est["basis"] == wet_est["basis"] == "zones_and_parameters"
    assert dry_est["sample_count"] == wet_est["sample_count"] == 3
    assert float(dry_est["forecast_value"]) < float(wet_est["forecast_value"])


def test_old_dry_snapshots_with_stale_mop_fields_merge_with_current_dry_profile_and_missing_passes_means_one():
    old = {"cleaning_mode": "vacuum", "fan_mode": "turbo", "mop_mode": "standard", "water_mode": "weak"}
    current = {"cleaning_mode": "vacuum", "fan_mode": "turbo", "passes": 1}
    assert parameter_signature(old, "time") == parameter_signature(current, "time")
    assert parameter_signature(old, "battery") == parameter_signature(current, "battery")


def test_frontend_forecast_signature_ignores_fields_hidden_by_effective_cleaning_mode():
    panel = MODULE / "frontend" / "panel.js"
    script = f"""
const fs=require('fs');global.HTMLElement=class {{attachShadow(){{this.shadowRoot={{innerHTML:''}};return this.shadowRoot;}}}};
const byName=new Map();global.customElements={{get:(n)=>byName.get(n),define:(n,c)=>byName.set(n,c)}};
eval(fs.readFileSync({str(panel)!r},'utf8'));
const C=byName.get('vacuum-schedule-panel-0132'); const x=new C();
x._language='ru-RU'; x._translations={{}}; x._fallbackTranslations={{}};
const old={{cleaning_mode:'vacuum',fan_mode:'turbo',mop_mode:'standard',water_mode:'weak'}};
const current={{cleaning_mode:'vacuum',fan_mode:'turbo',passes:1}};
if(x._forecastParameterSignature(old,'time')!==x._forecastParameterSignature(current,'time')) throw new Error('dry signature mismatch');
const mopOld={{cleaning_mode:'mop',fan_mode:'turbo',cleaning_route:'fast',mop_mode:'standard',water_mode:'weak',passes:1}};
const mopCurrent={{cleaning_mode:'mop',mop_mode:'standard',water_mode:'weak',passes:1}};
if(x._forecastParameterSignature(mopOld,'time')!==x._forecastParameterSignature(mopCurrent,'time')) throw new Error('mop signature mismatch');
"""
    subprocess.run(["node", "-e", script], check=True)


def test_localizations_include_matching_profile_insufficient_label():
    for lang in ("en", "ru", "uk"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert data.get("panel.forecast_basis_matching_profile_insufficient")
