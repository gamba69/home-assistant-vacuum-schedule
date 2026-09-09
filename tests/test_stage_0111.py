"""Regression tests for statistics and forecast are loaded separately, water forecast refresh is batched, and forecast get is async and reports failures.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_statistics_and_forecast_are_loaded_separately():
    manager = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    query_start = manager.index("    async def async_query(")
    query_end = manager.index("\n\n    async def _async_save_control", query_start)
    query_body = manager[query_start:query_end]
    assert 'payload["forecast"]' not in query_body
    assert 'f"{DOMAIN}/statistics/forecast/get"' in frontend
    assert 'type:"vacuum_schedule/statistics/forecast/get"' in panel
    assert 'if(this._statisticsTab==="forecast"' in panel


def test_water_forecast_refresh_is_batched():
    water = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    manager = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    assert "def job_usage_summaries(" in water
    refresh_start = manager.index("    def _refresh_water_samples")
    refresh_end = manager.index("\n    def _job_after_statistics_reset", refresh_start)
    refresh = manager[refresh_start:refresh_end]
    assert "job_usage_summaries" in refresh
    assert "job_usage_summary(job_id)" not in refresh




def test_forecast_get_is_async_and_reports_failures():
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    start = frontend.index("async def websocket_statistics_forecast_get")
    block = frontend[frontend.rfind("@websocket_api.websocket_command", 0, start):frontend.index("\n\n@websocket_api.websocket_command", start)]
    assert "@websocket_api.async_response" in block
    assert "async_add_executor_job" in block
    assert '"forecast_load_failed"' in block


def test_forecast_panel_has_explicit_error_and_retry_state():
    panel = (MODULE / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert "this._forecastLoading = false" in panel
    assert "this._forecastError = null" in panel
    assert 'panel.forecast_load_failed' in panel
    assert 'button.forecast-retry' in panel
