"""Current Studio credit limit; clocks and real Chromium without Google writes."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from test_v25_studio_direct import studio, browser, HTML
from djd_maker.adapters.studio_generation import (
    generation_button_state, GenerateButtonState as S, owned_video_dialog,
    close_video_settings)
from djd_maker.adapters.notebook import NotebookEngineAdapter, RemoteVideoStatus
from djd_maker.adapters.notebook_modal import BlockingModalError
from djd_maker.core.cloud_limit import CloudLimitGate, CloudLimitReached, LimitObservation, CREDIT_LIMIT_MESSAGE
from djd_maker.core.download_guard import DownloadLifecycleGuard, DeferredTaskType
from djd_maker.core.models import Job, JobState
from djd_maker.testing.studio_fixture import install_studio_fixture
from djd_maker.gui.presentation_models import CreditLimitViewModel
from test_pipeline import coordinator, MemoryJobs
from test_v122_sequential_runtime import Remote
from test_v125_limit_pause_contract import NOW


def deferred(s):
    install_studio_fixture(s.page, HTML, opened=True, deferred=True)


def test_deferred_generate_button_means_credit_limit_without_click_or_retry(studio):
    s=studio;deferred(s)
    assert generation_button_state(s.flow.panel()) is S.GENERATE_DEFERRED_CREDIT_LIMIT
    assert owned_video_dialog(s.flow.panel())
    before=s.job.to_dict()
    with pytest.raises(CloudLimitReached) as caught:s.flow.run()
    assert caught.value.observation.message==CREDIT_LIMIT_MESSAGE
    assert caught.value.observation.blocked_until is None
    assert s.page.get_by_role('dialog').count()==0
    assert s.page.evaluate('window.badClicks+window.generateClicks+window.chatSends')==0
    assert s.job.studio_generation_attempt==0 and not s.job.studio_generate_claimed_at
    assert s.job.attempt_by_stage==before['attempt_by_stage']


@pytest.mark.parametrize('location',['chat-panel','studio-panel','mat-dialog-content'])
def test_unrelated_deferred_text_ignored(studio,location):
    s=studio;s.page.evaluate('openStudioFixture()')
    s.page.locator(location).evaluate("e=>e.insertAdjacentHTML('beforeend','<button>後で生成</button>')")
    assert generation_button_state(s.flow.panel()) is S.GENERATE_AVAILABLE


@pytest.mark.parametrize('disabled',[False,True])
def test_actual_generate_button_state(studio,disabled):
    s=studio;s.page.evaluate('openStudioFixture()')
    panel=s.flow.panel();panel.get_by_role('button',name='後で生成',exact=True).evaluate('e=>e.remove()')
    panel.get_by_role('button',name='生成',exact=True).evaluate('(e,v)=>e.disabled=v',disabled)
    assert generation_button_state(panel) is (S.GENERATE_DISABLED if disabled else S.GENERATE_AVAILABLE)


@pytest.mark.parametrize('text,expected',[
    ('ログインが必要',S.AUTH_REQUIRED),('削除しますか',S.BLOCKING_MODAL_UNKNOWN),
    ('同意してください',S.BLOCKING_MODAL_UNKNOWN),('セキュリティ警告',S.BLOCKING_MODAL_UNKNOWN)])
def test_sensitive_modal_not_owned_or_auto_closed(studio,text,expected):
    s=studio;deferred(s);panel=s.page.get_by_role('dialog')
    panel.evaluate('(e,t)=>e.insertAdjacentHTML("beforeend","<p>"+t+"</p>")',text)
    assert generation_button_state(panel) is expected
    assert not owned_video_dialog(panel)
    assert s.page.evaluate('window.badClicks')==0


def test_deferred_button_scoped_to_video_settings(studio):
    s=studio;deferred(s);panel=s.page.get_by_role('dialog')
    panel.get_by_role('heading').evaluate("e=>e.textContent='別の設定'")
    assert generation_button_state(panel) is S.BLOCKING_MODAL_UNKNOWN
    assert not owned_video_dialog(panel)


def test_credit_modal_close_waits_for_scrim_and_hidden(studio):
    s=studio;deferred(s);panel=s.flow.panel()
    s.page.evaluate('''()=>{let backdrop=document.createElement('div');
        backdrop.className='cdk-overlay-backdrop cdk-overlay-backdrop-showing';backdrop.style='position:fixed;inset:0;pointer-events:none';document.body.append(backdrop);
        let close=document.querySelector('button:has(mat-icon)');close.onclick=()=>{
        setTimeout(()=>document.querySelector('[role=dialog]').remove(),100);
        setTimeout(()=>backdrop.remove(),220)}}''')
    close_video_settings(s.dom,panel,timeout_seconds=1)
    assert not s.page.locator('.cdk-overlay-backdrop').count()
    assert not s.page.get_by_role('dialog').count()
    assert s.page.evaluate('window.generateClicks+window.badClicks')==0


@pytest.mark.parametrize('failure',['scrim','dialog','close'])
def test_credit_modal_close_failure_is_bounded_review(studio,failure):
    s=studio;deferred(s);panel=s.flow.panel()
    if failure=='close':panel.locator('button:has(mat-icon:text-is("close"))').evaluate('e=>e.remove()')
    elif failure=='dialog':panel.locator('button:has(mat-icon:text-is("close"))').evaluate('e=>e.onclick=()=>{}')
    else:s.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div data-testid=modal-scrim>blocked</div>')")
    with pytest.raises(BlockingModalError,match='MODAL_CLOSE_NEEDS_REVIEW'):
        close_video_settings(s.dom,panel,timeout_seconds=.3)
    assert s.page.evaluate('window.badClicks+window.generateClicks')==0


@pytest.mark.parametrize('seconds,due',[(0,False),(599.999,False),(600,True),(601,True)])
def test_600_second_boundary_persisted_and_restart(tmp_path,seconds,due):
    clock=[NOW];path=tmp_path/'cloud-limit.json';gate=CloudLimitGate(path,clock=lambda:clock[0])
    gate.block(LimitObservation(CREDIT_LIMIT_MESSAGE,None),notebook_url='https://notebook.google.com/notebook/test')
    assert gate.state['cloud_resume_at'] is None
    assert gate.state['next_credit_probe_at']==(NOW+timedelta(seconds=600)).isoformat()
    clock[0]+=timedelta(seconds=seconds)
    restarted=CloudLimitGate(path,clock=lambda:clock[0])
    assert restarted.due is due
    assert restarted.recheck_delay==pytest.approx(max(0,600-seconds))


def pipeline(tmp_path):
    job=Job('lesson.txt',notebook_id='id',notebook_url='https://notebook.google.com/notebook/id',source_status='READY')
    pipe=coordinator(tmp_path,MemoryJobs(job),Remote([]));clock=[NOW]
    pipe.cloud_limit.clock=lambda:clock[0]
    pipe.cloud_limit.block(LimitObservation(CREDIT_LIMIT_MESSAGE,None),notebook_url=job.notebook_url)
    return pipe,clock,job


@pytest.mark.parametrize('result',[True,False,'deferred','timeout'])
def test_probe_scheduler_recovery_and_reschedule(tmp_path,result):
    pipe,clock,job=pipeline(tmp_path);calls=[];events=[];pipe.runtime_callback=events.append
    def probe(target):
        calls.append(target.id)
        if result=='deferred':raise CloudLimitReached(LimitObservation(CREDIT_LIMIT_MESSAGE,None))
        if result=='timeout':raise TimeoutError('temporary navigation timeout')
        return result
    pipe.notebook.probe_generation_credit=probe
    pipe._refresh_capabilities();assert calls==[]
    clock[0]+=timedelta(seconds=600);pipe._refresh_capabilities()
    assert calls==[job.id]
    assert pipe.capabilities.can_generate is (result is True)
    assert pipe.capabilities.can_download is (result!='timeout')
    assert pipe.capabilities.can_check_artifact is (result!='timeout')
    assert pipe.capabilities.can_local_process
    assert pipe.jobs.get(job.id).attempt_by_stage=={}
    assert not pipe.jobs.get(job.id).studio_generate_claimed_at
    if result is True:
        assert not pipe.cloud_limit.blocked
        assert 'next_credit_probe_at' not in pipe.cloud_limit.state
        assert any(e['stage']=='CREDIT_RECOVERED' for e in events)
        assert pipe._generation_preempts()
    else:
        # Ver2.12: only confirmed deferred credit uses the 600-second probe.
        assert pipe.cloud_limit.recheck_delay==(600 if result=='deferred' else 15)
        assert not pipe.all_tasks_completed()
        pipe._refresh_capabilities();assert calls==[job.id]


def test_probe_download_guard_and_dedup(tmp_path):
    pipe,clock,job=pipeline(tmp_path);guard=DownloadLifecycleGuard(tmp_path/'deferred.json')
    owner=SimpleNamespace(lifecycle_guard=guard)
    class Owner:
        lifecycle_guard=guard
        def interact(self):pass
    pipe.notebook.dom=SimpleNamespace(interactable_guard=Owner().interact)
    calls=[];pipe.notebook.probe_generation_credit=lambda target:calls.append(target.id) or True
    clock[0]+=timedelta(seconds=600)
    with guard.transfer(job.id):
        for _ in range(4):pipe._refresh_capabilities()
        assert len(guard.pending)==1
        assert guard.pending[0].task_type==DeferredTaskType.CREDIT_PROBE
        assert calls==[]
    pipe._refresh_capabilities();assert calls==[job.id]


def test_restart_restores_probe_deadline_without_orphan_queue_entry(tmp_path):
    pipe,clock,job=pipeline(tmp_path);clock[0]+=timedelta(seconds=600)
    path=tmp_path/'deferred.json';guard=DownloadLifecycleGuard(path)
    guard.defer(DeferredTaskType.CREDIT_PROBE)
    assert len(guard.pending)==1
    restarted=DownloadLifecycleGuard(path)
    assert not restarted.pending
    assert pipe.cloud_limit.due


def test_probe_waits_for_local_checkpoint(tmp_path):
    pipe,clock,job=pipeline(tmp_path);calls=[]
    pipe.notebook.probe_generation_credit=lambda target:calls.append(target.id) or True
    clock[0]+=timedelta(seconds=600);pipe._credit_local_active=True
    pipe._refresh_capabilities();assert not calls
    pipe._credit_local_active=False;pipe._refresh_capabilities();assert calls==[job.id]


@pytest.mark.parametrize('status',['GENERATING','READY'])
def test_probe_never_regenerates_existing_artifact(studio,status):
    s=studio;s.dom.inspect_status=lambda:RemoteVideoStatus(status)
    engine=NotebookEngineAdapter(s.dom,persist_identity=s.repo.save);engine._open_job=lambda job:None
    assert engine.probe_generation_credit(s.job) is False
    assert s.page.evaluate('window.generateClicks+window.studioOpens+window.chatSends')==0


@pytest.mark.parametrize('defer,disabled,expected',[(True,False,False),(False,False,True),(False,True,False)])
def test_real_dom_probe_does_not_generate_upload_or_mutate_snapshot(studio,defer,disabled,expected):
    s=studio;install_studio_fixture(s.page,HTML,opened=True,deferred=defer)
    if disabled:
        s.flow.panel().get_by_role('button',name='後で生成',exact=True).evaluate('e=>e.remove()')
        s.flow.panel().get_by_role('button',name='生成',exact=True).evaluate('e=>e.disabled=true')
    engine=NotebookEngineAdapter(s.dom,persist_identity=s.repo.save);engine._open_job=lambda job:None
    before=s.job.to_dict()
    if defer:
        with pytest.raises(CloudLimitReached):engine.probe_generation_credit(s.job)
    else:assert engine.probe_generation_credit(s.job) is expected
    assert s.job.to_dict()==before
    assert not s.page.get_by_role('dialog').count()
    assert s.page.evaluate('window.generateClicks+window.badClicks+window.chatSends')==0


@pytest.mark.parametrize('phase',['PHASE1','PHASE2'])
def test_shared_gui_limit_and_recovery_message(tmp_path,phase):
    from test_gui import _window, _drain_until
    pipe,clock,job=pipeline(tmp_path)
    window,settings,controller,bridge=_window(tmp_path,[])
    _drain_until(lambda:not bridge.busy)
    assert window.switch_gui(phase)
    view=CreditLimitViewModel.from_status(pipe.cloud_limit.status())
    assert view.active and '10分ごと' in view.text and '次回確認:' in view.text
    assert 'ユーザー確認' not in view.text and 'エラー' not in view.text
    window._apply_runtime_status(dict(cloud_limit=pipe.cloud_limit.status(),runtime=dict(stage='CLOUD_GENERATION_PAUSED',human_modal_wait=False)))
    assert '10分ごと' in window.limit_label.text()
    assert not window.human_modal_banner.isVisible()
    pipe.cloud_limit.release()
    assert '利用可能' in CreditLimitViewModel.from_status(pipe.cloud_limit.status()).text
    window._apply_runtime_status(dict(cloud_limit=pipe.cloud_limit.status(),runtime=dict(stage='CREDIT_RECOVERED',human_modal_wait=False)))
    assert '利用可能' in window.limit_label.text()
    window.close()


def test_pipeline_limit_does_not_fail_or_consume_generation_retry(tmp_path):
    pipe,clock,job=pipeline(tmp_path);pipe.cloud_limit.release()
    events=[];pipe.runtime_callback=events.append
    def submit(current):raise CloudLimitReached(LimitObservation(CREDIT_LIMIT_MESSAGE,None))
    pipe.notebook.submit=submit
    before=pipe.jobs.get(job.id).attempt_by_stage
    pipe._run_notebook_job(job)
    saved=pipe.jobs.get(job.id)
    assert saved.state is JobState.WAITING and not saved.error_code and not saved.failure_class
    assert saved.attempt_by_stage==before
    assert pipe.cloud_limit.blocked and pipe.cloud_limit.recheck_delay==600
    pipe._refresh_capabilities()
    assert not pipe.capabilities.can_generate
    assert pipe.capabilities.can_upload_source and pipe.capabilities.can_chat
    assert not pipe.all_tasks_completed()
    assert any(e['stage']=='CLOUD_GENERATION_PAUSED' and not e['human_modal_wait'] for e in events)


@pytest.mark.parametrize('stage',[JobState.RAW_READY,JobState.ENDING,JobState.HLS_ENCODING])
def test_credit_limit_allows_local_processing_and_keeps_pending(tmp_path,stage):
    pipe,clock,pending=pipeline(tmp_path)
    raw=tmp_path/'raw_files/local.mp4';raw.parent.mkdir(exist_ok=True);raw.write_bytes(b'validated raw')
    local=Job('local.txt',state=stage,raw_path=str(raw),edited_path=str(raw))
    pipe.jobs.save(local)
    pipe.run_cycle()
    saved=pipe.jobs.get(local.id)
    assert saved.state is JobState.COMPLETED, saved.error_message
    assert pipe.jobs.get(pending.id).state is JobState.WAITING
    assert not pipe.notebook.events
    assert pipe.wait_seconds==600
    assert not pipe.all_tasks_completed()


@pytest.mark.parametrize('stage',[JobState.WAITING_VIDEO,JobState.DOWNLOAD_PENDING,JobState.DOWNLOADING])
def test_credit_limit_allows_artifact_check_and_download(tmp_path,stage):
    pipe,clock,pending=pipeline(tmp_path)
    remote=Job('remote.txt',state=stage,notebook_id='remote',
        notebook_url='https://notebook.google.com/notebook/remote',artifact_status='READY')
    pipe.jobs.save(remote);pipe.notebook.statuses[remote.id]='READY'
    pipe.run_cycle()
    assert ('download',remote.id) in pipe.notebook.events
    assert not any(e[0]=='submit' for e in pipe.notebook.events)
    assert pipe.jobs.get(pending.id).state is JobState.WAITING


def test_generation_recovers_before_next_local_and_preserves_snapshot(tmp_path):
    pipe,clock,pending=pipeline(tmp_path);pipe.ffmpeg_concurrency=1
    events=[]
    for name in ('first','second'):
        raw=tmp_path/'raw_files'/(name+'.mp4');raw.parent.mkdir(exist_ok=True);raw.write_bytes(b'raw')
        pipe.jobs.save(Job(name+'.txt',state=JobState.ENDING,raw_path=str(raw)))
    original=pipe.ending.process
    def ending(*args,**kwargs):
        events.append('local');result=original(*args,**kwargs)
        clock[0]+=timedelta(seconds=600)
        return result
    pipe.ending.process=ending
    pipe.notebook.probe_generation_credit=lambda job:events.append('probe') or True
    pipe.run_cycle()
    assert events==['local','probe'], [(j.state,j.error_message) for j in pipe.jobs.list()]
    assert not pipe.cloud_limit.blocked
    pipe.run_cycle()
    assert pipe.notebook.events[0]==('submit',pending.id)
    assert not pipe._needs_dispatch(pipe.jobs.get(pending.id))


@pytest.mark.parametrize('state',[JobState.GENERATING,JobState.WAITING_VIDEO,JobState.DOWNLOAD_PENDING])
def test_existing_generation_is_not_resubmitted_after_probe(tmp_path,state):
    pipe,clock,pending=pipeline(tmp_path)
    existing=Job('existing.txt',state=state,notebook_id='existing',notebook_url='https://notebook.google.com/notebook/existing',
        studio_generate_claimed_at=NOW.isoformat(),generation_started_at=NOW.isoformat())
    pipe.jobs.save(existing);clock[0]+=timedelta(seconds=600)
    pipe.notebook.probe_generation_credit=lambda job:True
    pipe._refresh_capabilities()
    assert pipe.capabilities.can_generate
    assert not pipe._needs_dispatch(pipe.jobs.get(existing.id))


@pytest.mark.parametrize('exception',['auth','modal','cancel'])
def test_probe_auth_unknown_and_stop_preserve_existing_handling(tmp_path,exception):
    from djd_maker.adapters.browser import BrowserAuthenticationRequired
    from djd_maker.core.cancellation import RunCancelled
    pipe,clock,job=pipeline(tmp_path);clock[0]+=timedelta(seconds=600)
    error={'auth':BrowserAuthenticationRequired,'modal':BlockingModalError,'cancel':RunCancelled}[exception]
    def probe(_):raise error('test')
    pipe.notebook.probe_generation_credit=probe
    if exception=='auth':
        pipe._refresh_capabilities()
        assert pipe.cloud_limit.status()['credit_state']=='AUTH_REQUIRED'
        assert not pipe.capabilities.can_generate and pipe.capabilities.can_local_process
    else:
        with pytest.raises(error):pipe._refresh_capabilities()


def test_no_safe_probe_target_keeps_bounded_recheck(tmp_path):
    pipe,clock,job=pipeline(tmp_path);job.source_status='UNKNOWN';pipe.jobs.save(job)
    clock[0]+=timedelta(seconds=600)
    pipe.notebook.probe_generation_credit=lambda _:pytest.fail('unsafe target')
    pipe._refresh_capabilities()
    assert pipe.cloud_limit.recheck_delay==15 and pipe.cloud_limit.blocked
    assert pipe.cloud_limit.status()['credit_state']=='UNKNOWN_UI_NEEDS_REVIEW'


@pytest.mark.parametrize('deadline',['bad',None,'2099-01-01T00:00:00+00:00'])
def test_corrupt_future_probe_is_rechecked_instead_of_permanent_wait(tmp_path,deadline):
    pipe,clock,job=pipeline(tmp_path)
    pipe.cloud_limit.state['next_credit_probe_at']=deadline
    assert pipe.cloud_limit.due


def test_recovery_result_and_last_probe_persist_after_restart(tmp_path):
    clock=[NOW];path=tmp_path/'cloud.json';gate=CloudLimitGate(path,clock=lambda:clock[0])
    gate.block(LimitObservation(CREDIT_LIMIT_MESSAGE,None));clock[0]+=timedelta(seconds=600)
    gate.release();restored=CloudLimitGate(path,clock=lambda:clock[0])
    assert restored.state['last_credit_probe_at']==clock[0].isoformat()
    assert restored.state['credit_probe_result']=='GENERATE_AVAILABLE'
    assert not restored.blocked and 'next_credit_probe_at' not in restored.state


@pytest.mark.parametrize('part',['actions','heading','language','video_type'])
def test_unknown_settings_never_inferred_available(studio,part):
    s=studio;s.page.evaluate('openStudioFixture()');panel=s.flow.panel()
    selector={'actions':'mat-dialog-actions','heading':'h2','language':'[role=combobox]',
        'video_type':'mat-radio-group.tile-group'}[part]
    panel.locator(selector).evaluate('e=>e.remove()')
    assert generation_button_state(panel) in {S.BLOCKING_MODAL_UNKNOWN,S.GENERATE_BUTTON_UNKNOWN}
    assert not owned_video_dialog(panel)


def test_close_never_dismisses_second_unknown_modal(studio):
    s=studio;deferred(s);panel=s.flow.panel()
    s.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=dialog>Human review<button>Close</button></div>')")
    with pytest.raises(BlockingModalError):close_video_settings(s.dom,panel)
    assert s.page.get_by_role('dialog').count()==2
    assert s.page.evaluate('window.generateClicks+window.badClicks')==0


def test_generation_retry_credit_deferral_does_not_use_retry_budget(studio):
    s=studio;deferred(s)
    s.dom.inspect_status=lambda:RemoteVideoStatus.FAILED
    engine=NotebookEngineAdapter(s.dom,persist_identity=s.repo.save);engine._open_job=lambda _:None
    s.job.attempt_by_stage['generation.failed_retry']=1
    with pytest.raises(CloudLimitReached):engine.retry_failed_generation(s.job)
    assert s.job.attempt_by_stage['generation.failed_retry']==1
    assert not s.job.studio_generate_claimed_at
    assert s.page.evaluate('window.generateClicks+window.badClicks')==0


@pytest.mark.parametrize('enabled',[True,False])
def test_coexisting_deferred_and_generate_uses_current_generate_availability(studio,enabled):
    s=studio;s.page.evaluate('openStudioFixture()');panel=s.flow.panel()
    panel.get_by_role('button',name='生成',exact=True).evaluate('(e,v)=>e.disabled=!v',enabled)
    assert generation_button_state(panel) is (S.GENERATE_AVAILABLE if enabled else S.GENERATE_DEFERRED_CREDIT_LIMIT)
    assert s.page.evaluate('window.generateClicks+window.badClicks')==0


def test_video_credit_and_actual_chat_limit_remain_independent(studio,tmp_path):
    s=studio;deferred(s)
    s.page.locator('chat-panel textarea').evaluate('e=>e.disabled=true')
    s.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=alert>AIの使用量上限に達しました。チャットは 23:59 まで無効</div>')")
    with pytest.raises(CloudLimitReached) as caught:s.flow.run()
    assert caught.value.observation.chat_disabled
    pipe,clock,job=pipeline(tmp_path)
    pipe.cloud_limit.block(caught.value.observation)
    pipe._refresh_capabilities()
    assert not pipe.capabilities.can_chat and not pipe.capabilities.can_generate
    assert pipe.capabilities.can_download and pipe.capabilities.can_check_artifact
    assert pipe.capabilities.can_upload_source and pipe.capabilities.can_local_process
