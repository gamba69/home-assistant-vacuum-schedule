"""Regression tests for requested banner wording and percentile notation, statistics url context is resolved before view specific load, and hass connection replacement reloads and resubscribes.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
RU = MODULE / "frontend" / "localization" / "ru.json"
EN = MODULE / "frontend" / "localization" / "en.json"




def test_requested_banner_wording_and_percentile_notation():
    ru = RU.read_text(encoding="utf-8")
    en = EN.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert '"panel.schedule_execution_disabled": "Выполнение расписания отключено"' in ru
    assert '"panel.execution_disabled_banner_text": "Автоматический запуск заданий отключён"' in ru
    assert '"panel.percentile_90": "P₉₀"' in ru
    assert '"panel.percentile_90": "P₉₀"' in en
    assert '_formatPercentileLabel(' in panel


def test_statistics_url_context_is_resolved_before_view_specific_load():
    panel = PANEL.read_text(encoding="utf-8")
    load_start = panel.index("  async _load(force = false) {")
    load_end = panel.index("\n  _syncLoadingIndicator()", load_start)
    body = panel[load_start:load_end]
    context = body.index("if (await this._applyLocationContext()) return;")
    statistics = body.index('if (this._view === "statistics") {')
    maintenance = body.index('if (this._view === "maintenance") await this._loadMaintenance(false);')
    assert context < statistics
    assert context < maintenance


def test_hass_connection_replacement_reloads_and_resubscribes():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  set hass(value) {")
    end = panel.index("\n  set narrow(value)", start)
    body = panel[start:end]
    assert "previousConnection !== this._hassConnection" in body
    assert "if (connectionChanged && this._unsubscribePush)" in body
    assert "this._unsubscribePush = null;" in body
    assert "this._load(true);" in body
    assert "if (first || connectionChanged) this._subscribePush();" in body


