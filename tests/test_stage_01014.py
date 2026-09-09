"""Regression tests for wait reason table has percentage share, statistics secondary rows are right and top aligned, and zone p90 column has lighter distinct background.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_wait_reason_table_has_percentage_share():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'const waitReasonTotal=waitReasonEntries.reduce' in panel
    assert 'statistics-wait-reasons-table' in panel
    assert '<th class="metric-header">${this._tr("panel.share")}</th>' in panel
    assert '/waitReasonTotal*100' in panel


def test_statistics_secondary_rows_are_right_and_top_aligned():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'align-content:start; align-items:start;' in panel
    assert 'overflow-wrap:anywhere; text-align:right;' in panel


def test_statistics_profile_table_uses_shared_table_appearance():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.statistics-profile-table td.percentile-column' not in panel
    assert 'table { width:100%; border-collapse:collapse;' in panel
    assert 'th.metric-header,td.metric-number { text-align:right;' in panel


def test_charge_rate_details_are_separate_rows():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="statistics-card-note statistics-charge-detail"' in panel
    assert 'class="statistics-card-note statistics-charge-explanation"' in panel
    assert '${this._escape(chargeDetail)}</small><small' in panel
    assert '${chargeSourceHelp}</small>' in panel


def test_dirty_water_shows_filled_litre_equivalent_and_separate_metadata_rows():
    panel = PANEL.read_text(encoding="utf-8")
    assert '${fmt(dirty.filled_percent)}% · ${liters(dirty.estimate_ml_eq)}' in panel
    assert 'statistics-water-free' in panel
    assert 'statistics-water-confidence' in panel
    assert '${this._tr("panel.state_confidence")}: ${confidence(dirty.state_confidence)}' in panel


