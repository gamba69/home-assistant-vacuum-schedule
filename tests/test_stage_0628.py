"""Regression tests for recipient presence is localized badge not raw state text, presence badges reuse shared chip geometry and theme semantics, and custom zone presence maps to away like backend routing.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_recipient_presence_is_localized_badge_not_raw_state_text():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_notificationPresenceMeta(rawState)' in panel
    assert 'this._tr("panel.home")' in panel
    assert 'this._tr("panel.away")' in panel
    assert 'this._tr("panel.unknown_12049ad")' in panel
    assert 'this._tr("panel.unavailable")' in panel
    assert 'this._notificationPresenceBadgeHtml(presenceState)' in panel
    assert '${r.presence_entity_id} · ${presenceState}' not in panel


def test_presence_badges_reuse_shared_chip_geometry_and_theme_semantics():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="ui-chip status-badge presence-badge presence-${meta.tone}"' in panel
    assert '.presence-home { color:var(--vs-success-quiet-on); background:var(--vs-success-quiet-fill); }' in panel
    assert '.presence-away { color:var(--vs-neutral-quiet-on); background:var(--vs-neutral-quiet-fill); }' in panel
    assert '.presence-unknown { color:var(--vs-warning-quiet-on); background:var(--vs-warning-quiet-fill); }' in panel
    assert '.presence-unavailable { color:var(--vs-danger-quiet-on); background:var(--vs-danger-quiet-fill); }' in panel
    # No independent geometry is allowed on the presence modifiers.
    for selector in ('.presence-home {', '.presence-away {', '.presence-unknown {', '.presence-unavailable {'):
        rule = panel.split(selector, 1)[1].split('}', 1)[0]
        for forbidden in ('padding:', 'border-radius:', 'font-size:', 'font-weight:', 'line-height:'):
            assert forbidden not in rule


def test_custom_zone_presence_maps_to_away_like_backend_routing():
    panel = PANEL.read_text(encoding="utf-8")
    # Only home, unavailable and unknown are explicit branches; every other
    # concrete HA state (not_home or custom zone) becomes Away.
    helper = panel.split('_notificationPresenceMeta(rawState)', 1)[1].split('_notificationPresenceBadgeHtml', 1)[0]
    assert 'if(state==="home")' in helper
    assert 'if(state==="unavailable")' in helper
    assert 'state==="unknown"' in helper
    assert 'return {label:this._tr("panel.away"),tone:"away"};' in helper
