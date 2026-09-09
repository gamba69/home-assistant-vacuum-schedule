"""Editor spacing polish contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_specific_dates_is_explicitly_bold():
    panel=PANEL.read_text(encoding="utf-8")
    assert '.specific-dates-label { font-weight:700!important;' in panel

def test_force_group_header_has_vertical_gap():
    panel=PANEL.read_text(encoding="utf-8")
    assert '.force-group-head { display:flex; align-items:center; justify-content:space-between; gap:9px; min-height:40px; margin-bottom:10px; }' in panel
