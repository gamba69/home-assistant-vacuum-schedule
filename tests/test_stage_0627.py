"""Regression tests for buttons use home assistant semantic theme tokens not translucent, panel surfaces controls and focus follow ha theme, and semantic badges and status surfaces use ha semantic palette.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_buttons_use_home_assistant_semantic_theme_tokens_not_translucent_mix():
    panel = PANEL.read_text(encoding="utf-8")
    assert "--vs-brand-fill:var(--wa-color-brand-fill-normal,var(--ha-color-fill-primary-normal-resting,var(--primary-color)))" in panel
    assert "--vs-neutral-fill:var(--wa-color-neutral-fill-normal,var(--ha-color-fill-neutral-normal-resting,var(--secondary-background-color)))" in panel
    assert "--vs-danger-fill:var(--wa-color-danger-fill-normal,var(--ha-color-fill-danger-normal-resting,var(--error-color)))" in panel
    assert "button.primary { color:var(--vs-brand-on); background:var(--vs-brand-fill); border-color:transparent; }" in panel
    assert "button.ghost { color:var(--vs-neutral-on); background:var(--vs-neutral-fill); border-color:transparent; }" in panel
    assert "button.danger { color:var(--vs-danger-on); background:var(--vs-danger-fill);" in panel
    assert "button:disabled { opacity:1;" in panel
    assert "box-shadow:var(--ha-button-box-shadow,none)" in panel
    assert "min-height:var(--ha-button-height,36px)" in panel
    assert "color-mix" not in panel


def test_panel_surfaces_controls_and_focus_follow_ha_theme():
    panel = PANEL.read_text(encoding="utf-8")
    assert "--vs-card-surface:var(--ha-card-background,var(--card-background-color))" in panel
    assert "--vs-control-surface:var(--wa-form-control-background-color,var(--input-fill-color,var(--vs-raised-surface)))" in panel
    assert "--vs-control-border:var(--wa-form-control-border-color,var(--input-outlined-idle-border-color,var(--divider-color)))" in panel
    assert "background:var(--vs-card-surface); border-radius:var(--ha-card-border-radius,12px);" in panel
    assert "background:var(--vs-control-surface); border:1px solid var(--vs-control-border);" in panel
    assert "outline:2px solid var(--ha-color-focus,var(--primary-color))" in panel
    assert "color-scheme:dark light" not in panel
    assert "box-shadow:var(--dialog-box-shadow,var(--ha-dialog-box-shadow,var(--ha-card-box-shadow,none)))" in panel
    assert "backdrop-filter:var(--ha-dialog-scrim-backdrop-filter,none)" in panel


def test_semantic_badges_and_status_surfaces_use_ha_semantic_palette():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        "--vs-success-quiet-fill:var(--ha-color-fill-success-quiet-resting",
        "--vs-warning-quiet-fill:var(--ha-color-fill-warning-quiet-resting",
        "--vs-danger-quiet-fill:var(--ha-color-fill-danger-quiet-resting",
        ".state-WAIT { background:var(--vs-warning-quiet-fill); color:var(--vs-warning-quiet-on); }",
        ".state-RUNNING { background:var(--vs-success-quiet-fill); color:var(--vs-success-quiet-on); }",
        ".quiet-status-good .quiet-status-dot { background:var(--success-color,var(--vs-success-quiet-on)); }",
        ".quiet-status-bad .quiet-status-dot { background:var(--error-color,var(--vs-danger-quiet-on)); }",
    ):
        assert token in panel
    for hardcoded in ("#43a047", "#9e9e9e", "#f9a825", "#db4437", "#039be5"):
        assert hardcoded not in panel
