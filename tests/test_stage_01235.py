"""Regression coverage for 0.12.35 form-draft protection and notification labels."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_global_multi_field_forms_have_persistent_edit_drafts():
    panel = PANEL.read_text(encoding="utf-8")
    assert "this._notificationGlobalDraft = null;" in panel
    assert "this._notificationGlobalDirty = false;" in panel
    assert "this._policyDraft = null;" in panel
    assert "this._policyDirty = false;" in panel
    assert "this._notificationGlobalDirty || this._policyDirty" in panel
    assert "_syncNotificationGlobalDraft(force = false)" in panel
    assert "if(!force && this._notificationGlobalDirty) return;" in panel
    assert "_syncPolicyDraft(force = false)" in panel
    assert "if(!force && this._policyDirty) return;" in panel
    assert 'el.addEventListener("input",update)' in panel
    assert 'const markPolicyDirty=()=>{this._policyDirty=true;};' in panel


def test_global_drafts_survive_backend_refresh_while_user_is_editing():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01236');
        const x=new C();
        x._translations={{}};x._fallbackTranslations={{}};

        x._notificationData={{settings:{{enabled:true,preset:'balanced',dry_run_delivery:'send',policy:{{wait_delay_seconds:120}}}}}};
        x._syncNotificationGlobalDraft(true);
        x._notificationGlobalDraft.policy.wait_delay_seconds=777;
        x._notificationGlobalDirty=true;
        x._notificationData={{settings:{{enabled:false,preset:'minimal',dry_run_delivery:'log_only',policy:{{wait_delay_seconds:5}}}}}};
        x._syncNotificationGlobalDraft();
        if(x._notificationGlobalDraft.policy.wait_delay_seconds!==777) throw new Error('notification draft overwritten');
        if(!x._editingAny) throw new Error('notification edit not protected');

        x._notificationGlobalDirty=false;
        x._settingsData={{policy:{{default_min_battery_percent:20,forecasts:{{}}}}}};
        x._syncPolicyDraft(true);
        x._policyDraft.default_min_battery_percent=37;
        x._policyDirty=true;
        x._settingsData={{policy:{{default_min_battery_percent:10,forecasts:{{}}}}}};
        x._syncPolicyDraft();
        if(x._policyDraft.default_min_battery_percent!==37) throw new Error('policy draft overwritten');
        if(!x._editingAny) throw new Error('policy edit not protected');
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_finished_notification_event_is_generic_result_not_error():
    ru = json.loads((MODULE / "frontend/localization/ru.json").read_text(encoding="utf-8"))
    assert ru["panel.cleaning_result"] == "Результат уборки"
    panel = PANEL.read_text(encoding="utf-8")
    block = panel[panel.index("  _notificationEventLabel(value)"):panel.index("  _notificationModeOptions", panel.index("  _notificationEventLabel(value)"))]
    assert 'finished: this._tr("panel.cleaning_result")' in block
    assert "panel.finish_error" not in block


def test_all_multi_value_save_editors_have_state_or_render_guard():
    """Static audit: every multi-value editor has a draft/guard or saves DOM atomically."""
    panel = PANEL.read_text(encoding="utf-8")
    # Schedule, cleaning-zone, recipient/channel and maintenance editors use explicit drafts.
    for token in ("this._form", "this._roomDraft", "this._notificationRecipientDraft", "this._maintenanceDraft"):
        assert token in panel
    # Execution options already use a dedicated draft/dirty guard.
    assert "this._executionSettingsDraft" in panel and "this._executionSettingsDirty" in panel
    # Global notification and scheduler/forecast forms are the two always-visible
    # multi-field forms; 0.12.35 gives both explicit edit protection.
    assert "this._notificationGlobalDraft" in panel and "this._notificationGlobalDirty" in panel
    assert "this._policyDraft" in panel and "this._policyDirty" in panel
    # The single binding editor is protected from background renders by _settingsEditor
    # and its save payload is collected from the complete editor DOM in one operation.
    assert "!!this._settingsEditor" in panel
    assert "const bindingPayload=(key,body)=>" in panel
