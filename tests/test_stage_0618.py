"""Regression tests for mobile renderer has no provider tag or clear identity, mobile dispatch does not load or mutate previous delivery, and mobile candidate no longer advertises update replacement capability.

Covers retained behavioral contracts from earlier development stages.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "vacuum_schedule"
MANIFEST = MODULE / "manifest.json"
PANEL = MODULE / "frontend" / "panel.js"
FRONTEND = MODULE / "frontend.py"
MANAGER = MODULE / "notification_manager.py"
FORMATTING = MODULE / "notification_formatting.py"
ENGINE = MODULE / "scheduler_engine.py"




def test_mobile_renderer_has_no_provider_tag_or_clear_identity():
    formatting = FORMATTING.read_text(encoding="utf-8")
    block = formatting[formatting.index("def render_mobile_app("):formatting.index("def render_pushover(")]
    assert '"tag"' not in block
    assert "message_tag=" not in block
    assert '"group"' in block
    assert '"actions"' in block


def test_mobile_dispatch_does_not_load_or_mutate_previous_delivery():
    manager = MANAGER.read_text(encoding="utf-8")
    assert "if event.job_id and channel.transport_type is TransportType.TELEGRAM:" in manager
    assert "channel.transport_type in {TransportType.TELEGRAM, TransportType.MOBILE_APP}" not in manager
    assert '"message": "clear_notification"' not in manager
    assert "provider_message_tag == rendered.message_tag" not in manager
    assert "sent_fresh" not in manager


def test_mobile_candidate_no_longer_advertises_update_replacement_capability():
    manager = MANAGER.read_text(encoding="utf-8")
    assert '"supports_actionable": transport is TransportType.MOBILE_APP' in manager
    assert '"supports_updates": False' in manager
    panel = PANEL.read_text(encoding="utf-8")
    assert 'panel.every_allowed_event_is_sent_as_a_separate_push_duplicate_business_delive' in panel
    assert "стабильному tag" not in panel


def test_mobile_actions_are_revalidated_against_current_job_state():
    engine = ENGINE.read_text(encoding="utf-8")
    for token in (
        "if job is None or job.terminal:",
        "if job.state not in (JobState.PLANNED, JobState.WAIT):",
        "raise ValueError(\"job_cannot_start_now\")",
        "raise ValueError(\"job_cannot_skip\")",
        "if job.state not in (JobState.STARTING, JobState.RUNNING):",
        "raise ValueError(\"job_cannot_cancel\")",
    ):
        assert token in engine


def test_mobile_duplicate_prevention_stays_in_scheduler_delivery_store():
    manager = MANAGER.read_text(encoding="utf-8")
    assert 'delivery_id = f"{event.event_id}:{recipient.recipient_id}:{channel.channel_id}"' in manager
    assert "if self.store.get(delivery_id) is not None:" in manager
    assert 'summary["duplicates"] += 1' in manager
