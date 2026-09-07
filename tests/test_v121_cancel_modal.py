from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from djd_maker.core.cancellation import CancellationToken, RunCancelled, cancellation_scope, checkpoint, interruptible_sleep, run_process
from djd_maker.adapters.cancellable_browser import wrap
from djd_maker.adapters.notebook_modal import BlockingModalError, ModalKind, classify_modal, ensure_notebook_interactable
from test_v12_chat_dom import browser, page


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows ownership boundary')
def test_owned_job_fallback_leaves_unrelated_process_alive():
    from djd_maker.adapters.owned_process_job import OwnedProcessJob
    owned=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    boundary=None
    try:
        boundary=OwnedProcessJob(owned.pid)
        boundary.terminate()
        owned.wait(timeout=3)
        assert unrelated.poll() is None
        boundary.terminate()  # idempotent empty job
    finally:
        if boundary:boundary.close()
        for child in (owned,unrelated):
            if child.poll() is None:child.terminate()
            child.wait(timeout=3)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows ownership boundary')
def test_real_playwright_owner_fallback_unblocks_hung_page(tmp_path):
    from djd_maker.adapters.browser import BrowserManager
    manager=BrowserManager(tmp_path/'isolated-profile',headless=True)
    page=manager.start()
    assert manager.shutdown_diagnostic()['owned_driver_pid']
    timer=threading.Timer(.5,manager.abort_owned_automation)
    started=time.monotonic();timer.start()
    try:
        with pytest.raises(Exception):
            page.evaluate('()=>new Promise(()=>{})')
    finally:
        timer.join()
        manager.stop()
    assert time.monotonic()-started<8
    assert manager._owned_process_job is None


def test_controller_stop_fallback_exits_real_browser_worker(tmp_path):
    from test_gui_pipeline_controller import controller
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.core.models import Job
    instance, jobs, pipeline, scheduler=controller(tmp_path,Job('lesson.txt'))
    manager=BrowserManager(tmp_path/'profile',headless=True)
    ready=threading.Event();errors=[];logs=[]
    def hang():
        page=manager.start()
        ready.set()
        page.evaluate('()=>new Promise(()=>{})')
    pipeline.run_cycle=hang
    instance.cleanup=manager.stop
    instance.abort_owned_external=manager.abort_owned_automation
    instance.shutdown_diagnostic=manager.shutdown_diagnostic
    instance.bind(jobs=lambda _:None,status=lambda _:None,log=logs.append,error=lambda *e:errors.append(e))
    instance.start()
    try:
        assert ready.wait(10)
        worker=instance._worker
        started=time.monotonic()
        result=instance.stop()
        assert time.monotonic()-started<10
        assert not result['running'] and not worker.is_alive()
        assert not errors
        assert any(item['stage']=='shutdown-fallback' for item in logs)
        assert jobs.list()[0].state.value=='WAITING'
    finally:
        instance.shutdown()


def test_production_optional_dialog_does_not_use_global_close(page):
    from djd_maker.adapters.notebook import NotebookDomAdapter
    install_modal(page,'Notebookを削除しますか')
    adapter=NotebookDomAdapter(page,interactable_guard=ensure_notebook_interactable)
    with pytest.raises(BlockingModalError):
        adapter._dismiss_optional_dialogs()
    assert page.evaluate('window.modalClicks')==0


@pytest.mark.parametrize('engine',['ending','hls'])
def test_real_ffmpeg_adapter_wait_cancels_without_child_leak(engine,monkeypatch):
    from djd_maker.media.validator import resolve_executable
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import _run
    ffmpeg=resolve_executable('ffmpeg',None);token=CancellationToken();children=[]
    original=subprocess.Popen
    def tracked(*args,**kwargs):
        child=original(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(subprocess,'Popen',tracked)
    command=[str(ffmpeg),'-hide_banner','-nostdin','-re','-f','lavfi','-i','sine=frequency=440','-f','null','-']
    timer=threading.Timer(.5,token.request);started=time.monotonic();timer.start()
    try:
        with cancellation_scope(token),pytest.raises(RunCancelled):
            if engine=='ending':EndingEngineAdapter()._run(command,'cancellation smoke')
            else:_run(command,600)
    finally:timer.join()
    assert time.monotonic()-started<5
    assert children and all(child.poll() is not None for child in children)
    assert not token.children


def test_interruptible_sleep_and_idempotent_stop():
    token=CancellationToken()
    timer=threading.Timer(.1,token.request)
    started=time.monotonic();timer.start()
    try:
        with cancellation_scope(token), pytest.raises(RunCancelled):
            interruptible_sleep(600)
    finally:
        timer.join()
    assert time.monotonic()-started < 2
    before=token.requested_at;token.request();assert token.requested_at==before


def test_owned_subprocess_stops_without_orphan(tmp_path, monkeypatch):
    children=[]
    original_popen=subprocess.Popen
    def tracked_popen(*args,**kwargs):
        child=original_popen(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(subprocess,'Popen',tracked_popen)
    token=CancellationToken();pid_file=tmp_path/'child.pid'
    timer=threading.Timer(.5,token.request);timer.start()
    command=[sys.executable,'-c',f"import os,time;from pathlib import Path;Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(600)"]
    started=time.monotonic()
    try:
        with cancellation_scope(token),pytest.raises(RunCancelled):
            run_process(command,capture_output=True,text=True,timeout=600)
    finally:
        timer.join()
    assert time.monotonic()-started<4
    assert len(children)==1
    assert children[0].poll() is not None


def test_browser_poll_interrupts_and_no_goto_after_stop(page):
    token=CancellationToken();guarded=wrap(page)
    timer=threading.Timer(.1,token.request);timer.start()
    try:
        with cancellation_scope(token):
            with pytest.raises(RunCancelled):
                guarded.wait_for_timeout(600_000)
            with pytest.raises(RunCancelled):
                guarded.goto('https://example.invalid')
            with pytest.raises(RunCancelled):
                guarded.context.new_page()
    finally:
        timer.join()
    assert token.navigation_count==0


def test_browser_positional_timeout_is_not_passed_twice(page):
    with cancellation_scope(CancellationToken()):
        wrap(page).context.set_default_timeout(2000)
        assert wrap(page).locator('chat-panel').count()==1


def test_browser_observation_wait_keeps_original_deadline(page):
    page.evaluate("setTimeout(() => { const b=document.createElement('button'); b.id='late'; document.body.append(b); }, 2300)")
    with cancellation_scope(CancellationToken()):
        wrap(page).locator('#late').wait_for(state='attached', timeout=5000)
    assert page.locator('#late').count()==1


def test_browser_observation_wait_can_stop_within_short_slice(page):
    token=CancellationToken(); timer=threading.Timer(.1,token.request); timer.start()
    started=time.monotonic()
    try:
        with cancellation_scope(token),pytest.raises(RunCancelled):
            wrap(page).locator('#never').wait_for(timeout=60000)
    finally:
        timer.join()
    assert time.monotonic()-started<4


def test_reset_does_not_forget_an_active_child():
    token=CancellationToken();token.request()
    token.children[42]=SimpleNamespace(poll=lambda:None)
    with pytest.raises(RuntimeError,match='owned child'):
        token.reset()
    assert token.event.is_set() and 42 in token.children


def test_stop_timeout_does_not_report_gui_stopped(tmp_path):
    from test_gui import _window
    window, _settings, _controller, _bridge = _window(tmp_path, [])
    window._running=True
    window._operation_finished('stop', {'running':True})
    assert window._running
    assert not window.start_button.isEnabled()
    assert '停止完了' not in window.statusBar().currentMessage()
    window.close()


def test_recovery_unknown_modal_is_terminal_not_next_job(tmp_path):
    from datetime import UTC, datetime
    from test_pipeline import MemoryJobs, coordinator
    from djd_maker.core.models import Job, JobState
    class Notebook:
        def inspect_status(self, job):
            raise BlockingModalError('BLOCKING_MODAL_UNKNOWN')
    job=Job('lesson.txt',state=JobState.WAITING_VIDEO,notebook_id='existing',notebook_url='https://notebook.google.com/notebook/existing')
    jobs=MemoryJobs(job);pipeline=coordinator(tmp_path,jobs,Notebook())
    with pytest.raises(BlockingModalError):
        pipeline._recover_remote_job(job,checked_at=datetime.now(UTC))
    assert jobs.get(job.id).state is JobState.WAITING_VIDEO
    assert jobs.get(job.id).recovery_retry_count==0


@pytest.mark.parametrize('text,kind',[
    ('Gemini Notebook の使用方法をより柔軟に管理できるようになりました。',ModalKind.ANNOUNCEMENT),
    ('Limits refresh every 5 hours New background queue',ModalKind.ANNOUNCEMENT),
    ('NotebookLMへようこそ',ModalKind.ONBOARDING),
    ('NotebookLMの新機能',ModalKind.SAFE_INFO),
    ('Notebookを削除しますか',ModalKind.ACTION_CONFIRMATION),
    ('謎の確認内容',ModalKind.UNKNOWN),
])
def test_modal_classifier(text,kind):
    assert classify_modal(text)==kind


def install_modal(page,text,remove=True):
    page.evaluate('''({text,remove})=>{
      window.outsideClicks=0;window.modalClicks=0;
      const outside=document.createElement('button');outside.textContent='閉じる';outside.id='outside';outside.onclick=()=>window.outsideClicks++;document.body.append(outside);
      const scrim=document.createElement('div');scrim.dataset.testid='modal-scrim';scrim.id='scrim';scrim.textContent='scrim';document.body.append(scrim);
      const dialog=document.createElement('div');dialog.setAttribute('role','dialog');dialog.id='modal';
      const title=document.createElement('h2');title.textContent=text;dialog.append(title);
      const close=document.createElement('button');close.textContent='閉じる';close.onclick=()=>{window.modalClicks++;if(remove){dialog.remove();scrim.remove()}};dialog.append(close);document.body.append(dialog);
    }''',{'text':text,'remove':remove})


def test_announcement_dismiss_scoped_scrim_gone_and_reappears(page):
    for _ in range(2):
        install_modal(page,'Limits refresh every 5 hours New background queue')
        ensure_notebook_interactable(page,target=page.locator('chat-panel textarea'))
        assert page.locator('#modal').count()==0
        assert page.locator('#scrim').count()==0
        assert page.evaluate('window.outsideClicks')==0
        assert page.evaluate('window.modalClicks')==1
    ensure_notebook_interactable(page)


@pytest.mark.parametrize('text',['Notebookを削除しますか','未知のdialog'])
def test_confirmation_and_unknown_are_never_dismissed(page,text):
    install_modal(page,text)
    with pytest.raises(BlockingModalError):
        ensure_notebook_interactable(page)
    assert page.evaluate('window.modalClicks')==0
    assert page.evaluate('window.outsideClicks')==0


def test_stop_during_modal_wait_no_further_dismiss(page):
    install_modal(page,'NotebookLMの新機能',remove=False)
    token=CancellationToken();timer=threading.Timer(.2,token.request);timer.start()
    try:
        with cancellation_scope(token),pytest.raises(RunCancelled):
            ensure_notebook_interactable(wrap(page),timeout_seconds=60)
    finally:
        timer.join()
    before=page.evaluate('window.modalClicks')
    with cancellation_scope(token),pytest.raises(RunCancelled):
        ensure_notebook_interactable(wrap(page))
    assert page.evaluate('window.modalClicks')==before


@pytest.mark.parametrize('surface',['source','studio'])
def test_modal_fixture_restores_source_and_studio_controls(page,surface):
    page.evaluate('''surface=>{
        const button=document.createElement('button');button.id=surface;
        button.textContent=surface==='source'?'ソースを追加':'動画解説';
        window.surfaceClicks=0;button.onclick=()=>window.surfaceClicks++;
        document.body.append(button);
    }''',surface)
    install_modal(page,'Gemini Notebook の使用方法をより柔軟に管理できるようになりました。')
    control=page.locator('#'+surface)
    ensure_notebook_interactable(page,target=control)
    assert page.locator('#modal').count()==0 and page.locator('#scrim').count()==0
    assert page.evaluate('window.surfaceClicks')==0  # guard's trial has no action
    control.click()
    assert page.evaluate('window.surfaceClicks')==1


@pytest.mark.parametrize('operation',['source.wait','chat.send_enabled','chat.reply','artifact.poll','retry.sleep','quota.wait','download.wait'])
def test_stop_during_wait_prevents_following_operation(operation):
    token=CancellationToken();calls=[]
    def worker():
        try:
            with cancellation_scope(token):
                checkpoint(operation)
                interruptible_sleep(600)
                calls.append('forbidden_next_operation')
        except RunCancelled:
            pass
    thread=threading.Thread(target=worker);thread.start();token.request();thread.join(2)
    assert not thread.is_alive() and calls==[]


def test_stop_persists_checkpoint_without_failure_and_restart_reuses_identity(tmp_path):
    from test_pipeline import MemoryJobs, coordinator
    from djd_maker.core.models import Job,JobState
    token=CancellationToken()
    class Notebook:
        def submit(self,job):
            job.notebook_id='existing';job.notebook_url='https://notebook.google.com/notebook/existing'
            token.request();checkpoint('chat.send')
        def diagnose_resume(self,job):
            assert job.notebook_id=='existing'
            return {'artifact':'NOT_STARTED','source':'READY','preset':'NOT_SENT','reply':'NO_RESPONSE'}
    job=Job('lesson.txt');jobs=MemoryJobs(job);pipeline=coordinator(tmp_path,jobs,Notebook())
    with cancellation_scope(token),pytest.raises(RunCancelled):
        pipeline.run_cycle()
    interrupted=jobs.get(job.id)
    assert interrupted.state is JobState.RECOVERY_PENDING
    assert interrupted.resume_checkpoint=='STOPPED:UPLOADING'
    assert interrupted.error_code is None
    snapshot=interrupted.preset_body_snapshot
    token.reset()
    with cancellation_scope(token):
        assert pipeline.resume_failed_jobs()==[job.id]
    assert jobs.get(job.id).state is JobState.WAITING
    assert jobs.get(job.id).preset_body_snapshot==snapshot
    assert len(jobs.list())==1
