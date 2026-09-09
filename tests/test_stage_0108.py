"""Internal dispatcher transport contracts for Vacuum Schedule 0.10.10."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_scheduler_updates_use_compact_dispatcher_payloads_not_ha_events():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    emit_job = source[source.index("def _emit_job"):source.index("def _emit_scheduler")]
    emit_scheduler = source[source.index("def _emit_scheduler"):]
    assert "async_dispatcher_send(" in emit_job
    assert "SIGNAL_JOB_UPDATED" in emit_job
    assert '"job_id": job.job_id' in emit_job
    assert "job.to_dict()" not in emit_job
    assert "hass.bus.async_fire" not in emit_job
    assert "async_dispatcher_send(" in emit_scheduler
    assert "SIGNAL_SCHEDULER_UPDATED" in emit_scheduler
    assert "status_payload()" not in emit_scheduler
    assert "hass.bus.async_fire" not in emit_scheduler


def test_removed_oversized_internal_event_names_from_production_code():
    for path in MODULE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert '"vacuum_schedule_job_updated"' not in text
        assert '"vacuum_schedule_scheduler_updated"' not in text


def test_websocket_push_uses_dispatcher_not_event_bus():
    source = (MODULE / "frontend.py").read_text(encoding="utf-8")
    block = source[source.index("async def websocket_scheduler_subscribe"):source.index("async def websocket_statistics_get")]
    assert "async_dispatcher_connect" in block
    assert "SIGNAL_JOB_UPDATED" in block
    assert "SIGNAL_SCHEDULER_UPDATED" in block
    assert "hass.bus.async_listen" not in block
    assert '"job_updated"' in block
    assert '"scheduler_updated"' in block


def test_scheduler_sensors_and_maintenance_use_dispatcher():
    sensor = (MODULE / "sensor.py").read_text(encoding="utf-8")
    engine_sensor = sensor[sensor.index("class _VacuumScheduleEngineSensor"):sensor.index("class VacuumScheduleEngineStateSensor")]
    assert "async_dispatcher_connect" in engine_sensor
    assert "SIGNAL_SCHEDULER_UPDATED" in engine_sensor
    assert "hass.bus.async_listen" not in engine_sensor

    water = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    attention = water[water.index("def _fire_attention_updated"):water.index("def _resource_ok")]
    assert "async_dispatcher_send" in attention
    assert "SIGNAL_SCHEDULER_UPDATED" in attention
    assert "hass.bus.async_fire" not in attention
