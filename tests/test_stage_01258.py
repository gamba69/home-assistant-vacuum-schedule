"""0.12.58 calm notification archive design regression."""
from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_notification_archive_uses_quiet_status_indicators_not_badges():
    panel = PANEL.read_text(encoding="utf-8")
    helper = panel.split("_notificationMessageDeliverySummaryHtml(message)", 1)[1].split("_notificationExecutionModeHtml", 1)[0]
    assert "status-badge" not in helper
    assert "notification-status-dot" in helper
    assert "notification-message-delivery-state" in helper
    table = panel.split("_notificationMessagesHtml()", 1)[1].split("_notificationHistoryHtml()", 1)[0]
    assert "_notificationExecutionModeHtml" in table
    assert "_executionModeHtml(x.execution_mode" not in table
    assert ".notification-message-row:hover { background:var(--vs-neutral-quiet-hover); }" in panel
    assert ".notification-execution-mode{color:var(--primary-text-color);font-weight:500}" in panel


def test_notification_archive_html_is_informative_but_visually_quiet():
    script = textwrap.dedent(f"""
        global.HTMLElement=class{{attachShadow(){{this.shadowRoot={{querySelectorAll(){{return []}}}};return this.shadowRoot;}}}};
        global.customElements={{_m:new Map(),get(k){{return this._m.get(k)}},define(k,v){{this._m.set(k,v)}}}};
        global.window={{localStorage:{{getItem(){{return null}},setItem(){{}}}},matchMedia(){{return {{matches:false,addEventListener(){{}},removeEventListener(){{}}}}}}}};
        global.history={{replaceState(){{}}}};global.fetch=async()=>({{ok:true,json:async()=>({{}})}});global.navigator={{language:'ru-RU'}};
        require({str(PANEL)!r});
        const C=customElements.get('vacuum-schedule-panel-01258');
        const x=new C();
        const labels={{
          'panel.notification_log_is_empty':'Пусто','panel.time':'Время','panel.message':'Текст сообщения',
          'panel.recipient_channel':'Получатель / канал','panel.delivery':'Доставка','panel.execution_mode':'Режим выполнения',
          'panel.event':'Событие','panel.sent':'Отправлено','panel.failed':'Ошибка','panel.suppressed':'Подавлено',
          'panel.pending':'Ожидает','panel.unknown_8a51c33':'Неизвестно','panel.start_43805bb':'Начало',
          'common.transport.mobile_app':'Mobile App','common.delivery_status.sent':'Отправлено','common.delivery_status.suppressed':'Подавлено',
          'common.suppression.not_home':'Получатель не дома','panel.execution_real':'Реальное выполнение'
        }};
        x._tr=(k)=>labels[k]||k;
        x._formatDateTime=()=> 'сегодня, 10:00';
        x._notificationData={{candidates:{{channels:[]}},messages:[{{
          event_id:'e1',created_at:'2026-09-08T10:00:00+03:00',title:'Спальня · 10:45',message:'Уборка началась',
          event_type:'started',execution_mode:'REAL',delivery_summary:{{total:1,sent:0,suppressed:1,failed:0,pending:0}},
          delivery_routes:[{{recipient_name:'Игорь',channel_name:'iPhone 15 Pro',transport_type:'mobile_app',target:'notify.mobile_app_iphone_15_pro',target_kind:'service',status:'suppressed',suppression_reason:'not_home'}}]
        }}]}};
        const html=x._notificationMessagesHtml();
        for(const token of ['Игорь','Mobile App','iPhone 15 Pro','notify.mobile_app_iphone_15_pro','Подавлено 1/1','Получатель не дома','Реальное выполнение','notification-status-dot','notification-mode-dot']){{
          if(!html.includes(token)) throw new Error('missing '+token+' in '+html);
        }}
        if(html.includes('status-badge')) throw new Error('delivery badge still present: '+html);
        if(html.includes('execution-mode-inline')) throw new Error('large colored execution mode still present: '+html);
        const route=x._notificationMessageRoutesHtml(x._notificationData.messages[0]);
        if(route.includes('Подавлено')) throw new Error('delivery status duplicated in route: '+route);
    """)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_previous_frontend_identity_is_preserved():
    panel = PANEL.read_text(encoding="utf-8")
    assert '\"vacuum-schedule-panel-01258\"' in panel
    assert '\"vacuum-schedule-panel-01257\"' in panel
