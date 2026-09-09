"""Maintenance workflow contracts for Vacuum Schedule 0.10.0."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
WATER = MODULE / "water_statistics.py"




def test_maintenance_is_operational_screen_not_statistics_workflow():
    panel = PANEL.read_text(encoding="utf-8")
    assert '_maintenanceStatusCardHtml()' in panel
    assert 'else if (this._view === "maintenance") content = this._maintenanceHtml();' in panel
    assert '["status", "view-dashboard-outline"' in panel
    assert '["maintenance",' not in panel  # subordinate route, not a top-level tab
    stats = panel[panel.index("  _statisticsWaterHtml"):panel.index("  _tabsHtml", panel.index("  _statisticsWaterHtml"))]
    assert "maintenance-confirm" not in stats
    assert "water-service" not in stats


def test_maintenance_has_dedicated_read_write_websockets():
    frontend = FRONTEND.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    assert 'f"{DOMAIN}/maintenance/get"' in frontend
    assert 'f"{DOMAIN}/maintenance/save"' in frontend
    assert 'websocket_maintenance_get' in frontend
    assert 'websocket_maintenance_save' in frontend
    assert 'type:"vacuum_schedule/maintenance/get"' in panel
    assert 'type:"vacuum_schedule/maintenance/save"' in panel


def test_detected_maintenance_survives_resource_recovery_until_confirmation():
    source = WATER.read_text(encoding="utf-8")
    assert 'elif previous is False and current is True:' in source
    assert 'signal="resource_recovered"' in source
    assert '"status": "pending"' in source
    assert '_fire_attention_updated("water_state_changed", operational=True)' in source
    panel = PANEL.read_text(encoding="utf-8")
    assert 'pendingMaintenance=this._settingsData?.attention?.maintenance?.pending_sessions||[]' in panel
    assert 'action:"maintenance"' in panel


def test_clean_and_dirty_detected_actions_merge_into_one_session():
    source = WATER.read_text(encoding="utf-8")
    assert '_MAINTENANCE_MERGE_SECONDS = 5 * 60' in source
    assert 'abs((returned_dt - other_dt).total_seconds()) <= _MAINTENANCE_MERGE_SECONDS' in source
    assert 'candidate.setdefault("detection", {}).setdefault("items", []).append' in source


def test_detection_fact_is_separate_from_editable_interpretation():
    source = WATER.read_text(encoding="utf-8")
    assert '"detection": {"items": []}' in source
    assert '"interpretations": {}' in source
    assert 'Detection facts, source and detected timestamps are immutable.' in source
    assert 'session.setdefault("revisions", []).append' in source
    assert 'session["interpretations"] =' in source


def test_editing_old_service_replays_meter_and_calibration():
    source = WATER.read_text(encoding="utf-8")
    assert 'def _rebuild_from_ledger(self)' in source
    assert 'self.calibration = dict(self.model_baseline or self._fresh_calibration())' in source
    assert 'self.model_epoch' in source
    assert 'self.cycles = []' in source
    assert 'self._rebuild_from_ledger()' in source
    assert 'observed_scale = capacity / base_usage' in source
    assert '_record_scale_observation' in source


def test_any_confirmed_history_entry_can_be_edited():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'confirmed.map(s=>this._maintenanceSessionCardHtml(s,false))' in panel
    assert 'class="ghost maintenance-edit"' in panel
    assert 'data-maintenance-session=' in panel
    assert 'panel.maintenance_recalculation_notice' in panel



def test_upgrade_recovers_unclassified_095_threshold_service():
    source = (MODULE / "storage_migrations.py").read_text(encoding="utf-8")
    water = WATER.read_text(encoding="utf-8")
    assert "def _recover_unconfirmed_service_after_upgrade" in water
    assert 'balance.get("last_anchor") != "sensor_threshold"' in source
    assert 'signal="startup_recovery_inferred"' in source

def test_maintenance_localization_is_complete_and_parallel():
    catalogs = {}
    for lang in ("en", "ru"):
        data = json.loads((MODULE / "frontend" / "localization" / f"{lang}.json").read_text(encoding="utf-8"))
        catalogs[lang] = data
        for key in (
            "panel.maintenance", "panel.open_maintenance", "panel.pending_maintenance",
            "panel.maintenance_history", "panel.confirm_maintenance", "panel.edit_maintenance",
            "panel.maintenance_recalculation_notice", "panel.just_removed_reinserted",
            "panel.unknown_service_action", "panel.maintenance_saved",
        ):
            assert data.get(key) and data[key] != key
    assert set(catalogs["en"]) == set(catalogs["ru"])
