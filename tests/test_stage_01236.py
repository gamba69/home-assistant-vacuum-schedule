"""Regression coverage for the 0.12.36 global notification-policy layout."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_global_notification_message_types_use_forecast_style_sections():
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[
        panel.index('      <section class="entry-card"><div class="entry-header"><div><h2>${this._tr("panel.global_notification_policy")}'):
        panel.index('      <section class="entry-card notification-users-card">')
    ]

    expected_sections = (
        ("panel.prewarning_834cdf5", "panel.prewarning_policy_help", "prewarning_mode"),
        ("panel.wait_entry", "panel.wait_entry_policy_help", "wait_enter_mode"),
        ("panel.cleaning_start", "panel.cleaning_start_policy_help", "start_mode"),
        ("panel.cleaning_result", "panel.cleaning_result_policy_help", "finish_mode"),
        ("panel.start_forecast", "panel.forecast_help", "start_forecast_mode"),
    )
    positions = []
    for title_key, help_key, policy_key in expected_sections:
        header = f'<div class="field wide notification-policy-message-type"><b>${{this._tr("{title_key}")}}</b><small class="help">${{this._tr("{help_key}")}}</small></div>'
        assert header in block
        assert f'<span>${{this._tr("panel.mode")}}</span><select data-notification-policy="{policy_key}"' in block
        positions.append(block.index(header))

    assert positions == sorted(positions)
    # The four legacy event types no longer use their own title as an inline field label.
    assert '<label class="field"><span>${this._tr("panel.prewarning_834cdf5")}</span>' not in block
    assert '<label class="field"><span>${this._tr("panel.wait_entry")}</span>' not in block
    assert '<label class="field"><span>${this._tr("panel.cleaning_start")}</span>' not in block
    assert '<label class="field"><span>${this._tr("panel.cleaning_result")}</span>' not in block


def test_notification_policy_section_help_is_localized_ru_en():
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    en = json.loads((MODULE / "frontend/localization/en.json").read_text(encoding="utf-8"))
    keys = (
        "panel.prewarning_policy_help",
        "panel.wait_entry_policy_help",
        "panel.cleaning_start_policy_help",
        "panel.cleaning_result_policy_help",
    )
    for key in keys:
        assert ru[key].strip()
        assert en[key].strip()

    assert "плановым запуском" in ru["panel.prewarning_policy_help"]
    assert "переходит в ожидание" in ru["panel.wait_entry_policy_help"]
    assert "фактически начал уборку" in ru["panel.cleaning_start_policy_help"]
    assert "После завершения задания" in ru["panel.cleaning_result_policy_help"]
