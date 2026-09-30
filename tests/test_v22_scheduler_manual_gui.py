from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from djd_maker.core.commands import CommandId, validate_payload, InterfaceError
from djd_maker.core.download_quality import DownloadQualityProfile
from djd_maker.core.models import Job, JobState
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from test_gui import _window
from test_pipeline import MemoryJobs, coordinator


def _ready_job(tmp_path: Path, name: str = 'manual.txt') -> Job:
    job = Job(str(tmp_path / name), state=JobState.COMPLETED)
    job.notebook_id = f'notebook-{job.id}'
    job.notebook_url = f'https://notebook.google.com/notebook/{job.notebook_id}'
    return job


def test_save_retry_payload_is_shared_command_contract():
    validate_payload(CommandId.SAVE_RETRY, {'job_ids': ['one', 'two']})
    with pytest.raises(InterfaceError):
        validate_payload(CommandId.SAVE_RETRY, {'job_ids': 'one'})


def test_phase1_checkbox_enables_save_retry_and_dispatches_exact_selection(tmp_path):
    jobs = [_ready_job(tmp_path, 'a.txt'), _ready_job(tmp_path, 'b.txt')]
    window, _settings, controller, bridge = _window(tmp_path, jobs)
    controller.manual_save_retry = lambda ids: controller.calls.append(('save_retry', ids))
    assert not window.save_retry_button.isEnabled()
    for row in range(2):
        window.job_table.item(row, 6).setCheckState(Qt.CheckState.Checked)
    assert window.save_retry_button.isEnabled()
    window.save_retry_button.click()
    from test_gui import _drain_until
    _drain_until(lambda: not bridge.busy)
    call = next(value for value in controller.calls if isinstance(value, tuple))
    assert call[0] == 'save_retry'
    assert set(call[1]) == {job.id for job in jobs}
    window.close()


def test_phase2_contains_the_same_save_retry_button(tmp_path):
    jobs = [_ready_job(tmp_path)]
    window, _settings, _controller, _bridge = _window(tmp_path, jobs)
    assert window.switch_gui('PHASE2')
    assert window.save_retry_button.text() == '保存リトライ'
    window.close()


class ManualNotebook(FakeNotebookAdapter):
    def __init__(self, fixture_by_source, status='READY'):
        super().__init__(fixture_by_source)
        self.status = status
        self.inspect_calls = []

    def inspect_status(self, job):
        self.inspect_calls.append(job.id)
        return self.status


def test_manual_prepare_is_download_only_and_resets_exhausted_budget(tmp_path):
    job = _ready_job(tmp_path)
    job.download_attempt_count = 6
    job.download_retry_count = 5
    job.error_code = 'DOWNLOAD_RETRY_EXHAUSTED'
    job.failure_class = 'TERMINAL_FAILED'
    jobs = MemoryJobs(job)
    notebook = ManualNotebook({})
    pipe = coordinator(tmp_path, jobs, notebook)
    pipe._prepare_manual_save_retry(job, (False, True, False, True))
    saved = jobs.get(job.id)
    assert notebook.inspect_calls == [job.id]
    assert notebook.submit_calls == []
    assert saved.state is JobState.DOWNLOAD_PENDING
    assert saved.download_attempt_count == saved.download_retry_count == 0
    assert saved.manual_retry_requested
    assert (saved.ending_enabled, saved.tail_cut_enabled,
            saved.encode_enabled, saved.hls_zip_enabled) == (False, True, False, True)


def test_manual_prepare_refuses_not_ready_without_generation(tmp_path):
    job = _ready_job(tmp_path)
    jobs = MemoryJobs(job)
    notebook = ManualNotebook({}, status='GENERATING')
    pipe = coordinator(tmp_path, jobs, notebook)
    with pytest.raises(ValueError, match='Download'):
        pipe._prepare_manual_save_retry(job, (False, False, False, False))
    assert notebook.submit_calls == [] and notebook.download_calls == []


def test_manual_queue_keeps_multiple_selected_jobs(tmp_path):
    first, second = _ready_job(tmp_path, 'a.txt'), _ready_job(tmp_path, 'b.txt')
    pipe = coordinator(tmp_path, MemoryJobs(first, second), ManualNotebook({}))
    pipe.enqueue_manual_save_retry(
        [first.id, second.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )
    assert set(pipe._manual_retry_requests) == {first.id, second.id}


def test_manual_retry_transaction_preserves_old_raw_and_output_on_commit_failure(
    tmp_path, monkeypatch
):
    job = _ready_job(tmp_path)
    job.manual_download_session = 1
    job.manual_retry_requested = True
    job.ending_enabled = job.tail_cut_enabled = job.encode_enabled = False
    job.hls_zip_enabled = False
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}))
    formal_raw = pipe.paths.raw_directory / 'manual.mp4'
    formal_output = pipe.paths.output_directory / 'completed_mp4' / 'manual.mp4'
    formal_raw.parent.mkdir(parents=True, exist_ok=True)
    formal_output.parent.mkdir(parents=True, exist_ok=True)
    formal_raw.write_bytes(b'old-good-raw')
    formal_output.write_bytes(b'old-good-output')
    download = tmp_path / 'new-download.mp4'
    download.write_bytes(b'new-valid-video')

    import djd_maker.orchestration.pipeline as pipeline_module
    real_replace = pipeline_module.os.replace

    def fail_final_output(source, target):
        if Path(target) == formal_output:
            raise PermissionError('simulated publish collision')
        return real_replace(source, target)

    monkeypatch.setattr(pipeline_module.os, 'replace', fail_final_output)
    with pytest.raises(PermissionError, match='simulated'):
        pipe._commit_manual_retry(job, download)
    assert formal_raw.read_bytes() == b'old-good-raw'
    assert formal_output.read_bytes() == b'old-good-output'


def test_manual_retry_hls_off_publishes_mp4_and_retains_remote_artifact(tmp_path):
    job = _ready_job(tmp_path)
    job.manual_download_session = 1
    job.manual_retry_requested = True
    job.ending_enabled = job.tail_cut_enabled = job.encode_enabled = False
    job.hls_zip_enabled = False
    jobs = MemoryJobs(job)
    notebook = ManualNotebook({})
    pipe = coordinator(tmp_path, jobs, notebook)
    download = tmp_path / 'new-download.mp4'
    download.write_bytes(b'new-valid-video')
    pipe._commit_manual_retry(job, download)
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert saved.final_mp4_path and Path(saved.final_mp4_path).is_file()
    assert saved.zip_path is None
    assert saved.hls_result == saved.zip_result == 'SKIPPED_BY_SETTING'
    assert notebook.artifact_delete_calls == []


def test_manual_retry_hls_on_reprocesses_local_options_and_zip(tmp_path):
    class OptionEnding:
        def __init__(self):
            self.calls = []

        def process_options(self, raw, ending, output, **options):
            self.calls.append(options)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw.read_bytes() + b'-processed')
            return type('Media', (), {'path': output})()

    job = _ready_job(tmp_path)
    job.manual_download_session = 1
    job.manual_retry_requested = True
    job.ending_enabled = job.tail_cut_enabled = job.encode_enabled = True
    job.hls_zip_enabled = True
    jobs = MemoryJobs(job)
    notebook = ManualNotebook({})
    ending = OptionEnding()
    pipe = coordinator(tmp_path, jobs, notebook, ending=ending)
    download = tmp_path / 'new-download.mp4'
    download.write_bytes(b'new-valid-video')
    pipe._commit_manual_retry(job, download)
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert saved.zip_path and Path(saved.zip_path).is_file()
    assert saved.final_mp4_path is None
    assert saved.hls_result == saved.zip_result == 'PASS'
    assert ending.calls == [{
        'ending_enabled': True, 'tail_cut_enabled': True,
        'encode_enabled': True, 'padding_seconds': .5,
    }]
    assert notebook.artifact_delete_calls == []


def test_quality_deadline_blocks_early_open_and_prevents_false_idle(tmp_path):
    job = _ready_job(tmp_path)
    job.state = JobState.DOWNLOADING
    job.download_candidate_path = str(tmp_path / '.candidate.mp4')
    job.next_quality_check_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    jobs = MemoryJobs(job)
    notebook = ManualNotebook({})
    pipe = coordinator(tmp_path, jobs, notebook)
    pipe.download_quality_profile = DownloadQualityProfile(2, .1, .01, .05, 'fixed')
    pipe._discover_remaining_tasks()
    assert notebook.inspect_calls == []
    assert pipe.wait_seconds > 0
    assert pipe.scheduler_view['exclusion_reasons'][job.id] == 'QUALITY_CHECK_NOT_DUE'


def test_manual_queue_is_runnable_and_suppresses_wait(tmp_path, monkeypatch):
    job = _ready_job(tmp_path)
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}))
    pipe.enqueue_manual_save_retry(
        [job.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )
    monkeypatch.setattr(pipe, '_drain_manual_retry_requests', lambda: True)
    pipe.run_cycle()
    assert pipe.wait_seconds == 0


def test_manual_failure_leaves_queue_and_reports_instead_of_looping(tmp_path, monkeypatch):
    job = _ready_job(tmp_path)
    jobs = MemoryJobs(job)
    pipe = coordinator(tmp_path, jobs, ManualNotebook({}))
    runtime = []
    pipe.runtime_callback = runtime.append
    pipe.enqueue_manual_save_retry(
        [job.id], ending_enabled=False, tail_cut_enabled=False,
        encode_enabled=False, hls_zip_enabled=False,
    )

    def fail_download(current):
        current.state = JobState.FAILED
        current.error_code = 'REMOTE_ACCESS_UNAVAILABLE'
        current.error_message = 'Chrome download timed out'
        jobs.save(current)

    monkeypatch.setattr(pipe, '_run_notebook_job', fail_download)
    assert pipe._drain_manual_retry_requests()
    assert not pipe._manual_retry_requests
    saved = jobs.get(job.id)
    assert not saved.manual_retry_requested
    assert saved.last_quality_result == 'MANUAL_SAVE_RETRY_FAILED'
    assert pipe._manual_retry_failures[job.id] == 'Chrome download timed out'
    assert any(event['stage'] == 'manual.download.failed' for event in runtime)


def test_no_local_work_does_not_leave_collect_local_sticky(tmp_path):
    job = _ready_job(tmp_path)
    pipe = coordinator(tmp_path, MemoryJobs(job), ManualNotebook({}))
    pipe.phase = 'COLLECT_LOCAL'
    pipe._drain_limit_local(local_only=True)
    assert pipe.phase == 'SCHEDULER'


def test_download_13_retry_8_screenshot_scenario_never_waits(tmp_path, monkeypatch):
    downloads = []
    for index in range(13):
        job = _ready_job(tmp_path, f'download-{index}.txt')
        job.state = JobState.DOWNLOAD_PENDING
        downloads.append(job)
    retries = []
    for index in range(8):
        job = Job(str(tmp_path/f'retry-{index}.txt'), state=JobState.FAILED)
        job.error_code = 'NOTEBOOK_STAGE_FAILED'
        job.failure_class = 'PRESET_SEND_FAILED'
        job.next_poll_at = '2000-01-01T00:00:00+00:00'
        retries.append(job)
    pipe = coordinator(tmp_path, MemoryJobs(*(downloads + retries)), ManualNotebook({}))
    selected = []
    monkeypatch.setattr(
        pipe, '_run_cycle_lane',
        lambda selected_jobs=None, allow_collection=False: selected.extend(selected_jobs or []),
    )
    monkeypatch.setattr(pipe, '_retry_deferred', lambda **_kwargs: None)
    pipe._discover_remaining_tasks()
    assert {job.id for job in downloads}.issubset({job.id for job in selected})
    assert pipe.wait_seconds == 0
    assert pipe.scheduler_view['priority'] in {3, 4}


def test_compact_gui_candidate_status_hides_raw_dicts_and_job_ids(tmp_path):
    job = _ready_job(tmp_path)
    window, _settings, _controller, _bridge = _window(tmp_path, [job])
    window._apply_runtime_status({'scheduler': {
        'task': '期限到達動画の確認・Download', 'terminal_failed': 0,
        'capabilities': {'can_generate': True, 'can_download': True},
        'retry_attempts': {'private-job-id': 2},
        'discovery': {'generation_candidates': 0, 'local_candidates': 0,
                      'download_candidates': 13, 'retry_candidates': 8},
    }})
    text = window.scheduler_label.text()
    assert 'Download13' in text and '復旧8' in text
    assert 'private-job-id' not in text
    assert 'can_generate' not in text and '{' not in text
    window.close()
