"""Regression contracts for Vacuum Schedule 0.8.15 quick execution-mode controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"




def test_status_card_has_both_quick_mode_controls_after_current_value():
    panel = PANEL.read_text(encoding="utf-8")
    status = panel[panel.index("  _statusHtml() {"):panel.index("  _notificationPresetPolicy(preset) {")]
    assert 'class="summary-mode-value ${executionModeClass}"' in status
    assert 'class="mini-actions summary-actions summary-mode-actions"' in status
    assert 'class="ghost icon-only compact-icon-action quick-execution-mode summary-action quick-execution-mode-dry ${dryRunMode?"active":""}"' in status
    assert 'data-execution-mode="DRY_RUN"' in status
    assert 'class="ghost icon-only compact-icon-action quick-execution-mode summary-action quick-execution-mode-real ${realExecutionMode?"active":""}"' in status
    assert 'data-execution-mode="REAL"' in status
    assert 'aria-pressed="${dryRunMode?"true":"false"}"' in status
    assert 'aria-pressed="${realExecutionMode?"true":"false"}"' in status


def test_settings_and_status_reuse_one_mode_switch_transaction():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'async _requestExecutionModeSwitch(targetMode)' in panel
    assert 'button.execution-mode-choice' in panel
    assert 'button.quick-execution-mode' in panel
    assert panel.count('await this._requestExecutionModeSwitch(btn.dataset.executionMode);') == 2
    transaction = panel[panel.index('  async _requestExecutionModeSwitch(targetMode)'):panel.index('  _executionBannerHtml()')]
    assert 'vacuum_schedule/settings/preview_execution_mode' in transaction
    assert 'panel.switch_to_real_execution_warning' in transaction
    assert 'panel.switch_to_dry_run_warning' in transaction
    assert 'panel.execution_mode_regeneration_warning' in transaction
    assert 'panel.execution_mode_may_start_immediately' in transaction
    assert 'await this._confirmAction(label,message,label,selected==="REAL")' in transaction
    assert 'vacuum_schedule/settings/update_execution_mode' in transaction


def test_quick_mode_controls_are_compact_and_share_save_lock():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'button.quick-execution-mode' in panel
    assert 'button.icon-only.compact-icon-action {' in panel
    assert '--vs-compact-action-size:28px;' in panel
    helper = panel[panel.index('  _setExecutionFormSaving(saving, includeModeControls = false)'):panel.index('  _syncExecutionOptionsDom()')]
    assert 'button.quick-execution-mode' in helper
    assert 'if(includeModeControls)' in helper
    assert 'control.disabled=!!saving' in helper


