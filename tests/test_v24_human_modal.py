"""Human-review contracts: no automatic dismissal, retry, or browser cleanup."""
import threading
import time
from types import SimpleNamespace
import pytest

from djd_maker.adapters.notebook_modal import (
    ensure_notebook_interactable, classify_modal, ModalKind, BlockingModalError,
    HUMAN_MODAL_MESSAGE,
)
from djd_maker.core.cancellation import CancellationToken, RunCancelled, cancellation_scope, active_monotonic
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.core.download_guard import DownloadLifecycleGuard
from test_v12_chat_dom import browser, page


class Surface:
    def __init__(self,page,kind):self.page,self.kind=page,kind
    def count(self):return int(self.is_visible())
    def nth(self,index):return self
    @property
    def first(self):return self
    def is_visible(self):return self.page.modal if self.kind=='dialog' else self.page.scrim
    def inner_text(self):return self.page.text


class HumanPage:
    def __init__(self,*,dismiss_after=.2,scrim_after=.3,ready_after=.4,token=None,stop_after=None):
        self.url='https://notebook.google.com/notebook/current'
        self.elapsed=0;self.modal=True;self.scrim=True;self.text='Unrecognized introduction'
        self.dismiss_after=dismiss_after;self.scrim_after=scrim_after;self.ready_after=ready_after
        self.token=token;self.stop_after=stop_after;self.close_count=0;self.front_count=0
        self.context=SimpleNamespace(pages=[self]);self.closed=False
    def locator(self,selector):return Surface(self,'scrim' if 'scrim' in selector or 'backdrop' in selector else 'dialog')
    def is_closed(self):return self.closed
    def close(self):self.close_count+=1;self.closed=True
    def bring_to_front(self):self.front_count+=1
    def get_by_role(self,*args,**kwargs):raise AssertionError('unknown modal must not be clicked')
    def wait_for_timeout(self,ms):
        self.elapsed+=ms/1000
        self.modal=self.elapsed<self.dismiss_after;self.scrim=self.elapsed<self.scrim_after
        if self.stop_after is not None and self.elapsed>=self.stop_after:self.token.request()
        time.sleep(.001)


def run_wait(p,events=None,**kwargs):
    events=events if events is not None else []
    with operation_scope(lambda stage,fields:events.append((stage,fields))):
        return ensure_notebook_interactable(p,owned_context=p.context,
            verify_ready=lambda current:current.elapsed>=current.ready_after,poll_seconds=.01,**kwargs)


def test_unknown_modal_enters_human_wait_and_automatically_resumes():
    p=HumanPage();events=[]
    assert run_wait(p,events) is p
    stages=[stage for stage,_ in events]
    for stage in ['UNKNOWN_MODAL_DETECTED','WAITING_FOR_HUMAN_MODAL_DISMISSAL',
                  'MODAL_STILL_VISIBLE','MODAL_DISMISSED_BY_USER','NOTEBOOK_UI_RESTORED','PIPELINE_RESUMED_AFTER_MODAL']:
        assert stage in stages
    assert p.elapsed>=p.ready_after and not p.modal and not p.scrim
    assert p.close_count==0 and p.front_count==1
    assert any(fields.get('message')==HUMAN_MODAL_MESSAGE for _,fields in events)


def test_unknown_modal_has_no_short_failure_timeout():
    p=HumanPage(dismiss_after=.8,scrim_after=.8,ready_after=.8)
    run_wait(p,timeout_seconds=.0001)
    assert p.elapsed>=.8 and p.close_count==0


@pytest.mark.parametrize('command',['Stop','App Close'])
def test_stop_and_app_close_are_cancellable_without_error(command):
    token=CancellationToken();p=HumanPage(dismiss_after=100,scrim_after=100,token=token,stop_after=.1)
    with cancellation_scope(token),pytest.raises(RunCancelled):run_wait(p)
    assert p.modal and p.close_count==0


@pytest.mark.parametrize('text',['Adobe Express 詳しく見る','別会社の紹介','Advertisement','Unknown feature',
    'Error-free design tools', 'Quota available in a new plan', '上限なしの紹介', 'Consent Manager 無料体験'])
def test_unknown_products_are_not_learned_or_auto_closed(text):
    p=HumanPage();p.text=text;run_wait(p)
    assert classify_modal(text) is ModalKind.UNKNOWN and p.close_count==0
    p.elapsed=0;p.modal=p.scrim=True;run_wait(p)
    assert classify_modal(text) is ModalKind.UNKNOWN


def test_external_tabs_preserved_and_not_used_as_notebook():
    p=HumanPage();external=HumanPage();external.url='https://example.com/?token=private'
    p.context.pages.append(external)
    assert run_wait(p) is p
    assert external.close_count==0 and external.front_count==0


def test_dialog_hidden_but_scrim_blocks_resume():
    p=HumanPage(dismiss_after=.1,scrim_after=.6,ready_after=.1)
    run_wait(p);assert p.elapsed>=.6


def test_human_dismissal_detached_dom_is_reobserved_without_error():
    from playwright.sync_api import Error
    p=HumanPage();original=p.locator;observations=[]
    def locator(selector):
        observations.append(selector)
        if len(observations)==3:
            raise Error('Element detached during manual dismissal')
        return original(selector)
    p.locator=locator
    assert run_wait(p) is p
    assert p.close_count==0 and p.elapsed>=p.ready_after


def test_download_active_blocks_resume_without_closing_popup():
    p=HumanPage();guard=DownloadLifecycleGuard();guard._active['transfer']='job'
    original=p.wait_for_timeout
    def wait(ms):
        original(ms)
        if p.elapsed>=.5:guard._active.clear()
    p.wait_for_timeout=wait
    run_wait(p,lifecycle_guard=guard)
    assert p.elapsed>=.5 and p.close_count==0


def test_human_delay_excluded_from_reply_deadline():
    token=CancellationToken();p=HumanPage()
    with cancellation_scope(token):
        before=active_monotonic();wall=time.monotonic();run_wait(p)
        assert token._human_wait_duration>0
        assert active_monotonic()-before < time.monotonic()-wall


def test_job_checkpoint_remains_byte_equivalent_during_wait(tmp_path):
    from djd_maker.core.models import Job,JobState
    from test_pipeline import MemoryJobs,coordinator
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    job=Job('lesson.txt',state=JobState.WAITING_VIDEO,notebook_id='current',
            notebook_url='https://notebook.google.com/notebook/current')
    jobs=MemoryJobs(job);pipeline=coordinator(tmp_path,jobs,FakeNotebookAdapter({}))
    before=jobs.get(job.id).to_dict();p=HumanPage()
    with operation_scope(lambda stage,fields:pipeline._stage_event(job,stage)):
        ensure_notebook_interactable(p,verify_ready=lambda _:True,poll_seconds=.01)
    assert jobs.get(job.id).to_dict()==before


@pytest.mark.parametrize('text,kind',[
    ('ログインが必要',ModalKind.AUTH_REQUIRED),('利用枠の上限',ModalKind.USAGE_LIMIT),
    ('Notebookエラー',ModalKind.ERROR_MODAL),('削除しますか',ModalKind.ACTION_CONFIRMATION),
    ('同意してください',ModalKind.CONSENT_REQUIRED),('セキュリティ警告',ModalKind.SECURITY_WARNING),
])
def test_known_dangerous_modals_not_mixed_into_unknown(text,kind):
    assert classify_modal(text) is kind


def test_auth_modal_uses_existing_auth_flow(page):
    from djd_maker.adapters.browser import BrowserAuthenticationRequired
    page.set_content('<div role="dialog">ログインが必要<button>閉じる</button></div>')
    with pytest.raises(BrowserAuthenticationRequired):ensure_notebook_interactable(page)


def test_error_modal_stays_error_recovery(page):
    page.set_content('<div role="dialog">Notebookエラー<button>閉じる</button></div>')
    with pytest.raises(BlockingModalError,match='ERROR_MODAL'):ensure_notebook_interactable(page)


def test_real_dom_user_dismissal_reactivates_notebook_and_preserves_external_tab(browser):
    context=browser.new_context();p=context.new_page()
    context.route('https://notebook.google.com/**',lambda route:route.fulfill(
        content_type='text/html',body='<main><button aria-label="Create notebook">Create notebook</button></main>'))
    p.goto('https://notebook.google.com/')
    p.evaluate('''()=>{window.autoClicks=0;document.body.insertAdjacentHTML('beforeend',
      '<div role="dialog">Unknown product<button onclick="window.autoClicks++">Close</button></div>'+
      '<div data-testid="modal-scrim" style="position:fixed;inset:0;background:#8888"></div>');
      setTimeout(()=>{document.querySelector('[role=dialog]').remove();document.querySelector('[data-testid=modal-scrim]').remove();},200);}''')
    external=context.new_page();external.set_content('<title>Human review page</title>')
    events=[]
    try:
        with operation_scope(lambda s,f:events.append(s)):
            assert ensure_notebook_interactable(p,owned_context=context,poll_seconds=.05) is p
        assert p.evaluate('window.autoClicks')==0
        assert not external.is_closed() and len(context.pages)==2
        assert 'PIPELINE_RESUMED_AFTER_MODAL' in events
    finally:context.close()


def test_preset_sent_before_modal_is_not_resent(page):
    from djd_maker.adapters.notebook import NotebookDomAdapter
    from djd_maker.adapters.chat_flow import ChatFlow
    from djd_maker.adapters.replies import ReplyKind
    page.evaluate('''()=>{window.replyText='説明動画の作成を開始しました。';const button=document.querySelector('#send');
      const send=button.onclick;button.onclick=()=>{send();document.body.insertAdjacentHTML('beforeend',
        '<div role="dialog">Unknown feature<button>Close</button></div>');
        setTimeout(()=>document.querySelector('[role=dialog]').remove(),250);};}''')
    dom=NotebookDomAdapter(page,interactable_guard=lambda p,**k:ensure_notebook_interactable(p,poll_seconds=.05))
    with cancellation_scope(CancellationToken()):
        result=ChatFlow(dom,reply_timeout=4,send_timeout=2).send('one preset',max_attempts=1)
    assert result.kind is ReplyKind.GENERATION_ACCEPTED and page.evaluate('window.sends')==1


@pytest.mark.parametrize('status',['GENERATING','READY'])
def test_after_human_review_artifact_readback_prevents_preset_retry(page,status):
    from djd_maker.adapters.notebook import NotebookDomAdapter, RemoteVideoStatus
    from djd_maker.adapters.chat_flow import ChatFlow
    from djd_maker.adapters.replies import ReplyKind
    page.evaluate('''()=>{window.replyText='Different response wording';const button=document.querySelector('#send');
        const send=button.onclick;button.onclick=()=>{send();document.body.insertAdjacentHTML('beforeend',
        '<div role="dialog">Unrecognized introduction<button>Close</button></div>');
        setTimeout(()=>document.querySelector('[role=dialog]').remove(),150);};}''')
    dom=NotebookDomAdapter(page,interactable_guard=lambda p,**k:ensure_notebook_interactable(p,poll_seconds=.02))
    dom.inspect_status=lambda:RemoteVideoStatus(status)
    with cancellation_scope(CancellationToken()):
        result=ChatFlow(dom,reply_timeout=.1,send_timeout=2).send('one preset',max_attempts=2)
    assert result.kind is ReplyKind.GENERATION_ACCEPTED
    assert 'verified_current_artifact' in result.matched_terms
    assert page.evaluate('window.sends')==1


@pytest.mark.parametrize('status',['GENERATING','READY','WAITING'])
def test_checkpoint_prevents_duplicate_source_preset_and_generation(status):
    from djd_maker.adapters.notebook import NotebookEngineAdapter
    from djd_maker.core.models import Job, preset_body_sha256
    p=HumanPage()
    class Dom:
        page=p
        timeout_ms=1000
        def ensure_interactable(self):run_wait(p)
        def inspect_status(self):
            from djd_maker.adapters.notebook import RemoteVideoStatus
            return RemoteVideoStatus(status)
        def ensure_source(self,*args):raise AssertionError('duplicate Source upload')
        def submit_generation(self,*args):raise AssertionError('duplicate Preset/Generation')
    job=Job('lesson.txt',notebook_id='current',notebook_url=p.url,preset_body_snapshot='one preset',
            preset_body_sha256=preset_body_sha256('one preset'))
    result=NotebookEngineAdapter(Dom()).submit(job)
    assert result.remote_status.value==status and job.notebook_id=='current'


def test_controller_wait_preserves_job_and_resumes_without_error(tmp_path):
    from test_gui_pipeline_controller import controller
    from djd_maker.core.models import Job
    job=Job('lesson.txt');instance,jobs,pipeline,_=controller(tmp_path,job)
    p=HumanPage(dismiss_after=10000,scrim_after=10000,ready_after=0)
    statuses=[];errors=[];old_cycle=pipeline.run_cycle;before=jobs.get(job.id).to_dict()
    def cycle():
        ensure_notebook_interactable(p,verify_ready=lambda _:True,poll_seconds=.01)
        assert jobs.get(job.id).to_dict()==before
        old_cycle()
    pipeline.run_cycle=cycle
    instance.bind(jobs=lambda _:None,status=statuses.append,log=lambda _:None,error=lambda *e:errors.append(e))
    instance.start()
    try:
        deadline=time.monotonic()+3
        while not any(s.get('runtime',{}).get('human_modal_wait') for s in statuses) and time.monotonic()<deadline:time.sleep(.01)
        assert any(s.get('runtime',{}).get('human_modal_wait') for s in statuses)
        assert jobs.get(job.id).to_dict()==before and p.close_count==0
        p.dismiss_after=p.scrim_after=0;p.modal=p.scrim=False
        deadline=time.monotonic()+3
        while instance.status()['running'] and time.monotonic()<deadline:time.sleep(.01)
        assert jobs.get(job.id).state.value=='COMPLETED' and not errors
        assert any(s.get('runtime',{}).get('stage')=='PIPELINE_RESUMED_AFTER_MODAL' for s in statuses)
    finally:instance.shutdown()


@pytest.mark.parametrize('method',['stop','shutdown'])
def test_controller_stop_and_app_close_during_human_wait(tmp_path,method):
    from test_gui_pipeline_controller import controller
    from djd_maker.core.models import Job
    job=Job('lesson.txt');instance,jobs,pipeline,_=controller(tmp_path,job)
    p=HumanPage(dismiss_after=10000,scrim_after=10000);waiting=threading.Event();errors=[]
    def cycle():
        waiting.set()
        ensure_notebook_interactable(p,verify_ready=lambda _:True,poll_seconds=.01)
        raise AssertionError('task started before human dismissal')
    pipeline.run_cycle=cycle
    instance.bind(jobs=lambda _:None,status=lambda _:None,log=lambda _:None,error=lambda *e:errors.append(e))
    instance.start()
    try:
        assert waiting.wait(3)
        getattr(instance,method)()
        assert not instance.status()['running'] and not errors
        assert not instance.status()['runtime'].get('human_modal_wait')
        assert jobs.get(job.id).state.value=='WAITING' and p.close_count==0
    finally:instance.shutdown()


@pytest.mark.parametrize('gui_type',['PHASE1','PHASE2'])
def test_gui_human_message_visible_and_event_loop_responsive(tmp_path,gui_type):
    from test_gui import _window,_app
    from PySide6.QtCore import QTimer
    window,*_= _window(tmp_path,[])
    if gui_type=='PHASE2':window.switch_gui(gui_type)
    window.show();ticks=[];QTimer.singleShot(0,lambda:ticks.append('responsive'))
    try:
        window._apply_runtime_status({'running':True,'active':True,'runtime':{
            'stage':'WAITING_FOR_HUMAN_MODAL_DISMISSAL','human_modal_wait':True,'message':HUMAN_MODAL_MESSAGE}})
        _app().processEvents()
        assert ticks and window.human_modal_banner.isVisible()
        assert '閉じると処理は自動的に再開' in window.human_modal_banner.text()
        window._apply_runtime_status({'running':True,'runtime':{'stage':'PIPELINE_RESUMED_AFTER_MODAL','human_modal_wait':False}})
        assert not window.human_modal_banner.isVisible()
    finally:window.close()
