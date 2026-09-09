"""Schedule editor clarity contracts."""
from pathlib import Path
import json
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
sys.path.insert(0, str(ROOT))
pkg = ModuleType("custom_components.vacuum_schedule")
pkg.__path__ = [str(MODULE)]
sys.modules.setdefault("custom_components.vacuum_schedule", pkg)

from custom_components.vacuum_schedule.schedule import ScheduleDefinition

PANEL = MODULE / "frontend" / "panel.js"


def _schedule(**changes):
    payload = dict(
        name="Named groups",
        enabled=True,
        weekdays=[0],
        dates=[],
        local_time="16:40",
        targets=["zone_a"],
        force_enabled=True,
        force_max_advance_minutes=360,
        force_priority=10,
        force_condition_groups=[[{"condition_id":"g1c1","type":"binary","entity_id":"binary_sensor.ready","state":"on","for_minutes":0}]],
        force_condition_group_names=["Nobody home"],
    )
    payload.update(changes)
    return ScheduleDefinition.create(**payload)




def test_force_group_display_names_roundtrip_without_execution_revision_change():
    schedule = _schedule()
    restored = ScheduleDefinition.from_dict(schedule.to_dict())
    assert restored.force_condition_group_names == ("Nobody home",)
    renamed = schedule.revised(force_condition_group_names=["House empty"])
    assert renamed.force_condition_group_names == ("House empty",)
    assert renamed.revision == schedule.revision


def test_force_group_names_are_padded_and_trimmed_to_group_count():
    first = [dict(_schedule().force_condition_groups[0][0])]
    second = [{"condition_id":"g2c1","type":"binary","entity_id":"binary_sensor.door","state":"on","for_minutes":0}]
    schedule = _schedule(force_condition_groups=[first, second], force_condition_group_names=["A"])
    assert schedule.force_condition_group_names == ("A", "")
    restored = ScheduleDefinition.from_dict({**schedule.to_dict(), "force_condition_group_names": ["A", "B", "EXTRA"]})
    assert restored.force_condition_group_names == ("A", "B")


def test_editor_layout_and_early_summary_contracts():
    panel = PANEL.read_text(encoding="utf-8")
    timing = panel[panel.index('  _scheduleTimingHtml() {'):panel.index('  _targetEditorHtml() {', panel.index('  _scheduleTimingHtml() {'))]
    assert timing.index('panel.execution_window_hm') < timing.index('panel.minimum_remaining_start_window_hm') < timing.index('panel.early_start')
    assert 'data-duration-minutes="minimum_start_window_minutes" data-duration-optional="1"' in timing
    render = panel[panel.index('  _editorHtml() {'):panel.index('  async _debugAction(', panel.index('  _editorHtml() {'))]
    assert 'class="specific-dates-label"' in render
    assert render.index('panel.start_restrictions') < render.index('panel.notifications')
    restrictions = render[render.index('panel.start_restrictions'):render.index('panel.notifications')]
    assert 'panel.minimum_remaining_battery' in restrictions
    assert 'panel.prewarning_minutes' not in restrictions
    notifications = render[render.index('panel.notifications'):]
    assert 'panel.prewarning_minutes' in notifications
    summary = panel[panel.index('  _forceScheduleSummary(row) {'):panel.index('  _scheduleRule(row) {')]
    assert '_formatHoursMinutes(minutes,"-")' in summary
    assert 'panel.source_force' in summary
    assert '_formatDurationSeconds' not in summary


def test_editable_force_group_name_controls_stay_aligned():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'data-force-group-name="${gi}"' in panel
    assert 'force_condition_group_names' in panel
    assert 'forceGroupNames().push("")' in panel
    assert 'forceGroupNames().splice(gi,1)' in panel


def test_ru_terminology_and_labels():
    ru=json.loads((MODULE/'frontend/localization/ru.json').read_text(encoding='utf-8'))
    assert ru['panel.force_priority'] == 'Приоритет досрочного выполнения'
    assert 'форсирован' not in ru['panel.force_conditions_met'].lower()
    assert ru['panel.minimum_remaining_start_window_hm'].endswith('ч:мин')
    assert ru['panel.start_restrictions'] == 'Ограничения запуска'
    assert ru['panel.force_summary'].startswith('{window}')
