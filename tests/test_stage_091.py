"""Global attention classification contracts for Vacuum Schedule 0.9.3."""
from pathlib import Path
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"


def _load_preflight_models():
    spec = importlib.util.spec_from_file_location("vs_preflight_models_091", MODULE / "preflight_models.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module




def test_zone_wait_is_not_global_attention():
    models = _load_preflight_models()
    scope, attention = models.classify_blocker("zone_access_blocked", "zone.kitchen.accessible", "kitchen")
    assert scope.value == "ZONE"
    assert attention.value == "NONE"
    scope, attention = models.classify_blocker("zone_busy", "zone.kitchen.busy", "kitchen")
    assert scope.value == "ZONE"
    assert attention.value == "NONE"


def test_water_and_physical_intervention_are_global_attention_candidates():
    models = _load_preflight_models()
    for code, source in (
        ("clean_water_insufficient", "dock.clean_water"),
        ("dirty_water_full", "dock.dirty_water"),
        ("detergent_unavailable", "dock.detergent"),
        ("mop_not_attached", "mop.attached"),
    ):
        scope, attention = models.classify_blocker(code, source)
        assert scope.value == "ROBOT"
        assert attention.value == "ACTION_REQUIRED"
    scope, attention = models.classify_blocker("vacuum_error", "vacuum.activity")
    assert scope.value == "ROBOT"
    assert attention.value == "CRITICAL"


def test_normal_auto_resolving_waits_stay_out_of_global_attention():
    models = _load_preflight_models()
    for code, source in (
        ("vacuum_busy", "vacuum.activity"),
        ("battery_insufficient", "vacuum.battery_percent"),
        ("dnd_active", "dnd.active"),
        ("dock_unavailable", "dock.available"),
    ):
        _scope, attention = models.classify_blocker(code, source)
        assert attention.value == "NONE"


def test_blocker_payload_exposes_scope_and_attention():
    models = _load_preflight_models()
    blocker = models.Blocker(
        code="clean_water_insufficient",
        decision_class=models.PreflightDecision.WAIT,
        source_key="dock.clean_water",
        scope=models.BlockerScope.ROBOT,
        attention=models.BlockerAttention.ACTION_REQUIRED,
    )
    data = blocker.to_dict()
    assert data["scope"] == "ROBOT"
    assert data["attention"] == "ACTION_REQUIRED"


def test_panel_promotes_only_attention_runtime_blockers_not_generic_wait():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("  _globalNoticeItems()")
    end = panel.index("  _systemBannerRowHtml", start)
    notices = panel[start:end]
    assert "_runtimeAttentionBlockers(runtimeStatus)" in notices
    assert 'attention==="ACTION_REQUIRED"' in panel
    assert 'attention==="CRITICAL"' in panel
    assert "runtimeStatus.can_start_now===false" not in notices
    assert 'panel.current_start_blocked' not in notices


def test_station_service_and_pending_confirmation_are_global_notices():
    panel = PANEL.read_text(encoding="utf-8")
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert '_resourceNeedsUserService("dock.clean_water")' in panel
    assert '_resourceNeedsUserService("dock.dirty_water")' in panel
    assert 'item.live_available===true' in panel
    assert 'item.live===false' in panel
    assert 'panel.station_service_required' in panel
    assert 'pendingMaintenance' in panel
    assert 'action:"maintenance"' in panel
    assert '"maintenance": dict(water_attention.get("maintenance") or {})' in frontend


def test_global_execution_gate_and_configuration_attention_remain_banners():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'gate==="disabled"' in panel
    assert 'gate==="disabled_until"' in panel
    assert 'panel.schedule_execution_disabled' in panel
    assert 'panel.configuration_requires_attention' in panel


def test_attention_localization_is_complete():
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        for key in (
            "panel.action_required",
            "panel.critical_attention",
            "panel.station_service_required",
            "panel.clean_water_service_required",
            "panel.dirty_water_service_required",
            "panel.detergent_service_required",
            "panel.execution_disabled_banner_text",
            "panel.execution_disabled_until_banner_text",
            "panel.configuration_requires_attention",
            "panel.configuration_error_count",
        ):
            assert data[key] and data[key] != key
