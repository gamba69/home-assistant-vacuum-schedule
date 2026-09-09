"""Regression tests for all status badge families use one base component, status badge geometry is defined once, and zone live states are plain text not badges.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_all_status_badge_families_use_one_base_component():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        'class="ui-chip status-badge state-chip ',
        'class="ui-chip status-badge support support-',
        'class="ui-chip status-badge search-select-badge"',
    ):
        assert token in panel


def test_status_badge_geometry_is_defined_once():
    panel = PANEL.read_text(encoding="utf-8")
    base = '.ui-chip { display:inline-flex; align-items:center; gap:5px; width:max-content; max-width:100%; padding:4px 7px; border-radius:10px; font-size:11px; line-height:1.2; font-weight:700;'
    assert base in panel
    # Component-specific status selectors may define semantic colors only, not their own geometry.
    for forbidden in (
        '.state-chip { display:',
        '.state-chip { border-radius:',
        '.result-chip { display:',
        '.result-chip { border-radius:',
        '.support { font-size:',
        '.support { border-radius:',
        '.zone-live-state { display:',
        '.zone-live-state { border-radius:',
        '.search-select-badge { padding:',
        '.search-select-badge { border-radius:',
        '.status-badge { padding:',
        '.status-badge { border-radius:',
    ):
        assert forbidden not in panel


def test_zone_live_states_are_plain_text_not_badges():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="quiet-status quiet-status-${tone} zone-live-state zone-live-${tone}"' in panel
    assert 'class="ui-chip status-badge zone-live-state' not in panel
    assert '.quiet-status-good .quiet-status-dot { background:var(--success-color,var(--vs-success-quiet-on)); }' in panel
    assert '.quiet-status-bad .quiet-status-dot { background:var(--error-color,var(--vs-danger-quiet-on)); }' in panel
