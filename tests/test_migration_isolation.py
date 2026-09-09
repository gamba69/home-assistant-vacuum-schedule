"""Checks that historical upgrade transforms are isolated from current runtime code.

The contracts preserve supported upgrade paths while keeping scheduler/store owners focused on normal operation.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"


def test_config_and_runtime_migrations_are_isolated():
    init = (MODULE / "__init__.py").read_text(encoding="utf-8")
    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    migrations = (MODULE / "migrations.py").read_text(encoding="utf-8")

    assert "from .migrations import async_migrate_entry" in init
    assert "migrate_pre_071_runtime_state" in engine
    assert "migrate_pre_074_materialization" in engine
    assert 'options.setdefault(CONF_EXECUTION_MODE, "DRY_RUN")' in migrations
    assert 'schedule["weekday_times"]' in migrations
    assert "cleanup_legacy_calendar_entities" in migrations


def test_storage_migrations_are_isolated():
    job_store = (MODULE / "job_store.py").read_text(encoding="utf-8")
    water = (MODULE / "water_statistics.py").read_text(encoding="utf-8")
    statistics = (MODULE / "statistics_manager.py").read_text(encoding="utf-8")
    migrations = (MODULE / "storage_migrations.py").read_text(encoding="utf-8")

    assert "migrate_job_history_archive" in job_store
    assert "migrate_legacy_water_maintenance" in water
    assert "async_repair_statistics_area_history" in statistics
    assert '"migrated_terminal_snapshot"' in migrations
    assert 'AREA_REPAIR_ID = "0.10.18_floor_vs_processed_area_v3"' in migrations
