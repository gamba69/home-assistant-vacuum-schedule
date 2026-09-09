"""Continuous dock charging statistics contracts for Vacuum Schedule 0.10.7."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_charging_rate_is_observed_outside_jobs_from_live_physical_inputs():
    text = (MODULE / "charging_statistics.py").read_text(encoding="utf-8")
    for marker in (
        "class ChargingStatistics",
        '"vacuum.battery_percent"',
        '"vacuum.charging"',
        '"vacuum.activity"',
        '"dock.robot_docked"',
        "allow_test_overrides=False",
        "async_track_state_change_event",
        "async_track_time_interval",
        '"continuous_dock"',
    ):
        assert marker in text


def test_charge_session_filters_sensor_noise_and_does_not_bridge_ha_downtime():
    text = (MODULE / "charging_statistics.py").read_text(encoding="utf-8")
    assert "_MIN_SESSION_GAIN_PERCENT = 1.0" in text
    assert "_MIN_SESSION_SECONDS = 60.0" in text
    assert 'reason="restart_boundary"' in text
    assert "Never bridge" in text


def test_statistics_manager_prefers_continuous_dock_rate_but_retains_job_fallback():
    text = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    assert "self.charging = ChargingStatistics" in text
    assert "await self.charging.async_start()" in text
    assert "await self.charging.async_stop()" in text
    assert 'battery["job_charge_rate_percent_per_minute"]' in text
    assert 'battery["charge_rate_source"] = "continuous_dock_observation"' in text
    assert 'battery["charge_rate_source"] = "job_observation_fallback"' in text


def test_statistics_ui_explains_current_observation_and_completed_sessions():
    panel = PANEL.read_text(encoding="utf-8")
    ru = (MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8")
    assert "activeCharge" in panel
    assert "panel.charge_sessions" in panel
    assert "panel.charge_observing_now" in panel
    assert "panel.charge_rate_continuous_help" in panel
    assert "Сеансов зарядки" in ru
    assert "Наблюдение сейчас" in ru
