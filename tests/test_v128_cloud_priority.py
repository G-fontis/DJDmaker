from datetime import datetime, timedelta, UTC
import pytest

from djd_maker.core.cloud_limit import CloudLimitGate, LimitObservation
from djd_maker.core.models import Job, JobState
from djd_maker.orchestration.task_discovery import no_generation_checkpoint
from djd_maker.gui.presentation_models import CreditLimitViewModel
from test_pipeline import MemoryJobs, coordinator
from test_v122_sequential_runtime import Remote

NOW = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize('deadline', [None, '', 'invalid', '2026-09-01T00:00:00',
                                     '2026-09-01T00:00:00+00:00'])
def test_missing_or_expired_resume_time_rechecks_notebook(tmp_path, deadline):
    gate = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW)
    gate.state = dict(active=True, cloud_resume_at=deadline)
    gate._save()
    restored = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW)
    assert restored.blocked and restored.due
    assert restored.status()['reconciliation'] == 'LIMIT_UNKNOWN_NEEDS_RECHECK'


def test_current_quota_reply_has_bounded_backoff_not_immediate_resend(tmp_path):
    clock = [NOW]
    gate = CloudLimitGate(tmp_path/'limit.json', clock=lambda: clock[0])
    gate.block(LimitObservation('QUOTA_EXHAUSTED', None))
    assert gate.blocked and not gate.due
    clock[0] += timedelta(seconds=120)
    assert gate.due and gate.blocked  # UI proof is still required.


@pytest.mark.parametrize('available', [True, False, 'exception'])
def test_stale_limit_reconciles_only_with_current_available_ui(tmp_path, available):
    job = Job('pending.txt')
    remote = Remote([])
    pipe = coordinator(tmp_path, MemoryJobs(job), remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.cloud_limit.state = dict(active=True, message='old quota', cloud_resume_at=None,
                                  notebook_url='https://notebook.google.com/notebook/existing')
    calls = []
    def recheck(url):
        calls.append(url)
        if available == 'exception':
            raise RuntimeError('DOM unavailable')
        return available
    remote.recheck_cloud_limit = recheck
    pipe.run_cycle()
    assert len(calls) == 1
    assert pipe.capabilities.can_generate is (available is True)
    if available is True:
        assert remote.events[0] == ('submit', job.id)
        assert pipe.jobs.get(job.id).state is JobState.WAITING_VIDEO
        assert not pipe._needs_dispatch(pipe.jobs.get(job.id))  # Wait is now legitimate.
    else:
        assert not remote.events
        assert 0 < pipe.wait_seconds <= 120
        assert pipe.capabilities.can_download


def test_future_limit_deadline_is_not_overridden_by_unprocessed_jobs(tmp_path):
    remote = Remote([])
    pipe = coordinator(tmp_path, MemoryJobs(Job('pending.txt')), remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.cloud_limit.block(LimitObservation('current limit', NOW+timedelta(hours=1)))
    remote.recheck_cloud_limit = lambda _: pytest.fail('Before deadline')
    pipe.run_cycle()
    assert not remote.events
    assert pipe.cloud_limit.blocked


@pytest.mark.parametrize('label', ['Notebook待機', 'End待機', 'HLS/ZIP待機', '完了', ''])
def test_presentation_label_never_means_submitted(tmp_path, label):
    job = Job('pending.txt', presentation_stage=label, presentation_phase=label)
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    assert no_generation_checkpoint(job)
    assert pipe._needs_dispatch(job)


def test_393_completely_unprocessed_jobs_produce_393_candidates(tmp_path):
    jobs = [Job(f'lesson{i}.txt') for i in range(393)]
    pipe = coordinator(tmp_path, MemoryJobs(*jobs), Remote([]))
    pipe._scheduler_status(1, '生成投入')
    counts = pipe.scheduler_view['discovery']
    assert counts['total_jobs'] == counts['generation_candidates'] == 393
    assert counts['local_candidates'] == counts['download_candidates'] == 0


@pytest.mark.parametrize('state', [JobState.CREDIT_EXHAUSTED, JobState.RECOVERY_PENDING, JobState.FAILED])
def test_no_checkpoint_pre_generation_record_reconciles_to_not_started(tmp_path, state):
    job = Job('pending.txt', state=state)
    events = []
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    pipe.runtime_callback = events.append
    pipe._reconcile_unprocessed()
    assert pipe.jobs.get(job.id).state is JobState.WAITING
    assert pipe._needs_dispatch(pipe.jobs.get(job.id))
    assert events[0]['stage'] == 'STATE_RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('guard', ['duplicate', 'output', 'terminal', 'raw', 'zip', 'identity', 'sent'])
def test_reconciliation_preserves_nonempty_or_explicitly_blocked_records(tmp_path, guard):
    job = Job('pending.txt', state=JobState.RECOVERY_PENDING)
    if guard == 'duplicate': job.duplicate_of_job_id = 'owner'
    elif guard == 'output': job.failure_class = 'OUTPUT_BLOCKED'
    elif guard == 'terminal': job.failure_class = 'TERMINAL_FAILED'
    elif guard == 'raw': job.raw_path = str(tmp_path/'raw.mp4')
    elif guard == 'zip': job.zip_path = str(tmp_path/'done.zip')
    elif guard == 'identity': job.notebook_id = 'existing'
    else: job.resume_checkpoint = 'PRESET_SENT'
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    assert not no_generation_checkpoint(job)
    pipe._reconcile_unprocessed()
    assert job.state is JobState.RECOVERY_PENDING


@pytest.mark.parametrize('blocked', ['OUTPUT_BLOCKED', 'TERMINAL_FAILED'])
def test_blocked_waiting_not_dispatched_but_real_job_kept(tmp_path, blocked):
    a, b = Job('a.txt', failure_class=blocked), Job('b.txt')
    pipe = coordinator(tmp_path, MemoryJobs(a, b), Remote([]))
    assert not pipe._needs_dispatch(a)
    assert pipe._needs_dispatch(b)


@pytest.mark.parametrize('stage', [JobState.RAW_READY, JobState.HLS_ENCODING, JobState.DOWNLOAD_PENDING])
def test_generation_preempts_local_and_download(tmp_path, stage):
    pending = Job('pending.txt')
    other = Job('other.txt', state=stage)
    if stage is not JobState.DOWNLOAD_PENDING:
        raw = tmp_path/'raw.mp4'; raw.write_bytes(b'raw')
        other.raw_path = str(raw)
    remote = Remote([])
    pipe = coordinator(tmp_path, MemoryJobs(other, pending), remote)
    attempts = []
    def submit(job):
        attempts.append(job.id)
        assert job.id == pending.id
        assert not pipe.ending.calls
        assert not any(event[0] == 'download' for event in remote.events)
        raise RuntimeError('Stop after recording first priority')
    remote.submit = submit
    pipe.run_cycle()
    assert attempts and attempts[0] == pending.id
    assert pipe.jobs.get(pending.id).error_code is not None


def test_unknown_limit_view_says_recheck_not_indefinite_pause():
    view = CreditLimitViewModel.from_status(dict(active=True, cloud_resume_at=None))
    assert '再確認必要' in view.text
    assert 'CLOUD PAUSED' not in view.text


def test_wait_final_gate_rechecks_capability_before_sleep(tmp_path):
    job = Job('pending.txt')
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    pipe.cloud_limit.clock = lambda: NOW
    pipe.cloud_limit.state = dict(active=True)
    pipe.notebook.recheck_cloud_limit = lambda _: True
    pipe._discover_remaining_tasks()
    assert pipe.capabilities.can_generate
    assert pipe.wait_seconds == 0


@pytest.mark.parametrize('error', ['NO_OP_JOB_TRANSITION', 'SUBMISSION_STATE_UNCERTAIN',
                                  'NOTEBOOK_STAGE_FAILED', 'LOCAL_FILE_MISSING'])
def test_recorded_failure_is_not_erased_as_completely_unprocessed(tmp_path, error):
    job = Job('pending.txt', state=JobState.FAILED, error_code=error)
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    assert not no_generation_checkpoint(job)
    pipe._reconcile_unprocessed()
    assert pipe.jobs.get(job.id).state is JobState.FAILED
    assert pipe.jobs.get(job.id).error_code == error
