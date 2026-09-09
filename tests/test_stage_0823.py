"""Regression contracts for Vacuum Schedule 0.9.0 zone-status visual cleanup."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_cleaning_zone_runtime_states_use_quiet_status_dots():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="quiet-status quiet-status-${tone} zone-live-state zone-live-${tone}"' in panel
    assert 'class="ui-chip status-badge zone-live-state' not in panel
    assert '.zone-live-state { font:inherit; font-weight:400; color:var(--primary-text-color); }' in panel
    assert '.quiet-status-good .quiet-status-dot' in panel
    assert '.quiet-status-warning .quiet-status-dot' in panel
    assert '.quiet-status-bad .quiet-status-dot' in panel
    assert '.zone-live-neutral { color:var(--secondary-text-color); }' in panel
    zone_css = panel[panel.index('.zone-live-state {'):panel.index('.room-actions {')]
    for forbidden in ('background:', 'border:', 'border-radius:', 'padding:'):
        assert forbidden not in zone_css

def test_zone_countdown_keeps_semantic_color_transition_without_badge_chrome():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'el.classList.toggle("zone-live-warning", remaining > 0);' in panel
    assert 'el.classList.toggle("zone-live-good", remaining <= 0);' in panel
