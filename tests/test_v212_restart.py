"""Restart, current profile ownership and reason-specific recovery contracts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from test_v210_credit import pipeline
from test_browser_adapter import harness
from test_v25_studio_direct import studio, browser
from djd_maker.adapters.browser import BrowserProfileLocked, BrowserAuthenticationRequired
from djd_maker.core.cloud_limit import CloudLimitGate, LimitObservation, CREDIT_LIMIT_MESSAGE, CloudLimitReached
from djd_maker.core.models import Job, JobState
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.core.download_guard import DownloadLifecycleGuard
from djd_maker.gui.presentation_models import CreditLimitViewModel

JST=timezone(timedelta(hours=9))
NINE=datetime(2026,11,18,9,tzinfo=JST)


def due_pipe(tmp_path):
    pipe,clock,job=pipeline(tmp_path)
    clock[0]+=timedelta(seconds=600)
    return pipe,clock,job


@pytest.mark.parametrize('restart',[13,14,23])
def test_expired_credit_deadline_rechecked_on_restart(tmp_path,restart):
    path=tmp_path/'limit.json';gate=CloudLimitGate(path,clock=lambda:NINE)
    gate.block(LimitObservation('AI usage limit reached',NINE.replace(hour=12,minute=21)))
    restored=CloudLimitGate(path,clock=lambda:NINE.replace(hour=restart))
    assert restored.status()['credit_state']=='CREDIT_RECHECK_DUE'
    assert restored.recheck_delay==0 and restored.blocked


def test_restart_does_not_trust_in_progress_forever(tmp_path):
    p,c,j=due_pipe(tmp_path);p.cloud_limit.begin_recheck()
    restored=CloudLimitGate(p.cloud_limit.document.path,clock=lambda:c[0])
    assert restored.due and restored.blocked
    assert restored.status()['credit_state']=='CREDIT_RECHECK_DUE'


@pytest.mark.parametrize('result',[True,False,'limit','busy','transient','auth'])
def test_current_ui_required_for_credit_recovery_and_reasons(tmp_path,result):
    p,c,j=due_pipe(tmp_path);before=p.jobs.get(j.id).to_dict();events=[];p.runtime_callback=events.append
    def probe(target):
        assert target.id==j.id
        assert p.cloud_limit.status()['credit_state']=='CREDIT_RECHECK_IN_PROGRESS'
        if result=='limit':raise CloudLimitReached(LimitObservation(CREDIT_LIMIT_MESSAGE,None))
        if result=='busy':raise BrowserProfileLocked('fixture')
        if result=='transient':raise TimeoutError('fixture')
        if result=='auth':raise BrowserAuthenticationRequired('fixture')
        return result
    p.notebook.probe_generation_credit=probe;p._refresh_capabilities()
    expected={True:'CREDIT_RECOVERED',False:'UNKNOWN_UI_NEEDS_REVIEW','limit':'CREDIT_LIMIT_ACTIVE',
        'busy':'CREDIT_RECHECK_BROWSER_BUSY','transient':'CREDIT_RECHECK_TRANSIENT_ERROR','auth':'AUTH_REQUIRED'}[result]
    assert p.cloud_limit.status()['credit_state']==expected
    assert p.capabilities.can_generate is (result is True)
    assert p.jobs.get(j.id).to_dict()==before
    assert p.capabilities.can_local_process and not any(e.get('human_modal_wait') for e in events)
    if result is not True:assert p.cloud_limit.recheck_delay==(600 if result=='limit' else 15)


def test_browser_profile_locked_is_not_quota_limit_and_recovers(tmp_path):
    p,c,j=due_pipe(tmp_path);calls=[];original=dict(p.cloud_limit.state)
    def probe(target):
        calls.append(target.id)
        if len(calls)<3:raise BrowserProfileLocked('fixture')
        return True
    p.notebook.probe_generation_credit=probe
    for delay in (15,30):
        p._refresh_capabilities();assert p.cloud_limit.recheck_delay==delay
        assert p.cloud_limit.state['observed_at']==original['observed_at']
        assert not p.capabilities.can_generate and not p.capabilities.can_download
        for _ in range(4):p._refresh_capabilities()
        c[0]+=timedelta(seconds=delay)
    p._refresh_capabilities()
    assert len(calls)==3 and p.capabilities.can_generate and p.capabilities.can_download
    p.run_cycle();assert p.notebook.events.count(('submit',j.id))==1
    p.run_cycle();assert p.notebook.events.count(('submit',j.id))==1


def test_repeated_probe_failure_bounded_and_restart_retryable(tmp_path):
    p,c,j=due_pipe(tmp_path)
    p.notebook.probe_generation_credit=lambda _:(_ for _ in ()).throw(BrowserProfileLocked('fixture'))
    for expected in (15,30,60,120,120,120):
        p._refresh_capabilities();assert p.cloud_limit.recheck_delay==expected
        restored=CloudLimitGate(p.cloud_limit.document.path,clock=lambda:c[0])
        assert restored.recheck_delay==expected and restored.blocked
        c[0]+=timedelta(seconds=expected)
    assert not p.all_tasks_completed() and p.jobs.get(j.id).state is JobState.WAITING


@pytest.mark.parametrize('rows,classification',[([], 'NO_LIVE_OWNER'),([{'pid':999,'parent_pid':1}],'FOREIGN_LIVE'),(None,'UNCERTAIN')])
def test_current_owner_classified_without_force_unlock(tmp_path,rows,classification):
    m,driver,_=harness(tmp_path);m._profile_owner_probe=lambda _:rows
    m._profile_lock_probe=m._default_profile_lock_probe
    m.user_data_dir.mkdir(exist_ok=True)
    markers={n:b'preserve' for n in ('lockfile','SingletonLock','SingletonCookie','DevToolsActivePort')}
    for name,data in markers.items():(m.user_data_dir/name).write_bytes(data)
    assert m.profile_owner_status()['owner']==classification
    if rows or rows is None:
        with pytest.raises(BrowserProfileLocked):m.start()
        assert not driver.launches
    else:m.start();m.stop()
    assert all((m.user_data_dir/n).read_bytes()==v for n,v in markers.items())


@pytest.mark.parametrize('prepare',[False,True])
def test_owned_browser_connection_reused_duplicate_launch_prevented(tmp_path,prepare):
    m,driver,_=harness(tmp_path);page=m.start()
    m._profile_lock_probe=lambda _:pytest.fail('owned context is not an external lock')
    events=[]
    with operation_scope(lambda stage,fields:events.append(stage)):
        assert (m.prepare_for_processing() if prepare else m.start()) is page
    assert len(driver.launches)==1 and 'BROWSER_RECONNECT_SUCCESS' in events
    assert m.profile_owner_status()['owner']=='CONNECTED_CURRENT'
    m.stop();assert m.context is None


def test_profile_available_reconnects_after_foreign_owner_release(tmp_path):
    m,driver,_=harness(tmp_path);owners=[[{'pid':999}]]
    m._profile_owner_probe=lambda _:owners[0];m._profile_lock_probe=m._default_profile_lock_probe
    with pytest.raises(BrowserProfileLocked):m.start()
    owners[0]=[];m.start();assert len(driver.launches)==1
    m.stop()


@pytest.mark.parametrize('phase',['PHASE1','PHASE2'])
@pytest.mark.parametrize('reason',['CREDIT_RECHECK_DUE','CREDIT_RECHECK_BROWSER_BUSY','CREDIT_RECHECK_TRANSIENT_ERROR','AUTH_REQUIRED','UNKNOWN_UI_NEEDS_REVIEW'])
def test_recovery_status_shared_by_both_gui_layouts(phase,reason):
    # Both layouts consume the same presentation model, with no modal side effect.
    value=CreditLimitViewModel.from_status({'active':True,'credit_state':reason})
    assert value.active and '回復確認中' in value.text
    assert '上限のため保留中' not in value.text
    if reason=='CREDIT_RECHECK_BROWSER_BUSY':assert 'Chrome接続' in value.text


@pytest.mark.parametrize('state',[JobState.RAW_READY,JobState.ENDING,JobState.HLS_ENCODING])
def test_local_tasks_continue_while_browser_busy(tmp_path,state):
    p,c,j=due_pipe(tmp_path)
    raw=tmp_path/'raw_files/local.mp4';raw.parent.mkdir(exist_ok=True);raw.write_bytes(b'valid fake media')
    local=Job('local.txt',state=state,raw_path=str(raw),edited_path=str(raw));p.jobs.save(local)
    p.notebook.probe_generation_credit=lambda _:(_ for _ in ()).throw(BrowserProfileLocked('fixture'))
    p.run_cycle()
    assert p.jobs.get(local.id).state is JobState.COMPLETED
    assert p.jobs.get(j.id).state is JobState.WAITING and not p.all_tasks_completed()
    assert 0<p.wait_seconds<=120 and not p.notebook.events


def test_expired_credit_probe_preempts_new_local_task(tmp_path):
    p,c,j=due_pipe(tmp_path);order=[]
    p.notebook.probe_generation_credit=lambda _:order.append('probe') or True
    original=p.notebook.submit
    p.notebook.submit=lambda job:order.append('generation') or original(job)
    p.run_cycle();assert order[:2]==['probe','generation']


def test_running_local_and_active_download_defer_probe(tmp_path):
    p,c,j=due_pipe(tmp_path);calls=[];p.notebook.probe_generation_credit=lambda _:calls.append('probe') or True
    p._credit_local_active=True;p._refresh_capabilities();assert not calls
    p._credit_local_active=False
    guard=DownloadLifecycleGuard(tmp_path/'deferred.json')
    class Owner:
        lifecycle_guard=guard
        def interact(self):pass
    p.notebook.dom=SimpleNamespace(interactable_guard=Owner().interact)
    with guard.transfer(j.id):p._refresh_capabilities();assert not calls
    p._refresh_capabilities();assert calls==['probe']


def test_browser_connection_events_do_not_save_job(tmp_path):
    p,c,j=due_pipe(tmp_path);before=p.jobs.get(j.id).to_dict()
    for stage in ('BROWSER_OWNER_CLASSIFIED','BROWSER_RECONNECT_ATTEMPT','BROWSER_RECONNECT_SUCCESS'):
        p._stage_event(j,stage)
    assert p.jobs.get(j.id).to_dict()==before


@pytest.mark.parametrize('state',[JobState.DOWNLOAD_PENDING,JobState.FAILED])
def test_browser_busy_remote_candidates_wait_without_spin_or_failure(tmp_path,state):
    p,c,j=due_pipe(tmp_path)
    remote=Job('remote.txt',state=state,notebook_id='remote',notebook_url='https://notebook.google.com/notebook/remote')
    p.jobs.save(remote)
    p.notebook.probe_generation_credit=lambda _:(_ for _ in ()).throw(BrowserProfileLocked('fixture'))
    p.run_cycle()
    assert 0<p.wait_seconds<=15 and not p.notebook.events
    assert p.jobs.get(remote.id).state is state


def test_completed_notebook_read_only_probe_restores_pending_generation(tmp_path):
    p,c,j=due_pipe(tmp_path);j.source_status='UNKNOWN';p.jobs.save(j)
    complete=Job('complete.txt',state=JobState.COMPLETED,source_status='READY',notebook_id='complete',
        notebook_url='https://notebook.google.com/notebook/complete',generation_started_at=c[0].isoformat())
    p.jobs.save(complete);before=p.jobs.get(complete.id).to_dict();calls=[]
    p.notebook.probe_credit_recovery=lambda target:calls.append(target.id) or True
    p._refresh_capabilities()
    assert calls==[complete.id] and p.capabilities.can_generate
    assert p.jobs.get(complete.id).to_dict()==before and not p._needs_dispatch(complete)


def test_credit_recovery_hydrates_completed_notebook_without_generation(studio):
    from djd_maker.adapters.notebook import NotebookEngineAdapter,RemoteVideoStatus
    s=studio;engine=NotebookEngineAdapter(s.dom);engine._open_job=lambda _:None
    count=[0]
    def status():
        count[0]+=1
        return RemoteVideoStatus.UNKNOWN if count[0]==1 else RemoteVideoStatus.READY
    s.dom.inspect_status=status
    before=s.job.to_dict()
    assert engine.probe_credit_recovery(s.job)
    assert count[0]>=2 and s.job.to_dict()==before
    assert s.page.evaluate('window.generateClicks+window.badClicks+window.chatSends')==0


@pytest.mark.parametrize('arg',['--user-data-dir="C:\\日本語 path\\profile"','"--user-data-dir=C:\\日本語 path\\profile"'])
def test_current_process_profile_argument_handles_unicode_and_spaces(arg):
    from djd_maker.adapters.profile_owner import profile_argument
    assert profile_argument('chrome.exe '+arg)=='C:\\日本語 path\\profile'


def test_current_owner_query_works_with_portable_minimal_path(tmp_path,monkeypatch):
    import os,subprocess
    from djd_maker.adapters import profile_owner
    monkeypatch.setenv('PATH',str(__import__('pathlib').Path(os.environ['SystemRoot'])/'System32'))
    original=subprocess.run;codes=[]
    def observed(*args,**kwargs):
        result=original(*args,**kwargs);codes.append(result.returncode);return result
    monkeypatch.setattr(profile_owner.subprocess,'run',observed)
    profile_owner.profile_owners(tmp_path)
    assert codes==[0]  # No dependency on Python/Chrome/PowerShell in PATH.
