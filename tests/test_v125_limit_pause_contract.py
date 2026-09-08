from datetime import datetime, timedelta, timezone
from dataclasses import replace
import threading
import time

import pytest

from djd_maker.core.cloud_limit import CloudLimitGate, CloudLimitReached, parse_limit_text
from djd_maker.core.cancellation import CancellationToken, cancellation_scope, checkpoint, RunCancelled
from djd_maker.core.commands import CommandId, CommandRouter, REQUIRED_COMMANDS, BUTTON_COMMANDS, InterfaceError, EventId, PresentationEvent
from djd_maker.core.models import Job, JobState
from test_pipeline import coordinator, MemoryJobs
from test_v122_sequential_runtime import Remote
from test_gui import _window, _drain_until
from test_v12_chat_dom import browser, page, flow
from djd_maker.adapters.usage_limit import UsageLimitDetector
from djd_maker.core.cancellation import active_monotonic

NOW = datetime(2026, 9, 8, 5, 0, tzinfo=timezone(timedelta(hours=9)))


@pytest.mark.parametrize('text,hour,minute', [
    ('AI の使用量上限に達しました。6:21以降にすべての機能が利用可能になります。', 6, 21),
    ('チャットは 06:21 まで無効になっています', 6, 21),
    ('AIの使用量上限に達しました。午後6:21以降にすべての機能が利用可能になります。', 18, 21),
    ('チャットは 午前12:05 まで無効になっています', 0, 5),
])
def test_limit_clock_and_margin(tmp_path, text, hour, minute):
    gate = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW)
    observation = parse_limit_text(text, now=NOW, chat_disabled=True)
    assert observation.blocked_until.hour == hour
    assert observation.blocked_until.minute == minute
    gate.block(observation)
    assert datetime.fromisoformat(gate.state['cloud_resume_at']) == observation.blocked_until+timedelta(minutes=5)
    restored = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW)
    assert restored.blocked and not restored.due


@pytest.mark.parametrize('hour,day', [(4, 9), (5, 8), (6, 8)])
def test_limit_cross_midnight(hour, day):
    observation = parse_limit_text(f'チャットは {hour}:00 まで無効になっています', now=NOW)
    assert observation.blocked_until.day == day


@pytest.mark.parametrize('clock_text', ['25:00', '6:99', '午後13:00', '時刻不明'])
def test_unknown_limit_time_never_resumes(tmp_path, clock_text):
    gate = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW+timedelta(days=20))
    gate.block(parse_limit_text('AI の使用量上限に達しました。'+clock_text, now=NOW))
    assert gate.blocked and not gate.due


def test_limit_interrupts_dispatch_and_drains_raw(tmp_path):
    raw = tmp_path/'raw.mp4'; raw.write_bytes(b'RAW')
    waiting = [Job(f'{i}.txt') for i in range(3)]
    local = Job('local.txt', state=JobState.RAW_READY, raw_path=str(raw))
    repo = MemoryJobs(*waiting, local)
    remote = Remote([])
    calls = []
    def submit(job):
        calls.append(job.id)
        raise CloudLimitReached(parse_limit_text('チャットは 6:21 まで無効になっています', now=NOW))
    remote.submit = submit
    pipeline = coordinator(tmp_path, repo, remote)
    pipeline.cloud_limit.clock = lambda: NOW
    pipeline.paths = replace(pipeline.paths, ending_video=None)
    pipeline.run_cycle()
    assert calls == [waiting[0].id]
    assert repo.get(local.id).state is JobState.COMPLETED
    assert all(repo.get(j.id).state is JobState.WAITING for j in waiting)
    assert pipeline.cloud_limit.blocked
    for _ in range(3):
        pipeline.run_cycle()
    assert calls == [waiting[0].id]
    assert pipeline.phase == 'LIMIT_WAIT'
    assert not remote.events


def test_resume_rechecks_and_extends_before_any_send(tmp_path):
    clock = [NOW]
    repo = MemoryJobs(Job('a.txt'))
    remote = Remote([])
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    checks = []
    def recheck(url):
        checks.append(url)
        raise CloudLimitReached(parse_limit_text('チャットは 7:21 まで無効', now=clock[0]))
    remote.recheck_cloud_limit = recheck
    clock[0] = NOW.replace(hour=6, minute=25)
    pipe.run_cycle()
    assert not checks and not remote.events
    clock[0] += timedelta(minutes=1)
    pipe.run_cycle()
    assert len(checks) == 1 and not remote.events
    assert datetime.fromisoformat(pipe.cloud_limit.state['cloud_resume_at']).hour == 7


@pytest.mark.parametrize('operation', ['page.goto', 'notebook.submit', 'chat.send', 'source.upload', 'download.start', 'media.start', 'subprocess.start', 'queue.dequeue'])
def test_pause_blocks_next_side_effect_and_resume_preserves_checkpoint(operation):
    token = CancellationToken(); token.request_pause()
    actions = []
    def work():
        with cancellation_scope(token):
            checkpoint(operation)
            actions.append(operation)
    thread = threading.Thread(target=work); thread.start()
    try:
        assert token.paused.wait(2)
        assert actions == []
        token.resume(); thread.join(2)
        assert actions == [operation]
    finally:
        token.request(); thread.join(2)


def test_pause_100_jobs_no_next_navigation():
    token = CancellationToken(); reached = threading.Event(); actions = []
    def work():
        try:
            with cancellation_scope(token):
                for index in range(100):
                    token.navigation()
                    actions.append(index)
                    if index == 0:
                        token.request_pause(); reached.set()
                    checkpoint('job.next')
        except RunCancelled:
            pass
    thread = threading.Thread(target=work); thread.start()
    try:
        assert reached.wait(2) and token.paused.wait(2)
        assert actions == [0] and token.navigation_count == 1
        token.resume(); thread.join(2)
        assert actions == list(range(100))
    finally:
        token.request(); thread.join(2)


def test_stop_wakes_paused_worker():
    token = CancellationToken(); token.request_pause(); cancelled = []
    def work():
        try: token.check('page.goto')
        except RunCancelled: cancelled.append(True)
    thread = threading.Thread(target=work); thread.start()
    assert token.paused.wait(2)
    token.request(); thread.join(2)
    assert cancelled == [True]


@pytest.mark.parametrize('gui', ['PHASE1', 'PHASE2'])
def test_gui_contract_and_common_event(tmp_path, gui):
    job = Job('a.txt')
    window, settings, controller, bridge = _window(tmp_path, [job], ending=False)
    try:
        assert window.switch_gui(gui)
        assert window.bound_commands == REQUIRED_COMMANDS
        for attribute, command in BUTTON_COMMANDS.items():
            assert getattr(window, attribute).property('command_id') == command.value
        changed = replace(job, state=JobState.COMPLETED, presentation_revision=2)
        window.consume_event(PresentationEvent(EventId.JOB_UPDATED, changed))
        assert window.jobs[0].state is JobState.COMPLETED
        window.consume_event(PresentationEvent(EventId.RUNTIME_STATUS, {'cloud_limit': {'active':True,'cloud_resume_at':'06:26','remaining_seconds':10}}))
        assert '06:26' in window.limit_label.text()
        assert window.dispatch_command('CMD_DOES_NOT_EXIST') is False
        assert window._dispatch_disabled
        assert not controller.calls
    finally:
        window.close()


def test_gui_switch_preserves_jobs_and_checks(tmp_path):
    job = Job('a.txt')
    window, settings, _, _ = _window(tmp_path, [job], ending=False)
    try:
        window._checked_job_ids.add(job.id)
        before = job.to_dict()
        for mode in ['PHASE2', 'PHASE1']:
            assert window.switch_gui(mode)
            assert settings.load().gui_type == mode
            assert window.jobs[0].to_dict() == before
            assert job.id in window._checked_job_ids
    finally:
        window.close()


def test_router_duplicate_unknown_and_payload():
    calls=[]
    handlers=[(c, lambda p: calls.append(p)) for c in REQUIRED_COMMANDS]
    with pytest.raises(InterfaceError, match='duplicate'):
        CommandRouter(handlers+handlers[:1])
    router=CommandRouter(handlers)
    with pytest.raises(InterfaceError, match='UNKNOWN_COMMAND_ID'):
        router.dispatch('CMD_DOES_NOT_EXIST')
    with pytest.raises(InterfaceError, match='job_ids'):
        router.dispatch(CommandId.DELETE_SELECTED, {'job_ids':'all'})
    assert not calls
    router.dispatch(CommandId.STOP)
    assert calls == [{}]


def test_pause_observation_clock_freezes_but_limit_deadline_does_not(tmp_path):
    token = CancellationToken()
    gate = CloudLimitGate(tmp_path/'limit.json', clock=lambda: NOW)
    gate.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    deadline = gate.state['cloud_resume_at']
    with cancellation_scope(token):
        before = active_monotonic()
        token.request_pause()
        time.sleep(.06)
        paused = active_monotonic()
        token.resume()
        after = active_monotonic()
    assert abs(after-paused) < .03 and abs(paused-before) < .03
    assert gate.state['cloud_resume_at'] == deadline


@pytest.mark.parametrize('text', ['AI の使用量上限に達しました。6:21以降にすべての機能が利用可能になります。', 'チャットは 6:21 まで無効になっています'])
def test_real_dom_limit_blocks_chat_without_input(page, text):
    page.locator('chat-panel').evaluate('(e,t)=>e.innerHTML=`<div role="status">${t}</div><query-box><button aria-label="送信" disabled>送信</button></query-box>`', text)
    observation, enabled = UsageLimitDetector(page, lambda: NOW).observe()
    assert observation.chat_disabled and observation.blocked_until.hour == 6
    assert not enabled
    with pytest.raises(CloudLimitReached):
        flow(page).send('must not send')
    assert page.evaluate('window.sends') == 0


@pytest.mark.parametrize('container', ['source-panel', 'div data-message-author-role="user"', 'div data-message-author-role="assistant"'])
def test_limit_text_in_content_is_not_a_limit(page, container):
    tag=container.split()[0]
    page.evaluate('(html)=>document.body.insertAdjacentHTML("beforeend",html)', f'<{container}>AI の使用量上限に達しました。6:21以降にすべての機能が利用可能になります。</{tag}>')
    assert UsageLimitDetector(page, lambda: NOW).observe()[0] is None


def test_limit_downloaded_validation_and_local_only_cleanup(tmp_path):
    job = Job('downloaded.txt', state=JobState.DOWNLOADING, notebook_id='kept', artifact_status='READY')
    remote=Remote([])
    pipe=coordinator(tmp_path, MemoryJobs(job), remote)
    download=pipe.paths.work_directory/job.id/'download'/f'{job.script_name}.mp4'
    download.parent.mkdir(parents=True)
    download.write_bytes(b'validated-by-test-raw-store')
    pipe.cloud_limit.clock=lambda: NOW
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効',now=NOW))
    pipe.paths=replace(pipe.paths,ending_video=None)
    pipe.run_cycle()
    assert pipe.jobs.get(job.id).state is JobState.COMPLETED
    assert pipe.jobs.get(job.id).artifact_status == 'DELETE_PENDING'
    assert remote.events == []


@pytest.mark.parametrize('gui',['PHASE1','PHASE2'])
def test_every_gui_button_emits_same_command(tmp_path, gui):
    window, _, _, _ = _window(tmp_path,[Job('a.txt')],ending=False)
    try:
        window.switch_gui(gui)
        calls=[]
        window.dispatch_command=lambda command,payload=None: calls.append(command)
        for attribute,command in BUTTON_COMMANDS.items():
            button=getattr(window,attribute)
            button.setEnabled(True)
            button.click()
            assert calls[-1] == command
        window.consume_event(PresentationEvent(EventId.LIMIT_DETECTED, {'active':True,'cloud_resume_at':'06:26','remaining_seconds':9}))
        assert 'AI LIMIT' in window.limit_label.text()
        window.consume_event(PresentationEvent(EventId.LIMIT_RELEASED, {}))
        assert '未検出' in window.limit_label.text()
    finally:
        window.close()


@pytest.mark.parametrize('enabled', [False, True])
def test_due_recheck_requires_enabled_chat(tmp_path, enabled):
    clock=[NOW]
    job=Job('waiting.txt')
    remote=Remote([])
    pipe=coordinator(tmp_path,MemoryJobs(job),remote)
    pipe.cloud_limit.clock=lambda:clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効',now=NOW))
    checked=[]
    remote.recheck_cloud_limit=lambda url: checked.append(url) or enabled
    # No remote operation, even if the UI would report enabled early.
    pipe.run_cycle()
    assert checked==[] and remote.events==[]
    clock[0]=NOW.replace(hour=6,minute=26)
    pipe.run_cycle()
    assert len(checked)==1
    assert pipe.cloud_limit.blocked is not enabled
    if not enabled:
        pipe.run_cycle()
        assert len(checked)==1 and remote.events==[]


def test_recheck_network_failure_does_not_spin_or_dispatch(tmp_path):
    remote=Remote([])
    pipe=coordinator(tmp_path,MemoryJobs(Job('waiting.txt')),remote)
    pipe.cloud_limit.clock=lambda:NOW.replace(hour=6,minute=26)
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効',now=NOW))
    calls=[]
    def fail(url):
        calls.append(url)
        raise ConnectionError('temporary')
    remote.recheck_cloud_limit=fail
    for _ in range(4): pipe.run_cycle()
    assert len(calls)==1 and remote.events==[] and pipe.cloud_limit.blocked


def test_legacy_reserved_preserved_during_limit(tmp_path):
    job=Job('legacy.txt',state=JobState.RESERVED_WAITING_CREDIT_RESET,notebook_id='old',notebook_url='https://notebook.google.com/notebook/old')
    # V126 permits due artifact patrol during a Chat limit, never early patrol.
    job.next_poll_at=(NOW+timedelta(hours=2)).isoformat()
    remote=Remote([])
    pipe=coordinator(tmp_path,MemoryJobs(job),remote)
    pipe.cloud_limit.clock=lambda:NOW
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効',now=NOW))
    before=pipe.jobs.get(job.id).to_dict()
    pipe.run_cycle()
    assert pipe.jobs.get(job.id).to_dict()==before and remote.events==[]


def test_source_upload_checks_live_limit_before_action(page, tmp_path):
    from djd_maker.adapters.notebook import NotebookDomAdapter
    source=tmp_path/'lesson.txt';source.write_text('fixture',encoding='utf-8')
    page.evaluate('document.body.insertAdjacentHTML("beforeend", "<div role=alert>チャットは 6:21 まで無効になっています</div>")')
    dom=NotebookDomAdapter(page,clock=lambda:NOW)
    with pytest.raises(CloudLimitReached):
        dom.upload_txt(source)
    assert page.evaluate('window.sends')==0


@pytest.mark.parametrize('unknown', [None, [], {}, 123, 'CMD_DOES_NOT_EXIST'])
def test_unknown_command_types_fail_closed(unknown):
    calls=[]
    router=CommandRouter((c,lambda p:calls.append(p)) for c in REQUIRED_COMMANDS)
    with pytest.raises(InterfaceError,match='UNKNOWN_COMMAND_ID'):
        router.dispatch(unknown)
    assert not calls


def test_recheck_waits_for_hydrated_enabled_chat(page):
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter
    page.set_content('<chat-panel id="chat"></chat-panel><script>setTimeout(()=>document.querySelector("#chat").innerHTML="<textarea placeholder=question></textarea>",250)</script>')
    engine=NotebookEngineAdapter(NotebookDomAdapter(page))
    assert engine.recheck_cloud_limit()
