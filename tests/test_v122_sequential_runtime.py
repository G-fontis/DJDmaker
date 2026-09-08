"""Check -> Act -> Persist -> Next, using isolated state and remote fixtures."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from djd_maker.core.models import Job, JobState
from djd_maker.core.cancellation import CancellationToken, cancellation_scope, checkpoint, RunCancelled
from djd_maker.orchestration.pipeline import NoOpJobTransitionError
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from djd_maker.adapters.notebook_modal import BlockingModalError
from test_pipeline import MemoryJobs, coordinator
from test_gui import _window


def failed(name, error='PRESET_SEND_FAILED'):
    return Job(name + '.txt', state=JobState.FAILED, notebook_id=name,
               notebook_url='https://notebook.google.com/notebook/' + name, error_code=error)


class Remote:
    def __init__(self, events, statuses=None):
        self.events = events
        self.statuses = statuses or {}

    def diagnose_resume(self, job):
        self.events.append(('check', job.id))
        return dict(artifact=self.statuses.get(job.id, 'NOT_STARTED'), source='READY', preset='NOT_SENT')

    def submit(self, job):
        self.events.append(('submit', job.id))
        return job.notebook_id or job.id, job.notebook_url or 'https://notebook.google.com/notebook/' + job.id

    def inspect_status(self, job):
        self.events.append(('poll', job.id))
        return self.statuses.get(job.id, 'GENERATING')

    def download_artifact(self, job, destination):
        self.events.append(('download', job.id))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'fixture video')

    def delete_video_artifact(self, job, gate):
        assert gate.remote_deletion_allowed
        self.events.append(('delete', job.id))


@pytest.mark.parametrize('error', ['PRESET_SEND_FAILED', 'SOURCE_UPLOAD_FAILED', 'QUOTA_EXHAUSTED'])
def test_inspect_then_act_same_job_no_full_prescan(tmp_path, error):
    a, b = failed('a', error), failed('b', error)
    events = []
    jobs = MemoryJobs(a, b)
    pipeline = coordinator(tmp_path, jobs, Remote(events))
    pipeline.scheduler = PersistentPollScheduler(jobs)
    records = []
    pipeline.runtime_callback = records.append
    pipeline.run_cycle()
    assert events == [('check', a.id), ('submit', a.id), ('check', b.id), ('submit', b.id)]
    assert jobs.get(a.id).runtime_outcome == 'WAITING_VIDEO'
    assert jobs.get(a.id).next_poll_at
    assert any(record.get('processed') == 2 for record in records)
    assert all(j.runtime_outcome for j in jobs.list())
    pipeline.run_cycle()
    assert len(events) == 4  # no double pass or reopening before deadline


def test_ready_artifact_deferred_until_next_job_dispatched(tmp_path):
    a, b = failed('ready'), failed('next')
    jobs = MemoryJobs(a, b)
    events = []
    class CheckCompletion(Remote):
        def diagnose_resume(self, job):
            if job.id == b.id:
                assert jobs.get(a.id).state is JobState.DOWNLOAD_PENDING
                assert jobs.get(a.id).runtime_reason == 'REMOTE_ARTIFACT_READY'
            return super().diagnose_resume(job)
    pipeline = coordinator(tmp_path, jobs, CheckCompletion(events, {a.id: 'READY'}))
    pipeline.scheduler = PersistentPollScheduler(jobs)
    pipeline.run_cycle()
    assert events.index(('check', b.id)) < events.index(('download', a.id))
    assert events.index(('submit', b.id)) < events.index(('delete', a.id))


@pytest.mark.parametrize('state,reason', [(JobState.COMPLETED, 'COMPLETED_SKIP'), (JobState.FAILED, 'FATAL_FAILED')])
def test_completed_and_fatal_never_open(tmp_path, state, reason):
    job = failed('skip', 'FATAL_ERROR')
    job.state = state
    jobs = MemoryJobs(job)
    before = jobs.get(job.id).to_dict()
    events, records = [], []
    pipeline = coordinator(tmp_path, jobs, Remote(events))
    pipeline.runtime_callback = records.append
    pipeline.run_cycle()
    assert events == []
    assert any(r.get('decision') == reason for r in records)
    if state is JobState.COMPLETED:
        assert jobs.get(job.id).to_dict() == before


def test_generating_saves_deadline_and_waiting_not_reopened(tmp_path):
    job = failed('generating')
    jobs, events = MemoryJobs(job), []
    pipeline = coordinator(tmp_path, jobs, Remote(events, {job.id: 'GENERATING'}))
    pipeline.scheduler = PersistentPollScheduler(jobs)
    pipeline.run_cycle()
    assert jobs.get(job.id).next_poll_at
    pipeline.run_cycle()
    assert events == [('check', job.id)]
    assert jobs.get(job.id).runtime_reason == 'WAITING_FOR_NEXT_CHECK'


@pytest.mark.parametrize('state', [JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING])
def test_recovery_deadline_no_reopen(tmp_path, state):
    job = failed('reserved')
    job.state = state
    job.next_poll_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    jobs, events = MemoryJobs(job), []
    pipeline = coordinator(tmp_path, jobs, Remote(events))
    pipeline.run_cycle()
    pipeline.run_recovery_cycle()
    assert events == []


def test_noop_transition_isolated_without_stopping_other_jobs(tmp_path):
    values = [Job(f'noop{i}.txt') for i in range(10)]
    jobs, calls = MemoryJobs(*values), []
    pipeline = coordinator(tmp_path, jobs, Remote([]))
    pipeline._run_notebook_job = lambda job: calls.append(job.id)
    pipeline.run_cycle()
    assert len(calls) == 10
    assert all(jobs.get(j.id).state is JobState.FAILED for j in values)
    assert all(jobs.get(j.id).error_code == 'NO_OP_JOB_TRANSITION' for j in values)


def test_modal_never_causes_skip_and_stops_before_next(tmp_path):
    a, b = failed('modal'), failed('next')
    events = []
    class Modal(Remote):
        def diagnose_resume(self, job):
            self.events.append(('modal', job.id))
            raise BlockingModalError('BLOCKING_MODAL_DISMISS_TIMEOUT')
    pipeline = coordinator(tmp_path, MemoryJobs(a, b), Modal(events))
    with pytest.raises(BlockingModalError):
        pipeline.run_cycle()
    assert events == [('modal', a.id)]


def test_stop_prevents_next_navigation_and_persists_outcome(tmp_path):
    a, b = failed('stop'), failed('next')
    token, events, jobs = CancellationToken(), [], MemoryJobs(a, b)
    class Stop(Remote):
        def submit(self, job):
            self.events.append(('submit', job.id))
            token.request()
            checkpoint('chat.send')
    pipeline = coordinator(tmp_path, jobs, Stop(events))
    with cancellation_scope(token), pytest.raises(RunCancelled):
        pipeline.run_cycle()
    assert events == [('check', a.id), ('submit', a.id)]
    assert jobs.get(a.id).runtime_outcome == 'STOPPED'
    assert jobs.get(b.id).state is JobState.FAILED


@pytest.mark.parametrize('key,value', [
    ('job', 'lesson'), ('notebook', 'https://notebook.google.com/notebook/test'),
    ('phase', '再開処理'), ('stage', 'chat.send'), ('decision', 'SOURCE_ALREADY_READY'),
    ('next_action', '返信確認'), ('outcome', 'WAITING_VIDEO'), ('attempt', '2 / 3'),
    ('elapsed', 42), ('count', '1 / 3'),
])
def test_runtime_fields_visible(tmp_path, key, value):
    from djd_maker.core.runtime_operation import operation_text
    window, *_ = _window(tmp_path, [])
    try:
        record = dict(job='lesson', stage='chat.send', processed=1, total=3, elapsed=42)
        record[key] = value
        window._apply_runtime_status({'running': False, 'runtime': record})
        expected = '42秒' if key == 'elapsed' else operation_text(value)
        assert expected in window.runtime_labels[key][1].text()
    finally:
        window.close()


@pytest.mark.parametrize('stage', ['stop.requested', 'stop.wait', 'stop.complete', 'modal.found', 'modal.dismiss', 'modal.ready'])
def test_runtime_message_and_stop_status_visible(tmp_path, stage):
    from djd_maker.core.runtime_operation import operation_text
    window, *_ = _window(tmp_path, [])
    try:
        record = dict(stage=stage, job='lesson', decision='WAITING_FOR_NEXT_CHECK')
        window._append_runtime_message({'runtime': record})
        assert operation_text(stage) in window.runtime_messages.toPlainText()
        assert '次回確認時刻まで待機' in window.runtime_messages.toPlainText()
    finally:
        window.close()


def test_100_jobs_simulated_navigation_and_first_action_improve(tmp_path):
    results = {}
    for mode in ['old', 'new']:
        jobs = MemoryJobs(*(failed(f'job{i}') for i in range(100)))
        metrics = dict(time=0, opens=0, current=None, first=None)
        class Timed(Remote):
            def open(self, job):
                if metrics['current'] != job.id:
                    metrics['opens'] += 1
                    metrics['current'] = job.id
            def diagnose_resume(self, job):
                self.open(job)
                metrics['time'] += 33  # simulated, not a live timing claim
                return super().diagnose_resume(job)
            def submit(self, job):
                self.open(job)
                if metrics['first'] is None:
                    metrics['first'] = metrics['time']
                return super().submit(job)
        pipeline = coordinator(tmp_path / mode, jobs, Timed([]))
        pipeline.scheduler = PersistentPollScheduler(jobs)
        if mode == 'old':
            pipeline.resume_failed_jobs()  # reproduce the retired startup call
        pipeline.run_cycle()
        results[mode] = metrics
    assert results['old']['opens'] == 200
    assert results['new']['opens'] == 100
    assert results['old']['first'] == 3300
    assert results['new']['first'] == 33


@pytest.mark.parametrize('source_state', ['MISSING', 'READY'])
@pytest.mark.parametrize('old_reply', ['NO_RESPONSE', 'QUOTA_EXHAUSTED'])
def test_real_submit_adapter_source_and_preset_before_next(tmp_path, source_state, old_reply):
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter, GenerationOutcome, RemoteVideoStatus
    from djd_maker.adapters.credit import CreditSnapshot
    events = []
    values = [failed('first'), failed('second')]
    jobs = MemoryJobs(*values)
    class Dom:
        ensure_source = NotebookDomAdapter.ensure_source
        current = None
        states = {}
        def ensure_interactable(self):
            pass
        def diagnostic(self, _event):
            pass
        def source_state(self, filename):
            return self.states.get(filename, source_state)
        def upload_txt(self, source):
            events.append(('upload', self.current))
            self.states[source.name] = 'READY'
        def wait_for_source_ready(self, filename):
            events.append(('source_ready', self.current))
            assert self.source_state(filename) == 'READY'
        def start_video_generation_from_chat(self, prompt):
            assert prompt == 'test body'
            events.append(('send_current', self.current))
            return GenerationOutcome(RemoteVideoStatus.GENERATING, False, CreditSnapshot())
    dom = Dom()
    class Engine(NotebookEngineAdapter):
        def _open_job(self, job):
            dom.current = job.id
        def inspect_status(self, job):
            return 'NOT_STARTED'
        def diagnose_resume(self, job):
            if job.id == values[1].id:
                assert jobs.get(values[0].id).state is JobState.WAITING_VIDEO
                assert ('send_current', values[0].id) in events
            return dict(artifact='NOT_STARTED', source=source_state, preset='NOT_SENT', reply=old_reply)
    pipeline = coordinator(tmp_path, jobs, Engine(dom, persist_identity=jobs.save))
    for job in values:
        source = tmp_path / job.source_path
        source.write_text('test source', encoding='utf-8')
        job.source_path = str(source)
        job.snapshot_preset(pipeline.generation_preset)
        jobs.save(job)
    pipeline.scheduler = PersistentPollScheduler(jobs)
    pipeline.run_cycle()
    assert sum(e[0] == 'upload' for e in events) == (2 if source_state == 'MISSING' else 0)
    assert events.index(('source_ready', values[0].id)) < events.index(('send_current', values[0].id))
    assert events.index(('send_current', values[0].id)) < events.index(('source_ready', values[1].id))


def test_cached_fatal_classification_prevents_remote_open(tmp_path):
    job = failed('fatal')
    job.failure_class = 'FATAL_FAILED'
    events = []
    coordinator(tmp_path, MemoryJobs(job), Remote(events)).run_cycle()
    assert events == []


def test_idle_status_not_spammed_and_no_repeated_disk_save(tmp_path):
    job = failed('later')
    job.state = JobState.WAITING_VIDEO
    job.next_poll_at = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
    job.generation_started_at = datetime.now(UTC).isoformat()
    events, records = [], []
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote(events))
    pipeline.scheduler = PersistentPollScheduler(jobs)
    pipeline.runtime_callback = records.append
    pipeline.run_cycle()
    count = len(records)
    for _ in range(10):
        pipeline.run_cycle()
    assert len(records) == count
    assert events == []


def test_runtime_outcome_does_not_overwrite_completed_txt_move(tmp_path):
    source = tmp_path / 'lesson.txt'
    source.write_text('test source', encoding='utf-8')
    job = failed('lesson')
    job.source_path = str(source)
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote([], {job.id: 'READY'}))
    pipeline.run_cycle()
    result = jobs.get(job.id)
    assert result.state is JobState.COMPLETED
    assert result.txt_move_status == 'MOVED'
    assert result.source_sha256
    assert result.archived_txt_path
    assert not source.exists()


def test_media_failure_not_retried_in_same_run_or_foreign_zip_adopted(tmp_path):
    from zipfile import ZipFile
    raw = tmp_path / 'raw.mp4'
    raw.write_bytes(b'raw')
    job = Job('lesson.txt', state=JobState.HLS_ENCODING, raw_path=str(raw), edited_path=str(raw))
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Remote([]))
    pipeline.paths.output_directory.mkdir()
    destination = pipeline.paths.output_directory / 'lesson.zip'
    with ZipFile(destination, 'w') as archive:
        archive.writestr('playlist.m3u8', 'foreign')
    before = destination.read_bytes()
    pipeline.run_cycle()
    pipeline.begin_run()
    pipeline.run_cycle()
    assert jobs.get(job.id).state is JobState.FAILED
    assert jobs.get(job.id).failure_class == 'FATAL_FAILED'
    assert destination.read_bytes() == before


def test_fatal_persists_across_schema_migration(tmp_path):
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.job_migration import migrate_jobs
    job = failed('fatal_media', 'MEDIA_STAGE_FAILED')
    job.raw_path = str(tmp_path / 'raw.mp4')
    job.failure_class = 'FATAL_FAILED'
    repository = JobRepository(tmp_path / 'system/jobs')
    repository.save(job)
    migrate_jobs(tmp_path / 'system')
    assert repository.get(job.id).failure_class == 'FATAL_FAILED'
