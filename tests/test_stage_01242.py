"""Regression coverage for unified compact icon actions in 0.12.42."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_compact_icon_action_has_one_exact_square_geometry():
    panel = PANEL.read_text(encoding="utf-8")
    assert "--vs-compact-action-size:28px;" in panel
    assert "--vs-compact-action-icon-size:16px;" in panel
    assert "--vs-compact-action-radius:7px;" in panel
    assert "button.icon-only.compact-icon-action {" in panel
    assert "width:var(--vs-compact-action-size)!important; min-width:var(--vs-compact-action-size)!important; max-width:var(--vs-compact-action-size)!important;" in panel
    assert "height:var(--vs-compact-action-size)!important; min-height:var(--vs-compact-action-size)!important; max-height:var(--vs-compact-action-size)!important;" in panel
    assert "aspect-ratio:1 / 1!important;" in panel
    assert "button.icon-only.compact-icon-action>.button-icon {" in panel
    assert "position:absolute!important; left:50%!important; top:50%!important; transform:translate(-50%,-50%)!important;" in panel
    assert "width:var(--vs-compact-action-icon-size)!important;" in panel
    assert "height:var(--vs-compact-action-icon-size)!important;" in panel


def test_table_row_and_summary_plaque_actions_use_shared_compact_control():
    panel = PANEL.read_text(encoding="utf-8")
    required = [
        "compact-icon-action schedule-toggle",
        "compact-icon-action edit",
        "compact-icon-action delete",
        "compact-icon-action job-action job-row-action",
        "compact-icon-action job-action job-now-action",
        "compact-icon-action notification-edit-recipient",
        "compact-icon-action notification-delete-recipient",
        "compact-icon-action settings-edit-binding",
        "compact-icon-action room-edit",
        "compact-icon-action room-delete",
        "compact-icon-action override-apply",
        "compact-icon-action override-clear",
        "compact-icon-action quick-gate summary-action",
        "compact-icon-action quick-gate-until summary-action",
        "compact-icon-action quick-execution-mode summary-action",
        "compact-icon-action statistics-refresh",
        "compact-icon-action forecast-delete-archive",
    ]
    for marker in required:
        assert marker in panel, marker


def test_no_legacy_30px_active_job_or_partial_28px_copy_button_geometry_remains():
    panel = PANEL.read_text(encoding="utf-8")
    assert ".job-now-action { min-width:30px!important;" not in panel
    assert ".delivery-copy { flex:0 0 auto; width:28px!important; height:28px!important;" not in panel
    assert ".job-now-action { flex:0 0 var(--vs-compact-action-size)!important; }" in panel
    assert ".delivery-copy { flex:0 0 var(--vs-compact-action-size); }" in panel


def test_non_compact_icon_only_controls_are_deliberate_large_or_chip_exceptions():
    panel = PANEL.read_text(encoding="utf-8")
    classes = []
    for match in re.finditer(r'<button[^>]*class="([^"]*icon-only[^"]*)"', panel):
        value = match.group(1)
        if "compact-icon-action" not in value:
            classes.append(value)
    # Editor/page close buttons keep the larger touch target; filter-chip X keeps its own
    # 22 px geometry.  No table/row/plaque action may silently fall back to 36 px.
    allowed_fragments = (
        "history-active-filter-remove",
        "notification-recipient-cancel",
        "room-cancel",
        "settings-editor-cancel",
        "ghost icon-only close",
        "ghost icon-only refresh",
    )
    assert classes
    assert all(any(fragment in value for fragment in allowed_fragments) for value in classes), classes
