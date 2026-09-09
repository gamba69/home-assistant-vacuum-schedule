"""Release-identity and migration contracts for Vacuum Schedule 0.13.0."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "vacuum_schedule"


def _leaf_paths(value, prefix=""):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else key
            result |= _leaf_paths(item, child)
    else:
        result.add(prefix)
    return result


def test_release_identity_is_exact():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Vacuum Schedule"
    assert manifest["domain"] == "vacuum_schedule"
    assert manifest["version"] == "0.13.0"
    assert manifest["documentation"] == "https://github.com/gamba69/home-assistant-vacuum-schedule"
    assert manifest["issue_tracker"] == "https://github.com/gamba69/home-assistant-vacuum-schedule/issues"
    assert manifest["codeowners"] == ["@gamba69"]
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert 'DOMAIN: Final = "vacuum_schedule"' in const
    assert 'VERSION: Final = "0.13.0"' in const


def test_repository_has_exactly_one_hacs_integration_directory():
    dirs = sorted(p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir())
    assert dirs == ["vacuum_schedule"]
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == "Vacuum Schedule"


def test_services_and_frontend_use_new_public_identity():
    services = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    assert "integration: vacuum_schedule" in services
    assert "integration: vacuum_scheduler" not in services
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert 'EVENT_TEST: Final = "vacuum_schedule_test_event"' in const
    frontend = (COMPONENT / "frontend.py").read_text(encoding="utf-8")
    assert 'PANEL_URL_PATH = "vacuum-schedule"' in frontend
    assert 'FRONTEND_BASE_URL = "/vacuum_schedule_frontend"' in frontend
    panel = (COMPONENT / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert '"vacuum_schedule/' in panel
    assert '"vacuum_scheduler/' not in panel
    assert '"vacuum_schedule.last_config_entry"' in panel


def test_legacy_domain_literal_is_isolated_to_migration_runtime():
    hits = []
    for path in COMPONENT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "vacuum_scheduler" in text:
            hits.append(path.relative_to(COMPONENT).as_posix())
    assert hits == ["domain_migration.py"]


def test_domain_migration_covers_all_01261_stores_and_monthly_ledger():
    source = (COMPONENT / "domain_migration.py").read_text(encoding="utf-8")
    expected = {
        "jobs",
        "job_history",
        "notifications",
        "external_execution",
        "statistics.index",
        "statistics.cache",
        "statistics.charging",
        "statistics.control",
        "statistics.models",
        "statistics.water",
        "statistics.water.ledger",
        "statistics.water.state",
    }
    literal_suffixes = set(re.findall(r'^    "([a-z_.]+)",$', source, flags=re.MULTILINE))
    assert expected <= literal_suffixes
    assert 'LEGACY_DOMAIN = "vacuum_scheduler"' in source
    assert 'suffix = f"statistics.{month}"' in source
    assert 'raw_index.get("months", [])' in source
    assert "await target.async_save(payload)" in source
    assert 'config.path("vacuum_scheduler_backups")' in source
    assert 'config.path("vacuum_schedule_backups")' in source
    assert 'pattern = f"vacuum-scheduler-{legacy_entry_id}-*.zip"' in source
    assert 'manifest["entry_id"] = new_entry_id' in source
    assert '"migrated_backups": migrated_backups' in source


def test_config_flow_migrates_options_and_removes_legacy_only_after_store_copy():
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert "async_step_migrate_legacy" in source
    assert "data=dict(legacy_entry.data)" in source
    assert "options=dict(legacy_entry.options)" in source
    copy_pos = source.index("await async_migrate_legacy_domain_storage(")
    remove_pos = source.index("await self.hass.config_entries.async_remove(legacy_entry_id)")
    assert copy_pos < remove_pos
    assert "return result" in source[copy_pos:remove_pos + 200]


def test_patch_release_notes_are_permanently_absent():
    assert not list(ROOT.glob("PATCH_*.md"))
    assert not list(ROOT.rglob("PATCH_*.md"))
    for path in ROOT.rglob("*.md"):
        assert "PATCH_0.12." not in path.read_text(encoding="utf-8")


def test_project_documentation_has_en_ru_uk_file_parity():
    doc_sets = {
        lang: {p.name for p in (ROOT / "docs" / lang).iterdir() if p.is_file()}
        for lang in ("en", "ru", "uk")
    }
    expected = {"README.md", "ARCHITECTURE.md", "TESTING.md", "MIGRATION_0.13.0.md", "CHANGELOG.md"}
    assert doc_sets["en"] == doc_sets["ru"] == doc_sets["uk"] == expected


def test_native_custom_integration_translations_have_three_language_key_parity():
    catalogs = {
        lang: json.loads((COMPONENT / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "ru", "uk")
    }
    keys = _leaf_paths(catalogs["en"])
    assert _leaf_paths(catalogs["ru"]) == keys
    assert _leaf_paths(catalogs["uk"]) == keys
    assert "migrate_legacy" in catalogs["en"]["config"]["step"]
    assert not (COMPONENT / "strings.json").exists()
