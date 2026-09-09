"""Acceptance contracts for Vacuum Schedule 0.7.6 UI consistency patch."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_execution_modes_use_mdi_not_dry_run_emoji_in_panel():
    panel = PANEL.read_text()
    assert '_executionModeIcon' in panel
    assert '"flask-outline"' in panel
    assert '"robot-vacuum"' in panel
    assert 'execution-mode-picker' in panel
    assert 'data-execution-mode="DRY_RUN"' in panel
    assert 'data-execution-mode="REAL"' in panel
    assert '🧪' not in panel


def test_notification_message_unicode_is_intentionally_preserved():
    ru = (MODULE / "frontend" / "localization" / "ru.json").read_text()
    en = (MODULE / "frontend" / "localization" / "en.json").read_text()
    assert '🧪 Тест Vacuum Schedule' in ru
    assert '✅ Уборка завершена' in ru
    assert '🧪 Vacuum Schedule test' in en
    assert '✅ Cleaning completed' in en


def test_routing_test_uses_friendly_name_two_line_layout_and_localized_status():
    panel = PANEL.read_text()
    manager = (MODULE / "notification_manager.py").read_text()
    assert '_notificationChannelDisplayName' in panel
    assert 'recipient-channel-stack' in panel
    assert 'routing-target' in panel
    assert '_notificationDeliveryStatusLabel(x.status)' in panel
    assert '_notificationDeliveryOperationLabel(x.operation)' in panel
    assert '"target_kind": channel.target_kind' in manager


def test_execution_mode_labels_are_normalized():
    import json
    ru=json.loads((MODULE / "frontend" / "localization" / "ru.json").read_text())
    en=json.loads((MODULE / "frontend" / "localization" / "en.json").read_text())
    assert ru['panel.execution_dry_run']=='Dry-Run'
    assert ru['panel.execution_real']=='Реальное выполнение'
    assert ru['panel.real_execution']=='Реальное выполнение'
    assert en['panel.execution_real']=='Real execution'
