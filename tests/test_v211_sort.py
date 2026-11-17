"""Observed home sort menu recovery without replaying any Job side effects."""
from types import SimpleNamespace
import pytest
from test_v12_chat_dom import browser
from djd_maker.adapters.notebook_modal import ensure_notebook_interactable,wait_for_human_modal,BlockingModalError
from djd_maker.adapters.sort_menu import recover_sort_menu,classify_overlay,OverlayKind
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.testing.sort_fixture import install_sort_fixture,HOME,NOTEBOOK_URL


@pytest.fixture
def sort_page(browser):
    context=browser.new_context();context.route('https://notebook.google.com/**',lambda route:route.fulfill(body='<main></main>'))
    page=context.new_page();page.goto(HOME);install_sort_fixture(page)
    yield page
    context.close()


def guard(page,**kwargs):
    events=[]
    with operation_scope(lambda stage,fields:events.append((stage,fields))):
        ensure_notebook_interactable(page,owned_context=page.context,target=page.locator('#title'),poll_seconds=.01,**kwargs)
    return events


def test_sort_role_items_and_backdrop_are_recognized(sort_page):
    p=sort_page
    assert classify_overlay(p.get_by_role('menu')) is OverlayKind.SORT_MENU
    assert p.get_by_role('menuitemradio').count()==2
    result=recover_sort_menu(p)
    assert result.state=='RECOVERED' and p.evaluate('window.escapes')==1
    assert not p.get_by_role('menu').count() and not p.locator('.cdk-overlay-backdrop').count()


def test_safe_escape_title_actionable_click_and_selection_unchanged(sort_page):
    p=sort_page;events=guard(p)
    assert not any(fields.get('human_modal_wait') for _,fields in events)
    assert 'NON_BLOCKING_SORT_MENU' in [stage for stage,_ in events]
    assert p.evaluate('window.selectionChanges+window.titleClicks+window.wrongClicks')==0
    p.locator('#title').click();assert p.url==NOTEBOOK_URL and p.evaluate('window.titleClicks')==1


def test_dismiss_waits_for_backdrop_animation(sort_page):
    p=sort_page;install_sort_fixture(p,backdrop_delay=180)
    assert recover_sort_menu(p).state=='RECOVERED'
    assert not p.locator('.cdk-overlay-backdrop').count()


def test_failed_dismiss_only_two_escapes_then_human_and_auto_resume(sort_page):
    p=sort_page;p.evaluate('window.ignoreEscape=true');messages=[]
    def actor(message):
        messages.append(message)
        if message=='MODAL_STILL_VISIBLE':
            p.evaluate("document.querySelector('[role=menu]').remove();document.querySelector('.cdk-overlay-backdrop').remove()")
    events=guard(p,diagnostic=actor)
    assert p.evaluate('window.escapes')==2
    assert any(stage=='SORT_MENU_DISMISS_NEEDS_REVIEW' for stage,_ in events)
    assert any('並び替えメニュー' in str(fields.get('message','')) for _,fields in events)
    assert any(stage=='STALE_HUMAN_WAIT_CLEARED' for stage,_ in events)
    assert not any(stage=='UNKNOWN_MODAL_DETECTED' for stage,_ in events)


def test_sort_dismiss_never_keys_concurrent_unknown_modal(sort_page):
    p=sort_page;p.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=dialog id=unknown style=\"position:fixed;inset:0;z-index:30\">Unknown<button onclick=\"window.wrongClicks++\">Close</button></div>')")
    def human(message):
        if message=='MODAL_STILL_VISIBLE':p.evaluate("document.querySelector('#unknown').remove()")
    events=guard(p,diagnostic=human)
    assert p.evaluate('window.unsafeEscapes+window.wrongClicks')==0
    assert any(stage=='UNKNOWN_MODAL_DETECTED' for stage,_ in events)
    assert any(stage=='PIPELINE_RESUMED' for stage,_ in events)


@pytest.mark.parametrize('text,exception',[
    ('ログインが必要','auth'),('同意してください','blocking'),('セキュリティ警告','blocking'),('削除しますか','blocking'),
    ('AIの使用量上限に達しました','quota')])
def test_dangerous_dialogs_take_precedence(sort_page,text,exception):
    from djd_maker.adapters.browser import BrowserAuthenticationRequired
    from djd_maker.core.cloud_limit import CloudLimitReached
    p=sort_page;p.evaluate('(text)=>{const e=document.createElement("div");e.role="dialog";e.textContent=text;document.body.append(e)}',text)
    expected={'auth':BrowserAuthenticationRequired,'blocking':BlockingModalError,'quota':CloudLimitReached}[exception]
    with pytest.raises(expected):guard(p)
    assert p.evaluate('window.escapes')==0


@pytest.mark.parametrize('mutation',[
    "document.querySelector('#sort').setAttribute('aria-label','Other menu')",
    "document.querySelector('#sort').setAttribute('aria-controls','another-menu')",
    "document.querySelector('[role=menuitemradio]').textContent='Delete'",
    "document.querySelector('[role=menu]').insertAdjacentHTML('beforeend','<button role=menuitemradio>Extra</button>')",
    "document.querySelector('.cdk-overlay-backdrop').classList.remove('cdk-overlay-transparent-backdrop')",
    "document.querySelector('.cdk-overlay-container').insertAdjacentHTML('afterbegin','<div class=cdk-overlay-backdrop>Another</div>')",
    "history.pushState({},'', '/notebook/other')",
])
def test_ambiguous_or_wrong_scope_is_not_auto_dismissed(sort_page,mutation):
    p=sort_page;p.evaluate(mutation)
    assert recover_sort_menu(p).state=='ABSENT'
    assert p.evaluate('window.escapes')==0


def test_stale_wait_rechecks_current_sort_before_entering_human_review(sort_page):
    p=sort_page;events=[]
    with operation_scope(lambda stage,fields:events.append((stage,fields))):
        wait_for_human_modal(p,diagnostic=lambda _:None,target=p.locator('#title'),verify_ready=None,
            owned_context=p.context,lifecycle_guard=None,poll_seconds=.01)
    assert any(stage=='STALE_HUMAN_WAIT_CLEARED' for stage,_ in events)
    assert not any(fields.get('human_modal_wait') for _,fields in events)


def test_active_download_never_receives_escape(sort_page):
    p=sort_page;guard(p,lifecycle_guard=SimpleNamespace(active_download_count=1))
    assert p.evaluate('window.escapes')==0 and p.get_by_role('menu').count()==1


@pytest.mark.parametrize('role,expected',[('menu',OverlayKind.DROPDOWN),('listbox',OverlayKind.DROPDOWN),('tooltip',OverlayKind.TOOLTIP)])
def test_other_overlay_kinds_are_distinguished(sort_page,role,expected):
    p=sort_page;p.evaluate('(role)=>{const e=document.createElement("div");e.id="other";e.role=role;e.textContent="other";document.body.append(e)}',role)
    assert classify_overlay(p.locator('#other')) is expected


def test_chrome_restore_notice_surrogate_is_not_clicked_or_restarted(sort_page):
    p=sort_page;install_sort_fixture(p,opened=False)
    p.evaluate("document.body.insertAdjacentHTML('beforeend','<aside role=status>ページを復元しますか？ Chromeは正しく終了しませんでした。<button onclick=\"window.wrongClicks++\">復元</button><button onclick=\"window.wrongClicks++\">×</button></aside>')")
    events=guard(p)
    assert not events and p.evaluate('window.wrongClicks+window.escapes')==0


def test_sort_recovery_preserves_job_bytes_candidate_and_wakes_scheduler(sort_page,tmp_path):
    from test_pipeline import coordinator,MemoryJobs
    from test_v122_sequential_runtime import Remote
    from djd_maker.core.models import Job
    job=Job('fixture.txt');jobs=MemoryJobs(job);pipe=coordinator(tmp_path,jobs,Remote([]));before=jobs.get(job.id).to_dict()
    pipe.wait_seconds=600
    with operation_scope(lambda stage,fields:pipe._stage_event(job,stage)):
        ensure_notebook_interactable(sort_page,target=sort_page.locator('#title'))
    assert jobs.get(job.id).to_dict()==before
    assert pipe.wait_seconds==0 and pipe._needs_dispatch(jobs.get(job.id))
    assert not pipe.notebook.events and not pipe.all_tasks_completed()


@pytest.mark.parametrize('status',['GENERATING','READY','WAITING'])
def test_existing_checkpoint_after_sort_never_creates_uploads_or_generates(sort_page,status):
    from djd_maker.adapters.notebook import NotebookDomAdapter,NotebookEngineAdapter,RemoteVideoStatus
    from djd_maker.core.models import Job,Preset
    from djd_maker.testing.studio_fixture import BODY
    p=sort_page
    dom=NotebookDomAdapter(p,interactable_guard=lambda page,**kw:ensure_notebook_interactable(page,**kw))
    dom.inspect_status=lambda:RemoteVideoStatus(status)
    dom.create_notebook=lambda:pytest.fail('duplicate Notebook')
    dom.ensure_source=lambda *_:pytest.fail('duplicate Source upload')
    dom.start_video_generation_from_chat=lambda *_:pytest.fail('duplicate Preset or generation')
    job=Job('fixture.txt',notebook_id=NOTEBOOK_URL.rsplit('/',1)[1],notebook_url=NOTEBOOK_URL)
    job.snapshot_preset(Preset('fixture','fixture',BODY,'created','updated'))
    outcome=NotebookEngineAdapter(dom).submit(job)
    assert outcome.remote_status==status and job.studio_generation_attempt==0
    assert not job.studio_generate_claimed_at and job.notebook_url==NOTEBOOK_URL


@pytest.mark.parametrize('fail_dismiss',[False,True])
def test_normal_scheduler_continues_once_after_sort_without_job_failure(sort_page,tmp_path,fail_dismiss):
    from test_pipeline import coordinator,MemoryJobs
    from test_v122_sequential_runtime import Remote
    from djd_maker.core.models import Job,JobState
    p=sort_page
    if fail_dismiss:p.evaluate('window.ignoreEscape=true')
    def human(message):
        if message=='MODAL_STILL_VISIBLE':p.evaluate("document.querySelector('[role=menu]').remove();document.querySelector('.cdk-overlay-backdrop').remove()")
    class Task(Remote):
        def submit(self,job):
            ensure_notebook_interactable(p,target=p.locator('#title'),diagnostic=human,poll_seconds=.01)
            return super().submit(job)
    source=tmp_path/'lesson.txt';source.write_text('synthetic lesson',encoding='utf8')
    job=Job(str(source));jobs=MemoryJobs(job);remote=Task([]);pipe=coordinator(tmp_path,jobs,remote)
    pipe.run_cycle()
    assert jobs.get(job.id).state not in {JobState.FAILED,JobState.COMPLETED}
    assert not jobs.get(job.id).failure_class and not jobs.get(job.id).error_code
    assert remote.events.count(('submit',job.id))==1
    pipe.run_cycle();assert remote.events.count(('submit',job.id))==1
