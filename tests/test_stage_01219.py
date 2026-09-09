"""Regression tests for one-second live progress interpolation.

The UI must extrapolate area, percent and ETA only between fresh real HA anchors, while backend rates remain derived exclusively from observed cleaning samples.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
ENGINE = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")


def test_backend_exposes_observation_anchored_interpolation_rates():
    assert '"interpolation": {' in ENGINE
    assert '"anchor_at": observation.get("observed_at")' in ENGINE
    assert '"area_anchor_at": area_rate.anchor_at.isoformat()' in ENGINE
    assert '"percent_anchor_at": percent_rate.anchor_at.isoformat()' in ENGINE
    assert '"max_age_seconds": 12' in ENGINE
    assert 'robust_observed_rate' in ENGINE


def test_frontend_interpolates_only_during_floor_cleaning_and_caps_staleness():
    assert 'const floorCodes=new Set(["cleaning","cleaning_zone","vacuuming","mopping","vacuuming_and_mopping","spot_cleaning"]);' in PANEL
    assert 'age<=Math.max(1,maxAge)' in PANEL
    assert 'Math.min(areaRate*areaAge,maxAreaIncrement)' in PANEL
    assert 'Math.min(interpolatedPercentRate*percentAge,maxPercentIncrement)' in PANEL


def test_frontend_marks_interpolated_values_and_uses_stable_forecast_eta():
    assert 'areaEstimated?"≈":""' in PANEL
    assert 'estimated?"≈":""' in PANEL
    assert 'live.forecast?.eta_at' in PANEL
    assert '(100-progress)/progressRate' not in PANEL
    assert 'setInterval(()=>this._updateActiveNowCells(),1000)' in PANEL
