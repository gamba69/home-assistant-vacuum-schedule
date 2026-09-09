"""Regression contracts for Vacuum Schedule 0.8.3 active-job grid alignment."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_active_job_action_column_has_one_fixed_track_for_header_and_rows():
    panel = PANEL.read_text()
    assert '.job-list { --job-actions-column:124px;' in panel
    assert 'grid-template-columns:minmax(0,1.05fr) minmax(250px,1.65fr) minmax(220px,1.45fr) minmax(0,1fr) minmax(0,1fr) minmax(0,.85fr) minmax(78px,.65fr) var(--job-actions-column); gap:12px; align-items:center;' in panel
    assert 'grid-template-columns:1.35fr 1fr .85fr 1.15fr 1fr auto' not in panel


def test_tablet_active_job_grid_does_not_reintroduce_auto_action_width():
    panel = PANEL.read_text()
    assert 'grid-template-columns:minmax(0,1fr) minmax(0,1.45fr) minmax(0,1.35fr) minmax(0,1fr) minmax(78px,.7fr) var(--job-actions-column)' in panel
    assert '.job-list-header,.job-summary{grid-template-columns:1.3fr 1fr 1fr auto}' not in panel


def test_action_cell_stays_right_aligned_inside_fixed_track():
    panel = PANEL.read_text()
    assert '.job-row-actions { display:flex; align-items:center; justify-content:flex-end;' in panel
    assert '.job-actions-header { text-align:right; }' in panel
