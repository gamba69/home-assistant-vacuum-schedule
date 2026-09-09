"""Independent data layers, backup and purge contracts for 0.10.22."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'custom_components'/'vacuum_schedule'
MANAGER=MODULE/'statistics_manager.py'
STORE=MODULE/'statistics_store.py'
WATER=MODULE/'water_statistics.py'
JOBS=MODULE/'job_store.py'
FRONTEND=MODULE/'frontend.py'
PANEL=MODULE/'frontend'/'panel.js'




def test_four_independent_data_layers_and_automatic_safety_backups():
    source=MANAGER.read_text(encoding='utf-8')
    assert '_DATA_LAYERS = ("execution_history", "statistics", "models", "maintenance")' in source
    assert 'reason="before_clear"' in source
    assert 'reason="before_restore"' in source
    assert 'manifest.json' in source
    assert 'hashlib.sha256' in source
    assert 'vacuum_schedule_backups' in source


def test_statistics_reset_epoch_blocks_old_job_backfill_without_deleting_job_history():
    manager=MANAGER.read_text(encoding='utf-8')
    jobs=JOBS.read_text(encoding='utf-8')
    assert 'statistics_reset_at' in manager and 'statistics_epoch' in manager
    assert 'not self._job_after_statistics_reset(job)' in manager
    assert 'async_clear_execution_history' in jobs
    block=jobs[jobs.index('async def async_clear_execution_history'):jobs.index('async def async_restore_execution_history')]
    assert 'self.active' not in block
    assert 'self.history = []' in block and 'self.terminal_archive = []' in block and 'self.events = []' in block


def test_learned_models_are_separate_from_statistics_ledger():
    manager=MANAGER.read_text(encoding='utf-8')
    water=WATER.read_text(encoding='utf-8')
    assert '.statistics.models' in manager
    assert 'forecast_models' in manager and '_forecast_branches' in manager
    assert 'async_clear_model_layer' in water
    assert 'model_baseline' in water and 'model_epoch' in water
    assert 'Freeze learned coefficients before deleting their training facts.' in water
    assert 'self.calibration = dict(self.model_baseline or self._fresh_calibration())' in water


def test_statistics_and_maintenance_have_independent_water_purge_semantics():
    water=WATER.read_text(encoding='utf-8')
    stat=water[water.index('async def async_clear_statistics_layer'):water.index('async def async_restore_statistics_layer')]
    maintenance=water[water.index('async def async_clear_maintenance_history'):water.index('async def async_restore_maintenance_layer')]
    assert '{"FLOOR_MOP", "MOP_WASH"}' in stat
    assert 'self.maintenance_sessions' not in stat
    assert 'item.get("status") == "confirmed"' in maintenance
    assert 'item.get("status") != "confirmed"' in maintenance
    assert 'water_state_baseline' in maintenance


def test_frontend_exposes_layer_picker_backup_restore_delete_and_websockets():
    panel=PANEL.read_text(encoding='utf-8')
    frontend=FRONTEND.read_text(encoding='utf-8')
    for token in ('data_layer_execution_history','data_layer_statistics','data_layer_models','data_layer_maintenance'):
        assert token in panel
    assert '_dataLayerSelectionModal' in panel
    assert 'data-backup-create' in panel and 'data-clear-open' in panel
    assert 'data-backup-restore' in panel and 'data-backup-delete' in panel
    for command in ('/data/backup','/data/clear','/data/restore','/data/delete_backup'):
        assert f'f"{{DOMAIN}}{command}"' in frontend
    for handler in ('websocket_data_backup','websocket_data_clear','websocket_data_restore','websocket_data_delete_backup'):
        assert f'async_register_command(hass, {handler})' in frontend


def test_localization_catalogs_are_parallel_for_data_management_ui():
    catalogs={}
    required={
        'panel.data_and_backups','panel.data_layer_execution_history','panel.data_layer_statistics',
        'panel.data_layer_models','panel.data_layer_maintenance','panel.create_backup','panel.clear_data',
        'panel.restore_backup','panel.delete_backup','panel.backup_path_help',
    }
    for lang in ('en','ru'):
        data=json.loads((MODULE/'frontend'/'localization'/f'{lang}.json').read_text(encoding='utf-8'))
        catalogs[lang]=data
        assert all(data.get(key) for key in required)
    assert set(catalogs['en']) == set(catalogs['ru'])
