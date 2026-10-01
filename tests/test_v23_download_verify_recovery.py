from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import time

from PySide6.QtCore import Qt

from djd_maker.core.models import Job, JobState
from djd_maker.core.stop_reason import MESSAGES
from djd_maker.gui.viewmodels import state_display, summarize_jobs
from djd_maker.orchestration.task_discovery import (
    automatic_download_recovery_candidate,
    final_discovery,
    manual_download_recovery_available,
    terminal,
)
from test_gui import _drain_until, _window
from test_gui_pipeline_controller import controller as service_controller
from test_pipeline import MemoryJobs, coordinator
from test_v22_scheduler_manual_gui import ManualNotebook


NOW = datetime.now(UTC)


def failed_download(tmp_path: Path, index: int = 0, *, exhausted: bool = False) -> Job:
    job = Job(str(tmp_path / f'job-{index}.txt'), state=JobState.DOWNLOAD_VERIFY_FAILED)
    job.notebook_id = f'notebook-{index}'
    job.notebook_url = f'https://notebook.google.com/notebook/{job.notebook_id}'
    job.artifact_status = 'READY'
    job.error_code = 'DOWNLOAD_RETRY_EXHAUSTED' if exhausted else 'DOWNLOAD_VERIFY_FAILED'
    job.failure_class = 'RECOVERABLE_EXHAUSTED' if exhausted else 'RECOVERABLE_DOWNLOAD'
    job.download_attempt_count = 6 if exhausted else 1
    job.download_retry_count = 5 if exhausted else 0
    return job


def test_download_verify_failed_is_recoverable_not_terminal(tmp_path):
    job = failed_download(tmp_path)
    assert manual_download_recovery_available(job)
    assert automatic_download_recovery_candidate(job)
    assert not terminal(job)
    result = final_discovery([job], NOW)
    assert not result['complete']
    assert result['runnable'] == [job.id]


def test_eleven_ready_verify_failed_prevent_completion_and_are_candidates(tmp_path):
    jobs = [failed_download(tmp_path, index) for index in range(11)]
    result = final_discovery(jobs, NOW)
    assert not result['complete']
    assert len(result['unfinished']) == len(result['runnable']) == 11
    assert not result['attention_required']


def test_retry_exhausted_requires_attention_and_allows_manual_retry(tmp_path):
    jobs = [failed_download(tmp_path, index, exhausted=True) for index in range(11)]
    result = final_discovery(jobs, NOW)
    assert not result['complete']
    assert not result['runnable']
    assert len(result['attention_required']) == 11
    assert all(manual_download_recovery_available(job) for job in jobs)
    assert all(not terminal(job) for job in jobs)


def test_remote_ready_recovery_becomes_download_without_regeneration(tmp_path):
    job = failed_download(tmp_path)
    notebook = ManualNotebook({}, status='READY')
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, notebook)
    assert pipe._reconcile_download_verify_failed(job, allow_download=True) == 'READY'
    saved = jobs.get(job.id)
    assert saved.state is JobState.DOWNLOAD_PENDING
    assert notebook.submit_calls == []
    assert saved.error_code is None


def test_remote_generating_and_failed_recovery_checkpoints(tmp_path):
    generating = failed_download(tmp_path, 1)
    jobs = MemoryJobs(generating)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}, status='GENERATING'))
    pipe._reconcile_download_verify_failed(generating, allow_download=True)
    assert jobs.get(generating.id).state is JobState.WAITING_VIDEO

    failed = failed_download(tmp_path, 2)
    jobs = MemoryJobs(failed)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}, status='FAILED'))
    pipe._reconcile_download_verify_failed(failed, allow_download=True)
    saved = jobs.get(failed.id)
    assert saved.state is JobState.FAILED
    assert saved.resume_checkpoint == 'FAILED_ARTIFACT_RETRY'
    assert pipe.notebook.submit_calls == []


def test_exhausted_scan_keeps_attention_instead_of_resetting_budget(tmp_path):
    job = failed_download(tmp_path, exhausted=True)
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}, status='READY'))
    pipe._reconcile_download_verify_failed(job, allow_download=False)
    saved = jobs.get(job.id)
    assert saved.state is JobState.DOWNLOAD_VERIFY_FAILED
    assert saved.error_code == 'DOWNLOAD_RETRY_EXHAUSTED'
    assert saved.download_attempt_count == 6


def test_manual_retry_queue_is_durable_and_resets_separate_budget(tmp_path):
    job = failed_download(tmp_path, exhausted=True)
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}, status='READY'))
    result = pipe.enqueue_manual_save_retry(
        [job.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )
    saved = jobs.get(job.id)
    assert result['queued'] == [job.id]
    assert saved.manual_retry_requested
    assert saved.download_attempt_count == 6
    pipe._manual_retry_requests.clear()
    pipe.begin_run()
    assert job.id in pipe._manual_retry_requests
    pipe._prepare_manual_save_retry(saved, (False, False, False, False))
    assert jobs.get(job.id).download_attempt_count == 0
    assert pipe.notebook.submit_calls == []


def test_manual_retry_from_download_pending_starts_fresh_session(tmp_path):
    job = failed_download(tmp_path, exhausted=True)
    job.state = JobState.DOWNLOAD_PENDING
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}, status='READY'))
    pipe.enqueue_manual_save_retry(
        [job.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )
    # Prevent remote file I/O after proving that preparation reset the budget.
    pipe._run_notebook_job = lambda current: None
    pipe._drain_manual_retry_requests()
    saved = jobs.get(job.id)
    assert saved.manual_download_session == 1
    assert saved.download_attempt_count == 0
    assert saved.last_quality_result == 'MANUAL_SAVE_RETRY_REQUESTED'


def test_one_recovery_failure_does_not_stop_remaining_jobs(tmp_path):
    first = failed_download(tmp_path, 1, exhausted=True)
    second = failed_download(tmp_path, 2, exhausted=True)

    class OneBrokenNotebook(ManualNotebook):
        def inspect_status(self, job):
            if job.id == first.id:
                raise RuntimeError('first notebook unavailable')
            return 'READY'

    jobs = MemoryJobs(first, second)
    pipe = coordinator(tmp_path, jobs, OneBrokenNotebook({}))
    processed = pipe.run_recovery_cycle()
    assert processed == [first.id, second.id]
    assert 'first notebook unavailable' in jobs.get(first.id).error_message
    assert jobs.get(second.id).artifact_status == 'READY'


def test_manual_retry_rejects_missing_identity_with_feedback(tmp_path):
    job = Job(str(tmp_path / 'missing.txt'), state=JobState.DOWNLOAD_VERIFY_FAILED)
    pipe = coordinator(tmp_path, MemoryJobs(job), ManualNotebook({}))
    result = pipe.enqueue_manual_save_retry(
        [job.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )
    assert not result['queued']
    assert 'Notebook動画' in result['rejected'][job.id]


def test_gui_retryable_labels_counts_and_button_feedback(tmp_path):
    retryable = failed_download(tmp_path)
    exhausted = failed_download(tmp_path, 2, exhausted=True)
    summary = summarize_jobs([retryable, exhausted])
    assert summary.download_complete == 0
    assert summary.recoverable_errors == 2
    assert summary.terminal_errors == 0
    assert state_display(retryable) == '× 回収失敗・再試行可能'
    assert state_display(exhausted) == '× 自動回収失敗・手動リトライ可能'

    window, _settings, controller, bridge = _window(tmp_path, [retryable])
    controller.manual_save_retry = lambda ids: {'queued': ids, 'rejected': {}}
    window.job_table.item(0, 6).setCheckState(Qt.CheckState.Checked)
    assert window.save_retry_button.isEnabled()
    window.save_retry_button.click()
    _drain_until(lambda: not bridge.busy)
    assert '1件登録' in window.statusBar().currentMessage()
    assert '再回収可能: 1' in window.error_label.text()
    window.close()


def test_attention_required_has_distinct_stop_reason():
    assert '再回収' in MESSAGES['ATTENTION_REQUIRED']


def test_save_retry_and_unrecovered_commands_wake_active_scheduler(tmp_path):
    job = failed_download(tmp_path)
    service, _repo, pipe, _scheduler = service_controller(tmp_path, job)
    calls = []
    pipe.enqueue_manual_save_retry = lambda ids, **config: (
        calls.append(('manual', ids, config)) or {'queued': ids, 'rejected': {}}
    )
    pipe.request_unrecovered_scan = lambda: calls.append(('scan',))
    service._worker = SimpleNamespace(is_alive=lambda: True)
    try:
        result = service.manual_save_retry([job.id])
        assert result['queued'] == [job.id]
        assert service._command_wake.is_set()
        service._command_wake.clear()
        result = service.recover_pending()
        assert result['feedback'] == 'UNRECOVERED_SCAN_STARTED'
        assert service._command_wake.is_set()
        assert [call[0] for call in calls] == ['manual', 'scan']
    finally:
        service._worker = None


def test_only_manual_recovery_remaining_stops_as_attention_not_complete(tmp_path):
    job = failed_download(tmp_path, exhausted=True)
    service, _repo, _pipe, _scheduler = service_controller(tmp_path, job)
    service.start()
    deadline = time.monotonic() + 2
    while service.status()['active'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.status()['stop_reason']['code'] == 'ATTENTION_REQUIRED'


def test_quality_profile_is_loaded_from_config_not_hardcoded():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'src/djd_maker/core/download_quality.py').read_text(encoding='utf-8')
    app = (root / 'src/djd_maker/gui/app.py').read_text(encoding='utf-8')
    spec = (root / 'packaging/DJDMaker.spec').read_text(encoding='utf-8')
    assert '0.084634408639' not in source
    assert "load_quality_profile(root / 'config' / 'download-quality-profile.json')" in app
    assert 'download-quality-profile.json' in spec
