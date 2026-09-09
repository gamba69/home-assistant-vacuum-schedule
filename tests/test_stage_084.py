"""Regression contracts for Vacuum Schedule 0.8.12 mobile HA navigation."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_narrow_property_drives_menu_rendering():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'set narrow(value)' in panel
    assert 'this.toggleAttribute("narrow", narrow);' in panel
    assert '<button type="button" class="app-bar-menu"' in panel
    assert ':host(:not([narrow])) .app-bar-menu { display:none!important; }' in panel


def test_menu_uses_native_home_assistant_toggle_event():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })' in panel
    assert 'appBarMenu.addEventListener("click", () => this._toggleHomeAssistantMenu())' in panel


def test_mobile_menu_has_touch_target_and_safe_area_support():
    panel = PANEL.read_text(encoding="utf-8")
    assert '.app-bar-menu { width:48px; min-width:48px; height:48px;' in panel
    assert 'env(safe-area-inset-left)' in panel
    assert 'env(safe-area-inset-right)' in panel


def test_menu_label_is_localized_in_both_catalogs():
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        assert data["panel.open_home_assistant_menu"]
