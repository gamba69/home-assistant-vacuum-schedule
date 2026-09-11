"""0.13.4 per-occurrence occupancy override contracts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_waiting_job_has_non_forcing_occupancy_toggle():
    panel = PANEL.read_text(encoding="utf-8")
    row = panel[panel.index("  _jobRowActionsHtml(job) {"):panel.index("  _activeJobsHtml(entry) {")]
    assert 'button("ignore_occupancy"' in row
    assert 'button("respect_occupancy"' in row
    assert '(zone?.blockers||[]).includes("zone_busy")' in row
    assert 'manual_overrides?.ignore_busy_zones' in row

    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    method = engine[
        engine.index("    async def async_set_job_occupancy_override("):
        engine.index("    async def async_skip_job(")
    ]
    assert 'overrides["ignore_busy_zones"] = sorted(accepted)' in method
    assert 'overrides.pop("ignore_busy_zones", None)' in method
    assert 'await self.async_recheck_jobs({job_id})' in method
    assert '"manual_occupancy_override"' in method
    assert '"manual_occupancy_override_cleared"' in method
    assert 'force_execution' not in method
    assert 'manual_release_at' not in method
    assert 'await self.async_start_job_now(' not in method


def test_occupancy_override_suppresses_only_zone_busy_and_is_job_scoped():
    preflight = (MODULE / "preflight.py").read_text(encoding="utf-8")
    assert 'manual_overrides.get("ignore_busy_zones", ())' in preflight
    assert 'zone_blockers = [item for item in zone_blockers if item.code != "zone_busy"]' in preflight

    engine = (MODULE / "scheduler_engine.py").read_text(encoding="utf-8")
    method = engine[
        engine.index("    async def async_set_job_occupancy_override("):
        engine.index("    async def async_skip_job(")
    ]
    assert 'job.metadata["manual_overrides"] = overrides' in method
    assert 'job.metadata.pop("manual_overrides", None)' in method
    assert 'self._sync_zone_runs(job)' in method
    assert 'self.preflight.evaluate_zone(' in method
    assert 'PreflightPhase.AUTHORITATIVE' in method


def test_websocket_dispatches_occupancy_toggle_as_its_own_action():
    frontend = (MODULE / "frontend.py").read_text(encoding="utf-8")
    block = frontend[
        frontend.index('vol.Required("type"): f"{DOMAIN}/jobs/action"'):
        frontend.index('@websocket_api.websocket_command({\n    vol.Required("type"): f"{DOMAIN}/schedules/run_now"')
    ]
    assert '"ignore_occupancy"' in block
    assert '"respect_occupancy"' in block
    assert 'scheduler.async_set_job_occupancy_override(' in block
    assert 'ignore=action == "ignore_occupancy"' in block


def test_occupancy_toggle_is_localized_in_all_frontend_languages():
    required = {
        "panel.ignore_occupancy",
        "panel.ignore_occupancy_confirm",
        "panel.respect_occupancy",
        "panel.respect_occupancy_confirm",
        "panel.occupancy_ignored_for_this_job",
    }
    for language in ("en", "ru", "uk"):
        data = json.loads(
            (MODULE / "frontend" / "localization" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert required <= data.keys()
        assert all(str(data[key]).strip() for key in required)


def test_release_version_is_0134():
    manifest = json.loads((MODULE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.13.4"
