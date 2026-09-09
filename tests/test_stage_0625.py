"""Regression tests for all status badges use bold value typography.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_all_status_badges_use_bold_value_typography():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.ui-chip { display:inline-flex; align-items:center; gap:5px; width:max-content; max-width:100%; padding:4px 7px; border-radius:10px; font-size:11px; line-height:1.2; font-weight:700;' in panel
    assert '.ui-chip { display:inline-flex;' in panel
    # Badge families inherit weight from the single shared base style.
    for forbidden in (
        '.state-chip { font-weight:',
        '.result-chip { font-weight:',
        '.support { font-weight:',
        '.search-select-badge { font-weight:',
    ):
        assert forbidden not in panel
