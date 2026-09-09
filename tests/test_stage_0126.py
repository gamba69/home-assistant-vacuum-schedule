"""additional/early execution and telemetry stabilization."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'custom_components'/'vacuum_schedule'




def test_additional_and_early_actions_are_separate_in_ui_and_backend():
    panel=(MODULE/'frontend/panel.js').read_text(encoding='utf-8')
    engine=(MODULE/'scheduler_engine.py').read_text(encoding='utf-8')
    assert 'data-action="${this._escape(action)}"' in panel
    assert 'button("additional_run"' in panel
    assert 'button("start_now"' in panel
    assert 'panel.additional_run' in panel and 'panel.execute_early' in panel
    assert 'action:"additional_run"' in panel
    assert 'async def async_run_job_additional' in engine
    additional=engine[engine.index('async def async_run_job_additional'):engine.index('async def async_run_schedule_early')]
    assert 'local_date = self._scheduler_local_date(now)' in additional
    assert 'schedule.effective_cleaning_params_for_date(local_date)' in additional
    assert 'cleaning_params=dict(effective_params)' in additional
    assert 'cleaning_params=dict(template.cleaning_params)' not in additional
    block=engine[engine.index('async def async_run_schedule_now'):engine.index('async def async_skip_job')]
    assert 'job.origin = JobOrigin.MANUAL' in block
    assert 'job.manual_triggered_at = now' in block
    assert 'job.metadata["force_execution"]' in block
    assert 'job.manual_release_at = now' not in block


def test_live_area_normalizer_is_monotonic_and_suppresses_stale_start():
    source=(MODULE/'scheduler_engine.py').read_text(encoding='utf-8')
    block=source[source.index('def _normalized_live_area'):source.index('def _live_execution_status')]
    assert 'if values[0] <= 1.0' in block
    assert 'return max(values)' in block
    assert 'reset_at' in block
    assert 'return max(values[reset_at:])' in block
    assert 'return None' in block
    live=source[source.index('def _live_execution_status'):source.index('def _job_status_dict')]
    assert '"cleaning_area_m2": self._normalized_live_area(attempt)' in live


def test_live_clock_ticks_each_second_but_backend_poll_stays_ten_seconds():
    panel=(MODULE/'frontend/panel.js').read_text(encoding='utf-8')
    assert 'setInterval(()=>this._updateActiveNowCells(),1000)' in panel
    assert 'setInterval(() => this._refreshActiveLiveStatus(), 10000)' in panel


def test_roborock_post_clean_window_is_longer_than_delayed_autoempty_gap():
    backend=(MODULE/'execution_backend.py').read_text(encoding='utf-8')
    assert 'ROBOROCK_COMPLETION_SETTLE = timedelta(seconds=120)' in backend
    assert 'ROBOROCK_POST_AUTO_EMPTY_SETTLE = timedelta(seconds=10)' in backend
    observer=(MODULE/'execution_observer.py').read_text(encoding='utf-8')
    service=observer[observer.index('_ROBOROCK_SERVICE_STATES'):observer.index('_ROBOROCK_SEGMENT_STATES')]
    assert 'emptying_the_bin' in service
    assert 'dry_status' not in service


def test_russian_user_terms_are_unambiguous():
    ru=json.loads((MODULE/'frontend/localization/ru.json').read_text(encoding='utf-8'))
    assert ru['panel.additional_run']=='Дополнительный запуск'
    assert ru['panel.execute_early']=='Выполнить досрочно'
    assert ru['panel.source_force']=='Досрочно'
    assert ru['panel.early_start']=='Досрочное выполнение'
