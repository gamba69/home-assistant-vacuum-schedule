"""Async callback thread-safety contracts for Vacuum Schedule 0.10.10."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"




def test_water_state_callback_is_explicitly_loop_safe():
    source = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    assert "from homeassistant.core import HomeAssistant, callback" in source
    block = source[source.index("async def _async_handle(event)"):source.index("self._unsub = async_track_state_change_event", source.index("async def _async_handle(event)"))]
    assert "@callback\n        def _handle(event)" in block
    assert "self.hass.async_create_task(_async_handle(event))" in block


def test_scheduler_watchdog_callback_is_explicitly_loop_safe():
    source = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    needle = "@callback\n    def _watchdog_callback(self, _now: datetime) -> None:"
    assert needle in source
    block = source[source.index(needle):source.index("def _clear_timers", source.index(needle))]
    assert "self.hass.async_create_task(self.async_reconcile())" in block


def test_other_registered_create_task_callbacks_remain_marked_callback():
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    for signature in (
        "def _zone_input_changed(_event: Any) -> None:",
        "def _changed(_event: Any) -> None:",
        "def _mobile_action(event: Any) -> None:",
        "def _telegram_action(event: Any) -> None:",
        "def _due(_real_now: datetime) -> None:",
    ):
        idx = engine.index(signature)
        prefix = engine[max(0, idx - 40):idx]
        assert "@callback" in prefix

    charging = (MODULE / "charging_statistics.py").read_text(encoding="utf-8")
    for signature in (
        "def _state_changed(_event: Event[EventStateChangedData]) -> None:",
        "def _interval_callback(self, _now: datetime) -> None:",
    ):
        idx = charging.index(signature)
        assert "@callback" in charging[max(0, idx - 40):idx]

    dependency = (MODULE / "dependency_index.py").read_text(encoding="utf-8")
    idx = dependency.index("def _state_changed(event: Event[EventStateChangedData]) -> None:")
    assert "@callback" in dependency[max(0, idx - 40):idx]
