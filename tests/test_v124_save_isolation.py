from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import pytest
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobRepository, JobStateSaveError
from djd_maker.core.deferred_state import DeferredStateStore, SaveDeferred
from djd_maker.core.cancellation import CancellationToken, RunCancelled, cancellation_scope
from test_pipeline import MemoryJobs, coordinator
from djd_maker.testing.fake_notebook import FakeNotebookAdapter


class FaultJobs(MemoryJobs):
    def __init__(self, *jobs, bad=(), once_state=None, once_stage=None):
        super().__init__(*jobs)
        self.bad = set(bad)
        self.once_state = once_state
        self.once_stage = once_stage
        self.failed = False
        self.attempts = Counter()

    def save(self, job):
        self.attempts[job.id] += 1
        if job.id in self.bad or ((self.once_state == job.state or self.once_stage == job.presentation_stage) and not self.failed):
            self.failed = True
            raise JobStateSaveError('injected publish exhaustion')
        super().save(job)


def test_deferred_overlay_sort_selection_and_close(tmp_path, monkeypatch):
    from test_gui import _window
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    jobs = [Job(f'{i}.txt', id=str(i)) for i in range(4)]
    window, _, controller, _ = _window(tmp_path, jobs)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: pytest.fail('shutdown modal'))
    window.job_table.sortItems(5, Qt.SortOrder.AscendingOrder)
    window.job_table.selectRow(1)
    selected = window._selected_job().id
    window._deferred_overlays = {'1', '3'}
    window._refresh_summary()
    for row in range(window.job_table.rowCount()):
        identity = window.job_table.item(row,0).data(Qt.ItemDataRole.UserRole)
        assert ('状態保存待ち' in window.job_table.item(row,5).text()) == (identity in {'1','3'})
    assert window._selected_job().id == selected
    assert '状態保存保留 2件' in window.completion_label.text()
    window.close()
    assert 'shutdown' in controller.calls


def test_corrupt_journal_isolates_job_without_blind_submission(tmp_path):
    recovery = tmp_path/'recovery'
    recovery.mkdir()
    (recovery/'one.json').write_text('not json', encoding='utf-8')
    jobs = MemoryJobs(Job('one.txt', id='one'), Job('two.txt', id='two'))
    jobs._deferred_state = DeferredStateStore(recovery)
    remote = Remote()
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    assert pipeline.deferred_ids == {'one'}
    assert ('submit','one') not in remote.actions
    assert ('submit','two') in remote.actions


def test_reload_save_failure_retains_one_identity_and_recovers(tmp_path):
    from djd_maker.orchestration.gui_controller import GuiPipelineController
    from djd_maker.orchestration.scheduler import PersistentPollScheduler
    from djd_maker.core.settings import AppSettings
    source = tmp_path/'input'
    source.mkdir()
    for name in ('one','two'):
        (source/f'{name}.txt').write_text('fixture', encoding='utf-8')
    jobs = FaultJobs()
    original = jobs.save
    def save(job):
        if job.script_name == 'one':
            raise JobStateSaveError('fixture')
        original(job)
    jobs.save = save
    service = GuiPipelineController(jobs=jobs, settings=AppSettings(), app_root=tmp_path,
                                   scheduler=PersistentPollScheduler(jobs), pipeline=None, pipeline_factory=lambda:None)
    service.reload()
    service.reload()
    assert len(jobs.list()) == 1
    assert len(jobs._deferred_state.entries) == 1
    identity = next(iter(jobs._deferred_state.entries))
    jobs.save = original
    remote = Remote()
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    pipeline.run_cycle()
    assert jobs.get(identity) is not None
    assert remote.actions.count(('submit', identity)) == 1
    assert not pipeline.deferred_ids


class Remote(FakeNotebookAdapter):
    def __init__(self, sources=None, reserved=False):
        super().__init__(sources or {})
        self.actions = []
        self.reserved = reserved

    def submit(self, job):
        self.actions.append(('submit', job.id))
        return SimpleNamespace(notebook_id='nb-' + job.id,
            notebook_url='https://notebook.google.com/notebook/nb-' + job.id,
            reserved=self.reserved)

    def inspect_status(self, job):
        self.actions.append(('inspect', job.id))
        return 'WAITING' if self.reserved else 'GENERATING'


@pytest.mark.parametrize('bad', [('20',), ('20', '54', '88')])
def test_one_bad_job_does_not_block_100_jobs(tmp_path, bad):
    jobs = FaultJobs(*[Job(f'lesson{i}.txt', id=str(i)) for i in range(1, 101)], bad=bad)
    remote = Remote()
    pipeline = coordinator(tmp_path, jobs, remote)
    messages = []
    pipeline.runtime_callback = messages.append
    pipeline.run_cycle()
    assert pipeline.deferred_ids == set(bad)
    assert len([a for a in remote.actions if a[0] == 'submit']) == 100 - len(bad)
    assert all(jobs.get(i).state is JobState.WAITING for i in bad)
    assert all(jobs.attempts[i] == 3 for i in bad)
    pipeline.run_cycle()
    assert all(jobs.attempts[i] == 3 for i in bad)
    pipeline.deferred_summary()
    assert messages[-1]['deferred_count'] == len(bad)


@pytest.mark.parametrize('state,reserved', [(JobState.GENERATING, False), (JobState.CREDIT_EXHAUSTED, True)])
def test_remote_action_not_duplicated_after_failed_save(tmp_path, state, reserved):
    jobs = FaultJobs(Job('one.txt', id='one'), Job('two.txt', id='two'), once_state=state)
    remote = Remote(reserved=reserved)
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    pipeline.run_cycle()
    assert remote.actions.count(('submit', 'one')) == 1
    assert ('inspect', 'one') in remote.actions
    assert ('submit', 'two') in remote.actions
    assert not pipeline.deferred_ids


def test_journal_restart_reconciles_instead_of_submitting(tmp_path):
    jobs = FaultJobs(Job('one.txt', id='one'), once_state=JobState.GENERATING)
    remote = Remote()
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.deferred = DeferredStateStore(tmp_path / 'recovery')
    pipeline._retry_deferred = lambda: None
    pipeline.run_cycle()
    assert (tmp_path / 'recovery/one.json').is_file()
    jobs._deferred_state = DeferredStateStore(tmp_path / 'recovery')
    restarted = coordinator(tmp_path, jobs, remote)
    restarted.run_cycle()
    assert remote.actions.count(('submit', 'one')) == 1
    assert remote.actions.count(('inspect', 'one')) >= 1
    assert not restarted.deferred_ids


def test_journal_failure_retains_memory_and_continues(tmp_path, monkeypatch):
    jobs = FaultJobs(Job('one.txt', id='one'), Job('two.txt', id='two'), bad=('one',))
    pipeline = coordinator(tmp_path, jobs, Remote())
    pipeline.deferred = DeferredStateStore(tmp_path / 'journal')
    monkeypatch.setattr(pipeline.deferred, '_document', lambda *_: (_ for _ in ()).throw(PermissionError()))
    pipeline.run_cycle()
    assert 'one' in pipeline.deferred_ids
    assert not pipeline.deferred.entries['one'].journal_saved
    assert jobs.get('two').state is JobState.WAITING_VIDEO


@pytest.mark.parametrize('winerror', [5, 32, None])
def test_existing_retry_exhausts_before_defer(tmp_path, monkeypatch, winerror):
    jobs = JobRepository(tmp_path / 'system/jobs')
    job = Job('one.txt', id='one')
    jobs.save(job)
    calls = []
    real_replace = __import__('os').replace
    def replace_file(source, destination):
        if Path(destination) == jobs.directory / 'one.json':
            calls.append(destination)
            error = PermissionError('test contention')
            if winerror is not None:
                error.winerror = winerror
            raise error
        return real_replace(source, destination)
    monkeypatch.setattr('djd_maker.core.storage.os.replace', replace_file)
    monkeypatch.setattr('djd_maker.core.storage.time.sleep', lambda _: None)
    pipeline = coordinator(tmp_path, jobs, Remote())
    with pytest.raises(SaveDeferred):
        pipeline._transition(job, JobState.UPLOADING)
    assert len(calls) == 7
    assert jobs.get(job.id).state is JobState.WAITING
    assert pipeline.deferred.entries[job.id].journal_saved


def test_stop_prevents_deferred_retry_and_navigation(tmp_path):
    jobs = FaultJobs(Job('one.txt', id='one'), bad=('one',))
    remote = Remote()
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    token = CancellationToken()
    token.request()
    before = dict(jobs.attempts)
    with cancellation_scope(token), pytest.raises(RunCancelled):
        pipeline._retry_deferred()
    assert dict(jobs.attempts) == before
    assert not remote.actions


def test_uncertain_sent_chat_is_not_resent(tmp_path):
    job = Job('one.txt', id='one', notebook_id='nb-one', notebook_url='https://notebook.google.com/notebook/nb-one',
              presentation_stage='chat.sent', state=JobState.UPLOADING)
    jobs = MemoryJobs(job)
    remote = Remote()
    remote.inspect_status = lambda _: 'NOT_STARTED'
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline._defer(job, JobStateSaveError())
    pipeline.run_cycle()
    assert job.id in pipeline.deferred_ids
    assert not remote.actions


def test_gui_deferred_overlay_is_not_false_domain_success(tmp_path, monkeypatch):
    from test_gui import _window
    from PySide6.QtWidgets import QMessageBox
    job = Job('one.txt', id='one')
    window, *_ = _window(tmp_path, [job])
    monkeypatch.setattr(QMessageBox, 'critical', lambda *_: pytest.fail('blocking modal'))
    window._apply_runtime_status({'runtime': {'job_id': 'one', 'stage': 'save.deferred', 'decision': 'save pending'}})
    assert '状態保存待ち' in window.job_table.item(0, 5).text()
    assert window.jobs[0].state is JobState.WAITING
    window.update_job(job)
    assert '状態保存待ち' in window.job_table.item(0, 5).text()
    window._apply_runtime_status({'runtime': {'job_id': 'one', 'stage': 'save.recovered'}})
    assert '状態保存待ち' not in window.job_table.item(0, 5).text()
    window.close()


@pytest.mark.parametrize('stage', ['chat.send', 'chat.sent', 'generation.accepted', 'reservation.complete'])
def test_mid_submit_stage_failure_never_duplicates_chat(tmp_path, stage):
    from djd_maker.core.runtime_operation import report_operation
    class ChatRemote(Remote):
        def submit(self, job):
            result = super().submit(job)
            job.notebook_id, job.notebook_url = result.notebook_id, result.notebook_url
            report_operation(stage)
            return result
    jobs = FaultJobs(Job('one.txt', id='one'), once_stage=stage)
    remote = ChatRemote(reserved=stage == 'reservation.complete')
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    pipeline.run_cycle()
    assert remote.actions.count(('submit', 'one')) == 1
    assert ('inspect', 'one') in remote.actions
    assert not pipeline.deferred_ids


@pytest.mark.parametrize('stage', ['download.complete', 'raw.validate', 'RAW_READY', 'raw.saved', 'artifact.deleted'])
def test_download_raw_save_failure_reuses_same_download(tmp_path, stage):
    fixture = tmp_path / 'fixture.mp4'
    fixture.write_bytes(b'fixture media')
    source = tmp_path / 'one.txt'
    source.write_text('fixture', encoding='utf-8')
    jobs = FaultJobs(Job(str(source), id='one', state=JobState.DOWNLOAD_PENDING,
        notebook_id='nb-one', notebook_url='https://notebook.google.com/notebook/nb-one'), once_stage=stage)
    class OwnedRemote(FakeNotebookAdapter):
        pass
    remote = OwnedRemote({str(source):fixture}, status_by_job={'one':'READY'})
    import threading
    owner = threading.get_ident()
    for method in ('inspect_status', 'download_artifact', 'delete_video_artifact'):
        original = getattr(remote, method)
        def on_owner(*args, _original=original):
            assert threading.get_ident() == owner, 'browser used from a local-media worker'
            return _original(*args)
        setattr(remote, method, on_owner)
    pipeline = coordinator(tmp_path, jobs, remote)
    for _ in range(3):
        pipeline.run_cycle()
    assert remote.download_calls == ['one']
    assert jobs.get('one').state is JobState.COMPLETED
    assert Path(jobs.get('one').raw_path).read_bytes() == b'fixture media'
    assert not pipeline.deferred_ids


@pytest.mark.parametrize('stage', ['hls.complete', 'zip.start', 'zip.publish', 'zip.complete', 'COMPLETED'])
def test_validated_hls_zip_checkpoint_never_reencodes(tmp_path, monkeypatch, stage):
    import shutil
    from djd_maker.adapters import hls
    from djd_maker.packaging.portable_e2e import _make_fixture
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    assert ffmpeg and ffprobe
    raw = tmp_path / 'raw.mp4'
    _make_fixture(Path(ffmpeg), raw, 'blue', 1)
    from dataclasses import replace
    jobs = FaultJobs(Job('one.txt', id='one', state=JobState.RAW_READY, raw_path=str(raw)), once_stage=stage)
    pipeline = coordinator(tmp_path, jobs, Remote())
    pipeline.paths = replace(pipeline.paths, ending_video=None)
    pipeline.hls = hls.HlsAdapter(ffmpeg, ffprobe)
    calls = []
    original = hls._run
    def run(command, timeout):
        if '-hls_time' in command:
            calls.append(command)
        return original(command, timeout)
    monkeypatch.setattr(hls, '_run', run)
    for _ in range(3):
        pipeline.run_cycle()
    assert jobs.failed
    assert jobs.get('one').state is JobState.COMPLETED
    assert len(calls) == 1
    assert not pipeline.deferred_ids


def test_unknown_identity_stays_isolated_across_retries_and_restart(tmp_path):
    job = Job('one.txt', id='one', state=JobState.UPLOADING, presentation_stage='notebook.create')
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote())
    for _ in range(3):
        pipeline.run_cycle()
    assert 'one' in pipeline.deferred_ids
    assert not pipeline.notebook.actions


def test_reconciliation_exception_does_not_stop_others(tmp_path):
    jobs = FaultJobs(Job('one.txt', id='one'), Job('two.txt', id='two'), once_state=JobState.GENERATING)
    remote = Remote()
    original = remote.inspect_status
    def inspect(job):
        if job.id == 'one':
            raise RuntimeError('DOM transient')
        return original(job)
    remote.inspect_status = inspect
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.run_cycle()
    assert 'one' in pipeline.deferred_ids
    assert ('submit', 'two') in remote.actions


def test_retry_recovers_without_creating_deferred_entry(tmp_path, monkeypatch):
    import os
    jobs = JobRepository(tmp_path / 'system/jobs')
    job = Job('one.txt', id='one')
    jobs.save(job)
    original = os.replace
    count = []
    def replace_once(source, destination):
        if Path(destination) == jobs.directory / 'one.json':
            count.append(1)
            if len(count) < 3:
                raise PermissionError('transient')
        return original(source, destination)
    monkeypatch.setattr('djd_maker.core.storage.os.replace', replace_once)
    monkeypatch.setattr('djd_maker.core.storage.time.sleep', lambda _: None)
    pipeline = coordinator(tmp_path, jobs, Remote())
    pipeline._transition(job, JobState.UPLOADING)
    assert not pipeline.deferred_ids
    assert len(count) == 3


def test_cancel_interrupts_storage_retry_without_more_replace(tmp_path, monkeypatch):
    from djd_maker.core.storage import JsonStore
    token = CancellationToken()
    calls = []
    def fail(*args):
        calls.append(args)
        token.request()
        raise PermissionError('sharing')
    monkeypatch.setattr('djd_maker.core.storage.os.replace', fail)
    with cancellation_scope(token), pytest.raises(RunCancelled):
        JsonStore(tmp_path / 'job.json', replace_retry_delays=(.1, .2, .4, .8, 1.6, 3.2)).save({'safe':True})
    assert len(calls) == 1


def test_invalid_existing_zip_stays_deferred(tmp_path):
    from dataclasses import replace
    job = Job('one.txt', id='one', state=JobState.ZIPPING)
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote())
    output = pipeline.paths.output_directory / 'one.zip'
    output.parent.mkdir(parents=True)
    output.write_bytes(b'not zip')
    pipeline._defer(replace(job, presentation_stage='zip.complete'), JobStateSaveError())
    pipeline.run_cycle()
    assert 'one' in pipeline.deferred_ids
    assert jobs.get('one').state is JobState.ZIPPING
    assert output.read_bytes() == b'not zip'


def test_gui_controller_finishes_with_deferred_summary_not_error(tmp_path):
    from djd_maker.orchestration.gui_controller import GuiPipelineController
    from djd_maker.orchestration.scheduler import PersistentPollScheduler
    from djd_maker.core.settings import AppSettings
    fixture = tmp_path / 'fixture.mp4'
    fixture.write_bytes(b'valid fixture')
    jobs = FaultJobs(*[Job(f'{i}.txt', id=i, state=JobState.DOWNLOAD_PENDING,
        notebook_id=f'nb-{i}', notebook_url=f'https://notebook.google.com/notebook/nb-{i}') for i in ('bad','good')], bad=('bad',))
    remote = FakeNotebookAdapter({'good.txt':fixture}, status_by_job={'bad':'READY','good':'READY'})
    pipeline = coordinator(tmp_path, jobs, remote)
    errors, logs = [], []
    controller = GuiPipelineController(jobs=jobs, settings=AppSettings(), app_root=tmp_path,
        pipeline=pipeline, scheduler=PersistentPollScheduler(jobs))
    controller.bind(jobs=lambda _:None, status=lambda _:None, log=logs.append, error=lambda *args:errors.append(args))
    controller.start()
    worker = controller._worker or controller._retiring_worker
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert not errors
    assert jobs.get('good').state is JobState.COMPLETED
    assert any(item.get('stage') == 'save.summary' and item['level'] == 'WARNING' for item in logs)
    controller.shutdown()
    assert not worker.is_alive()


def test_stale_journal_cannot_roll_back_completed_job(tmp_path):
    job = Job('one.txt', id='one')
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote())
    pipeline._defer(job, JobStateSaveError())
    job.state = JobState.COMPLETED
    jobs.save(job)
    pipeline.run_cycle()
    assert not pipeline.notebook.actions
    assert jobs.get('one').state is JobState.COMPLETED
    assert not pipeline.deferred_ids
