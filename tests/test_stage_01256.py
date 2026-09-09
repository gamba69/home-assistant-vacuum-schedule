"""0.12.56 schedule-table compaction and notification archive diagnostics."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"


def test_schedule_id_is_inline_and_force_preemption_is_explicit():
    panel = PANEL.read_text(encoding="utf-8")
    assert 'class="schedule-id-line"' in panel
    assert '<span>ID:</span> <code' in panel
    assert 'class="schedule-id-cell"' not in panel
    assert '<th>${this._tr("panel.schedule_id")}</th>' not in panel
    assert 'colspan="6"' in panel
    assert 'panel.force_preempt_short_yes' in panel
    assert 'panel.force_preempt_short_no' in panel
    assert 'row.force_preempts_scheduled?' in panel

    expected = {
        "ru": ("↑ плановых", "↓ плановых"),
        "uk": ("↑ планових", "↓ планових"),
        "en": ("↑ scheduled", "↓ scheduled"),
    }
    for lang, labels in expected.items():
        data = json.loads((MODULE / f"frontend/localization/{lang}.json").read_text(encoding="utf-8"))
        assert data["panel.force_preempt_short_yes"] == labels[0]
        assert data["panel.force_preempt_short_no"] == labels[1]
        assert data["panel.force_respects_scheduled_help"]


def test_message_payload_always_contains_compact_delivery_routes():
    source = (MODULE / "notification_manager.py").read_text(encoding="utf-8")
    assert '"delivery_routes": delivery_routes' in source
    for key in (
        '"recipient_name"',
        '"channel_name"',
        '"transport_type"',
        '"target"',
        '"status"',
        '"suppression_reason"',
        '"error"',
    ):
        assert key in source


def test_notification_message_table_renders_who_channel_target_and_delivery_status():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01256');
        const x=new C();
        const labels={{
          'panel.notification_log_is_empty':'Пусто','panel.time':'Время','panel.message':'Текст сообщения',
          'panel.recipient_channel':'Получатель / канал','panel.delivery':'Доставка','panel.execution_mode':'Режим выполнения',
          'panel.event':'Событие','panel.sent':'Отправлено','panel.failed':'Не выполнено','panel.suppressed':'Подавлено',
          'panel.pending':'Ожидает','panel.unknown_8a51c33':'Неизвестно','panel.start_43805bb':'Начало',
          'common.transport.mobile_app':'Home Assistant','common.delivery_status.sent':'Отправлено','common.delivery_status.suppressed':'Подавлено',
          'common.suppression.not_home':'Получатель не дома','panel.execution_real':'Реальное выполнение'
        }};
        x._tr=(k)=>labels[k]||k;
        x._formatDateTime=()=> 'сегодня, 10:00';
        x._notificationData={{candidates:{{channels:[]}},messages:[{{
          event_id:'e1',created_at:'2026-09-08T10:00:00+03:00',title:'Спальня · 10:45',message:'Уборка началась',
          event_type:'started',execution_mode:'REAL',delivery_summary:{{total:2,sent:1,suppressed:1,failed:0,pending:0}},
          delivery_routes:[
            {{recipient_name:'Игорь',channel_name:'Телефон',transport_type:'mobile_app',target:'notify.mobile_app_phone',target_kind:'service',status:'sent'}},
            {{recipient_name:'Дом',channel_name:'Планшет',transport_type:'mobile_app',target:'notify.mobile_app_tablet',target_kind:'service',status:'suppressed',suppression_reason:'not_home'}}
          ]
        }}]}};
        const html=x._notificationMessagesHtml();
        for(const token of ['Игорь','Телефон','notify.mobile_app_phone','Дом','Планшет','notify.mobile_app_tablet','1/2','Получатель не дома','Получатель / канал','Доставка']){{
          if(!html.includes(token)) throw new Error('missing '+token+' in '+html);
        }}
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_frontend_locales_have_new_archive_and_force_keys_in_parity():
    keys = None
    for lang in ("en", "ru", "uk"):
        data = json.loads((MODULE / f"frontend/localization/{lang}.json").read_text(encoding="utf-8"))
        current = set(data)
        keys = current if keys is None else keys
        assert current == keys
        for key in (
            "panel.delivery",
            "panel.force_preempt_short_yes",
            "panel.force_preempt_short_no",
            "panel.force_respects_scheduled_help",
        ):
            assert data[key]
