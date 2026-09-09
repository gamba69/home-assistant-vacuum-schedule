"""Regression coverage for 0.12.45 settings-card ordering."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_interface_language_card_is_last_settings_card():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _settingsHtml()")
    end = panel.index("  _overrideInputLabel", start)
    settings = panel[start:end]
    language = settings.rindex('class="entry-card interface-language-card"')
    data = settings.rindex("${this._dataManagementHtml(d.data_management)}")
    assert language > data
    assert settings.count('class="entry-card interface-language-card"') == 1
