"""Schedule editor UI polish contracts for Vacuum Schedule 0.10.7."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_weekday_bulk_time_and_alignment_contracts():
    panel = PANEL.read_text(encoding="utf-8")
    for token in (
        '_weekdayBulkTimeHtml()', 'data-weekday-bulk-time', 'weekday-bulk-apply',
        'apply_to_enabled_days', 'grid-template-columns:84px minmax(110px,1fr)',
    ):
        assert token in panel


def test_force_priority_help_and_grid_alignment_contracts():
    panel = PANEL.read_text(encoding="utf-8")
    ru = json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text(encoding="utf-8"))
    assert ru['panel.force_priority_help'] == 'Чем больше число, тем выше приоритет.'
    for token in (
        'field-label-row', 'class="force-group-name-input"',
        'grid-template-columns:minmax(0,1fr) minmax(0,1fr) var(--force-action-width)',
        'margin-right:calc(var(--force-action-width) + var(--force-condition-gap))',
    ):
        assert token in panel
