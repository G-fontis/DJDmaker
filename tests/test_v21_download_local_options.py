from __future__ import annotations

from pathlib import Path

import pytest

from djd_maker.core.download_policy import (
    ACCEPTED_AFTER_3_SMALL_DOWNLOADS,
    MAX_DOWNLOAD_ATTEMPTS,
    MIN_DOWNLOAD_BYTES,
    PASSED_MIN_SIZE,
    decide_download_size,
)
from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.core.repositories import SettingsRepository
from djd_maker.core.interfaces import MediaResult
from test_pipeline import MemoryJobs, coordinator, full_gate


class SizedDownloads:
    def __init__(self, sizes):
        self.sizes = list(sizes)
        self.calls = 0

    def download_artifact(self, _job, destination):
        size = self.sizes[self.calls]
        self.calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'x' * size)
        return destination


@pytest.mark.parametrize('attempt', (1, 2))
def test_small_download_retries_before_third(attempt):
    result = decide_download_size(MIN_DOWNLOAD_BYTES - 1, attempt)
    assert result.retry and not result.accepted


def test_third_small_download_is_accepted_for_validation_not_declared_valid():
    result = decide_download_size(MIN_DOWNLOAD_BYTES - 1, 3)
    assert result.accepted and not result.retry
    assert result.status == ACCEPTED_AFTER_3_SMALL_DOWNLOADS


def test_download_over_10mib_passes_first_size_gate(tmp_path):
    job = Job('large.txt', state=JobState.DOWNLOADING)
    jobs = MemoryJobs(job)
    notebook = SizedDownloads([MIN_DOWNLOAD_BYTES])
    pipe = coordinator(tmp_path, jobs, notebook)
    target = tmp_path / 'work' / job.id / 'download' / 'large.mp4'
    pipe._download_with_size_gate(job, target)
    saved = jobs.get(job.id)
    assert notebook.calls == 1
    assert saved.download_attempt_count == 1
    assert saved.download_size_gate_status == PASSED_MIN_SIZE


def test_three_small_downloads_stop_without_fourth_and_replace_atomically(tmp_path):
    job = Job('small.txt', state=JobState.DOWNLOADING)
    jobs = MemoryJobs(job)
    sizes = [101, 202, 303]
    notebook = SizedDownloads(sizes)
    pipe = coordinator(tmp_path, jobs, notebook)
    target = tmp_path / 'work' / job.id / 'download' / 'small.mp4'
    pipe._download_with_size_gate(job, target)
    saved = jobs.get(job.id)
    assert notebook.calls == MAX_DOWNLOAD_ATTEMPTS
    assert target.stat().st_size == sizes[-1]
    assert saved.download_attempt_count == 3
    assert saved.small_download_attempts == 3
    assert saved.download_size_gate_status == ACCEPTED_AFTER_3_SMALL_DOWNLOADS
    assert list(target.parent.glob('*.crdownload')) == []
    assert list(target.parent.glob('*attempt*')) == []
    pipe._download_with_size_gate(saved, target)
    assert notebook.calls == 3


def test_download_attempt_state_survives_json_roundtrip():
    job = Job('a.txt', download_attempt_count=2, small_download_attempts=2,
              download_size_gate_status='RETRY_SMALL_DOWNLOAD')
    loaded = Job.from_dict(job.to_dict())
    assert (loaded.download_attempt_count, loaded.small_download_attempts) == (2, 2)
    assert loaded.download_size_gate_status == 'RETRY_SMALL_DOWNLOAD'


@pytest.mark.parametrize('ending,tail,encode', [
    (False, False, False), (True, False, False), (False, True, False),
    (False, False, True), (True, True, False), (True, False, True),
    (False, True, True), (True, True, True),
])
@pytest.mark.parametrize('hls', [False, True])
def test_all_sixteen_local_and_hls_contracts(tmp_path, ending, tail, encode, hls):
    class OptionsEngine:
        def __init__(self):
            self.calls = []
        def process_options(self, raw, ending_video, output, **options):
            self.calls.append(options)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw.read_bytes() + (b'e' if options['ending_enabled'] else b''))
            return MediaResult(output, 1.0, output.stat().st_size, .4, .9)
        def process(self, raw, ending_video, output, padding_seconds):
            self.calls.append(dict(ending_enabled=True, tail_cut_enabled=True,
                                   encode_enabled=True, padding_seconds=padding_seconds))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw.read_bytes() + b'e')
            return MediaResult(output, 1.0, output.stat().st_size, .4, .9)

    case = f'{int(ending)}{int(tail)}{int(encode)}-{int(hls)}'
    raw = tmp_path / case / 'raw.mp4'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'raw')
    source = tmp_path / case / (case + '.txt')
    source.write_text('test', encoding='utf-8')
    job = Job(str(source), state=JobState.RAW_READY, raw_path=str(raw), safety_gate=full_gate())
    jobs = MemoryJobs(job)
    engine = OptionsEngine()
    pipe = coordinator(tmp_path / case, jobs, SizedDownloads([]), engine)
    pipe._run_local_options = (ending, tail, encode)
    pipe._run_hls_zip_enabled = hls
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED, saved.error_message
    assert bool(saved.zip_path) is hls
    assert bool(saved.final_mp4_path) is (not hls)
    assert saved.ending_result.startswith('PASS') is ending
    assert saved.tail_cut_result.startswith('PASS') is tail
    assert saved.encode_result.startswith('PASS') is encode
    assert len(engine.calls) == int(any((ending, tail, encode)))


def test_local_settings_persist_and_legacy_migration_follows_old_pipeline(tmp_path):
    path = tmp_path / 'settings.json'
    repo = SettingsRepository(path)
    value = AppSettings(ending_enabled=True, tail_cut_enabled=False, encode_enabled=True)
    repo.save(value)
    loaded = repo.load()
    assert (loaded.ending_enabled, loaded.tail_cut_enabled, loaded.encode_enabled) == (True, False, True)

    document = path.read_text(encoding='utf-8')
    import json
    old = json.loads(document)
    for key in ('ending_enabled', 'tail_cut_enabled', 'encode_enabled'):
        old['settings'].pop(key)
    old['settings']['ending_video'] = 'ending.mp4'
    path.write_text(json.dumps(old), encoding='utf-8')
    migrated = repo.load()
    assert migrated.ending_enabled and migrated.tail_cut_enabled and migrated.encode_enabled


@pytest.mark.parametrize('phase', ['PHASE1', 'PHASE2'])
def test_both_guis_share_and_lock_all_local_settings(qtbot, phase):
    from djd_maker.gui.dialogs import SettingsDialog
    settings = AppSettings(gui_type=phase, ending_enabled=True,
                           tail_cut_enabled=False, encode_enabled=True)
    dialog = SettingsDialog(settings, processing=True)
    qtbot.addWidget(dialog)
    assert dialog.ending_checkbox.isChecked()
    assert not dialog.tail_cut_checkbox.isChecked()
    assert dialog.encode_checkbox.isChecked()
    assert not dialog.ending_checkbox.isEnabled()
    assert not dialog.tail_cut_checkbox.isEnabled()
    assert not dialog.encode_checkbox.isEnabled()
    value = dialog.value()
    assert (value.ending_enabled, value.tail_cut_enabled, value.encode_enabled) == (True, False, True)


def test_current_window_contract_has_no_history_dependency():
    from djd_maker.adapters.browser import BrowserManager
    names = set(BrowserManager.runtime_status.__code__.co_names)
    banned = {'history', 'last_closed', 'closed_at', 'was_closed',
              'previous_auth', 'previous_session', 'close_history'}
    assert names.isdisjoint(banned)
