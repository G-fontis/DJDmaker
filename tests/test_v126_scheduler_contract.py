"""V126 contracts use copy-only state; no authenticated or production data."""
import ast
import inspect
import textwrap
import threading
from datetime import timedelta

import pytest
from PySide6.QtWidgets import QPushButton

from djd_maker.adapters.browser import BrowserManager
from djd_maker.core.cancellation import CancellationToken, RunCancelled
from djd_maker.core.cloud_limit import parse_limit_text, CloudLimitReached
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import PresetRepository
from djd_maker.orchestration.task_discovery import Capabilities, RESCAN_SECONDS
from test_pipeline import coordinator, MemoryJobs
from test_v122_sequential_runtime import Remote
from test_v125_limit_pause_contract import NOW
from test_gui import _window


def test_chat_limit_does_not_disable_collection_capabilities():
    capabilities = Capabilities.from_limit(True)
    assert not any((capabilities.can_chat, capabilities.can_generate, capabilities.can_upload_source))
    assert all((capabilities.can_check_artifact, capabilities.can_download, capabilities.can_local_process))


@pytest.mark.parametrize('ready', [False, True])
def test_limit_due_collection_and_local_completion(tmp_path, ready):
    job = Job('due.txt', state=JobState.WAITING_VIDEO, notebook_id='due',
        notebook_url='https://notebook.google.com/notebook/due', next_poll_at=NOW.isoformat())
    remote = Remote([], {job.id: 'READY' if ready else 'GENERATING'})
    repo = MemoryJobs(job)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    pipe.run_cycle()
    assert ('poll', job.id) in remote.events
    assert not any(event[0] == 'submit' for event in remote.events)
    if ready:
        assert ('download', job.id) in remote.events
        assert repo.get(job.id).state is JobState.COMPLETED
        assert repo.get(job.id).safety_gate.remote_deletion_allowed
    else:
        assert repo.get(job.id).state is JobState.WAITING_VIDEO
        assert pipe.wait_seconds == RESCAN_SECONDS
        assert not pipe.all_tasks_completed()


def test_future_job_not_opened_and_not_auto_stopped(tmp_path):
    job = Job('future.txt', state=JobState.WAITING_VIDEO,
        next_poll_at=(NOW+timedelta(hours=1)).isoformat())
    remote = Remote([])
    pipe = coordinator(tmp_path, MemoryJobs(job), remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.run_cycle()
    assert not remote.events
    assert not pipe.all_tasks_completed()
    assert pipe.wait_seconds == 600


def test_collection_limit_never_reverts_job_to_submission(tmp_path):
    job = Job('existing.txt', state=JobState.WAITING_VIDEO, notebook_id='existing',
        notebook_url='https://notebook.google.com/notebook/existing')
    remote = Remote([])
    def limited(job):
        raise CloudLimitReached(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    remote.inspect_status = limited
    repo = MemoryJobs(job)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.run_cycle()
    assert repo.get(job.id).state is JobState.WAITING_VIDEO
    assert repo.get(job.id).next_poll_at
    assert not pipe.all_tasks_completed()


def test_ten_minute_wait_is_immediately_pause_stop_interruptible():
    token = CancellationToken()
    entered, stopped = threading.Event(), threading.Event()
    def work():
        entered.set()
        try:
            token.wait(600)
        except RunCancelled:
            stopped.set()
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(1)
        token.request_pause()
        assert token.paused.wait(1)
        assert not stopped.is_set()
        token.request()
        assert stopped.wait(1)
    finally:
        token.request()
        thread.join(2)
    assert not thread.is_alive()


def test_persisted_three_attempt_budget_does_not_stop_other_job(tmp_path):
    from test_pipeline import Ending
    raw = tmp_path/'bad.mp4'
    raw.write_bytes(b'raw')
    bad = Job('bad.txt', state=JobState.RAW_READY, raw_path=str(raw))
    good = Job('good.txt', state=JobState.WAITING_VIDEO, notebook_id='good',
               notebook_url='https://notebook.google.com/notebook/good')
    repo = MemoryJobs(bad, good)
    remote = Remote([], {good.id:'READY'})
    clock = [NOW]
    for attempt in range(1, 4):
        # Recreate coordinator to prove the retry budget survives restart.
        pipe = coordinator(tmp_path, repo, remote, Ending(fail_name='bad.mp4'))
        pipe.cloud_limit.clock = lambda: clock[0]
        pipe.run_cycle()
        failed = repo.get(bad.id)
        assert failed.attempt_by_stage['scheduler.recovery'] == attempt
        assert failed.state is JobState.FAILED
        assert repo.get(good.id).state is JobState.COMPLETED
        if attempt < 3:
            assert not pipe.all_tasks_completed()
            assert pipe.wait_seconds == 600
        clock[0] += timedelta(seconds=601)
    assert failed.failure_class == 'TERMINAL_FAILED'
    assert pipe.all_tasks_completed()


def test_remote_access_failure_defers_only_affected_job(tmp_path):
    a = Job('a.txt', state=JobState.WAITING_VIDEO, notebook_id='a', notebook_url='https://notebook.google.com/notebook/a')
    b = Job('b.txt', state=JobState.WAITING_VIDEO, notebook_id='b', notebook_url='https://notebook.google.com/notebook/b')
    remote = Remote([], {b.id:'READY'})
    inspect_status = remote.inspect_status
    def inspect_job(job):
        if job.id == a.id:
            remote.events.append(('unavailable', a.id))
            raise ConnectionError('temporary notebook access failure')
        return inspect_status(job)
    remote.inspect_status = inspect_job
    repo = MemoryJobs(a,b)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.run_cycle()
    assert repo.get(b.id).state is JobState.COMPLETED
    assert repo.get(a.id).state is JobState.WAITING_VIDEO
    assert repo.get(a.id).runtime_reason == 'WAITING_REMOTE_ACCESS'
    pipe.run_cycle()
    assert remote.events.count(('unavailable', a.id)) == 1


@pytest.mark.parametrize('rechecked', ['GENERATING', 'UNKNOWN'])
def test_unknown_recovery_checks_source_without_duplicate_generation(tmp_path, rechecked):
    from types import SimpleNamespace
    from djd_maker.adapters.notebook import NotebookEngineAdapter, NotebookAdapterError
    from djd_maker.core.models import preset_body_sha256
    source = tmp_path/'source.txt'
    source.write_text('source', encoding='utf-8')
    checks = []
    dom = SimpleNamespace(ensure_source=lambda path: checks.append(path))
    adapter = NotebookEngineAdapter(dom)
    adapter._open_job = lambda job: None
    statuses = iter(('UNKNOWN', rechecked))
    adapter.inspect_status = lambda job: next(statuses)
    job = Job(str(source), notebook_id='existing', notebook_url='https://notebook.google.com/notebook/existing',
        resume_checkpoint='SOURCE_CHECK', preset_body_snapshot='body', preset_body_sha256=preset_body_sha256('body'))
    if rechecked == 'UNKNOWN':
        with pytest.raises(NotebookAdapterError, match='REMOTE_DIAGNOSIS_UNCERTAIN'):
            adapter.submit(job)
    else:
        assert adapter.submit(job).notebook_id == 'existing'
    assert checks == [source]


def test_invalid_deadline_is_diagnosed_not_waited_forever(tmp_path):
    job = Job('invalid.txt', state=JobState.WAITING_VIDEO, notebook_id='old',
        notebook_url='https://notebook.google.com/notebook/old', next_poll_at='invalid')
    remote = Remote([], {job.id:'GENERATING'})
    repo = MemoryJobs(job)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.run_cycle()
    assert ('check', job.id) in remote.events
    assert not any(event[0] == 'submit' for event in remote.events)
    assert repo.get(job.id).state is JobState.WAITING_VIDEO


def test_failed_raw_validation_has_bounded_automatic_retry(tmp_path):
    from test_pipeline import RejectingRawStore
    job = Job('invalid-raw.txt', state=JobState.DOWNLOAD_VERIFY_FAILED,
        notebook_id='old', notebook_url='https://notebook.google.com/notebook/old')
    repo = MemoryJobs(job)
    pipe = coordinator(tmp_path, repo, Remote([]))
    pipe.raw_store = RejectingRawStore()
    clock = [NOW]
    pipe.cloud_limit.clock = lambda: clock[0]
    for attempt in range(1, 4):
        pipe.run_cycle()
        assert repo.get(job.id).attempt_by_stage['scheduler.recovery'] == attempt
        assert not repo.get(job.id).raw_path
        clock[0] += timedelta(seconds=601)
    assert repo.get(job.id).failure_class == 'TERMINAL_FAILED'


def test_explicit_collection_command_also_works_during_chat_limit(tmp_path):
    job = Job('collect.txt', state=JobState.WAITING_VIDEO, notebook_id='old',
        notebook_url='https://notebook.google.com/notebook/old')
    repo = MemoryJobs(job)
    remote = Remote([], {job.id:'READY'})
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    assert pipe.run_recovery_cycle(now=NOW) == [job.id]
    assert ('download', job.id) in remote.events
    assert repo.get(job.id).state is JobState.COMPLETED
    assert not any(event[0] == 'submit' for event in remote.events)


def test_browser_current_state_uses_live_process_not_history(tmp_path):
    from types import SimpleNamespace
    manager = BrowserManager(tmp_path)
    running = [True]
    manager._auth_process = SimpleNamespace(poll=lambda: None if running[0] else 0)
    manager._navigation_result = 'auth-chrome-closed'
    assert manager.auth_process_alive
    running[0] = False
    manager._navigation_result = 'auth-chrome-opened'
    assert not manager.auth_process_alive
    source = textwrap.dedent(inspect.getsource(BrowserManager.auth_process_alive.fget))
    attributes = {n.attr for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Attribute)}
    assert attributes == {'_auth_process', 'poll'}
    tree = ast.parse(textwrap.dedent(inspect.getsource(BrowserManager)))
    diagnostic_only = {'_navigation_result', '_authentication_result', '_preflight_result'}
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            assert not ({n.attr for n in ast.walk(node.test) if isinstance(n, ast.Attribute)} & diagnostic_only)


def test_dual_gui_common_presets_and_no_resume_button(tmp_path):
    window, _, _, _ = _window(tmp_path, [])
    repository = PresetRepository(tmp_path/'presets.json')
    a = repository.create('A', '本文A')
    repository.select(a.id)
    window.preset_repository = repository
    try:
        for gui in ('PHASE2', 'PHASE1', 'PHASE2'):
            window.switch_gui(gui)
            assert window.preset_repository is repository
            assert window.preset_combo.currentData() == a.id
            assert all(button.text() != '再開' for button in window.findChildren(QPushButton))
            assert not hasattr(window, 'resume_button')
        assert PresetRepository(tmp_path/'presets.json').selected() is None
    finally:
        window.close()
