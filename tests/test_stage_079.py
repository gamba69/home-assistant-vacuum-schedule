"""Acceptance contracts for Vacuum Schedule 0.7.9 recent-job execution trace."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"




def test_recent_jobs_are_expandable_cards_with_lazy_full_trace():
    panel=PANEL.read_text()
    assert 'details class=\"job-card history-job-card\"' in panel
    assert 'vacuum_schedule/scheduler/job_details' in panel
    assert '_historyJobDetailsHtml' in panel
    assert '_historyJobOpen' in panel


def test_backend_exposes_durable_job_trace_endpoint():
    frontend=FRONTEND.read_text()
    engine=(MODULE / "scheduler_engine.py").read_text()
    assert 'f"{DOMAIN}/scheduler/job_details"' in frontend
    assert 'websocket_scheduler_job_details' in frontend
    assert 'def job_details_payload' in engine
    assert 'payload["lifecycle_events"]' in engine
    assert 'payload["notification_history"]' in engine


def test_recent_job_detail_contains_maximum_execution_sections():
    panel=PANEL.read_text()
    for marker in [
        '_historySummaryReportHtml(job,context)',
        '_historyZoneReportHtml(job,context)',
        '_historyTimeReportHtml(job,context)',
        '_historyResourcesReportHtml(job,context)',
        '_historyExecutionReportHtml(job,context)',
        '_historyJournalReportHtml(job)',
        '_historyTechnicalReportHtml(job)',
        '_lifecycleTraceHtml(job)',
        '_preflightSourcesHtml(job,false)',
        '_jobAuditHtml(job)',
        '_jobNotificationHistoryHtml(job)',
        'JSON.stringify(job,null,2)',
    ]:
        assert marker in panel

def test_job_and_notification_stores_support_per_job_trace_queries():
    jobs=(MODULE / "job_store.py").read_text()
    notifications=(MODULE / "notification_store.py").read_text()
    assert 'def events_for_job' in jobs
    assert 'def for_job' in notifications
