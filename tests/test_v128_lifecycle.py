import ast, inspect, textwrap, time
from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from pathlib import Path
import pytest

from djd_maker.core.models import Job,JobState
from djd_maker.core.stop_reason import StopReason,MESSAGES
from djd_maker.orchestration.task_discovery import final_discovery
from djd_maker.adapters.browser import BrowserManager
from test_gui_pipeline_controller import controller

NOW=datetime.now(UTC)

@pytest.mark.parametrize('state',[JobState.WAITING,JobState.UPLOADING,JobState.GENERATING,
    JobState.WAITING_VIDEO,JobState.DOWNLOAD_PENDING,JobState.DOWNLOADING,JobState.RAW_READY,
    JobState.HLS_ENCODING,JobState.FAILED,JobState.RECOVERY_PENDING,JobState.RESERVED_WAITING_CREDIT_RESET])
def test_unfinished_job_prevents_auto_stop(state):
    result=final_discovery([Job('a.txt',state=state)],NOW)
    assert not result['complete'] and result['unfinished']

def test_future_and_limit_wait_not_completion():
    job=Job('a.txt',state=JobState.WAITING_VIDEO,next_poll_at=(NOW+timedelta(hours=1)).isoformat())
    result=final_discovery([job],NOW,True)
    assert not result['complete'] and not result['runnable'] and result['waiting']==[job.id]

def test_only_explicit_exhaustion_allows_terminal_completion():
    job=Job('a.txt',state=JobState.FAILED,failure_class='TERMINAL_FAILED')
    assert not final_discovery([job],NOW)['complete']
    job.attempt_by_stage['scheduler.recovery']=3
    assert final_discovery([job],NOW)['complete']
    job.failure_class='FATAL_FAILED'
    assert not final_discovery([job],NOW)['complete']

def test_all_completed_allows_auto_stop_but_deferred_does_not():
    job=Job('a.txt',state=JobState.COMPLETED)
    assert final_discovery([job],NOW)['complete']
    assert not final_discovery([job],NOW,deferred={job.id})['complete']

def test_duplicate_cycle_is_unknown_not_completed():
    jobs=[Job('a',id='a',duplicate_of_job_id='b'),Job('b',id='b',duplicate_of_job_id='a')]
    assert not final_discovery(jobs,NOW)['complete']

def wait_stopped(service):
    deadline=time.monotonic()+3
    while service.status()['active'] and time.monotonic()<deadline:time.sleep(.01)
    assert not service.status()['active']

def test_final_discovery_and_completion_reason(tmp_path):
    service,repo,pipe,_=controller(tmp_path,Job('a.txt'))
    messages=[];service._log_callback=messages.append
    service.start();wait_stopped(service)
    assert service.final_discovery_result['complete']
    assert service.status()['stop_reason']['code']=='ALL_TASKS_COMPLETED'
    assert any('全行程が完了' in r.get('message','') for r in messages)

def test_waiting_pipeline_stays_alive_until_user_stop(tmp_path):
    service,_,pipe,_=controller(tmp_path,Job('a.txt',state=JobState.WAITING_VIDEO,
        next_poll_at=(NOW+timedelta(hours=1)).isoformat()))
    pipe.wait_seconds=.02
    messages=[];service._log_callback=messages.append
    try:
        service.start();time.sleep(.15)
        assert service.status()['active'] and service.status()['stop_reason'] is None
        assert any('待機しています' in r.get('message','') for r in messages)
    finally:service.stop()
    assert service.status()['stop_reason']['code']=='USER_STOP'

def test_global_fatal_has_reason(tmp_path):
    service,_,pipe,_=controller(tmp_path,Job('a.txt'))
    def fail():raise OSError('storage unavailable')
    pipe.run_cycle=fail
    service.start();wait_stopped(service)
    assert service.status()['stop_reason']['code']=='STORAGE_FATAL'

def test_close_has_distinct_reason(tmp_path):
    service,_,_,_=controller(tmp_path)
    service.shutdown()
    assert service.status()['stop_reason']['code']=='APP_CLOSE'

@pytest.mark.parametrize('code',list(MESSAGES))
def test_reason_message_never_empty(code):
    assert StopReason.make(code).message

@pytest.mark.parametrize('alive,history',[(True,'closed'),(False,'opened'),(True,'nonsense'),(False,'nonsense')])
def test_current_process_wins_over_history(tmp_path,alive,history):
    manager=BrowserManager(tmp_path)
    manager._auth_process=SimpleNamespace(poll=lambda:None if alive else 0)
    manager._navigation_result=history
    manager.last_closed=history
    manager.close_history=[history]
    (tmp_path/'history.json').write_text('{"was_closed":true}')
    assert manager.auth_process_alive is alive

def test_no_history_dependency_in_current_state():
    tree=ast.parse(textwrap.dedent(inspect.getsource(BrowserManager)))
    banned={'history','close_history','closed_at','last_closed','was_closed','previous_auth','previous_session',
            '_navigation_result','_authentication_result','_preflight_result'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.If,ast.While,ast.IfExp)):
            assert not ({a.attr for a in ast.walk(node.test) if isinstance(a,ast.Attribute)} & banned)
    property_tree=ast.parse(textwrap.dedent(inspect.getsource(BrowserManager.auth_process_alive.fget)))
    assert {a.attr for a in ast.walk(property_tree) if isinstance(a,ast.Attribute)}=={'_auth_process','poll'}
    assert 'auth-chrome-opened' not in inspect.getsource(BrowserManager)
    assert 'auth-chrome-closed' not in inspect.getsource(BrowserManager)

def test_immutable_current_window_rule_documented():
    text=(Path(__file__).resolve().parents[1]/'docs/DEVELOPMENT_RULES.md').read_text(encoding='utf-8')
    assert 'ユーザーの明示的な変更指示がない限り、この判定ロジックを変更してはならない' in text

@pytest.mark.parametrize('mode',['PHASE1','PHASE2'])
def test_both_gui_display_shared_stop_reason(tmp_path,mode):
    from test_v126_scheduler_contract import _window
    window,_,_,_=_window(tmp_path,[])
    try:
        window.switch_gui(mode)
        reason=StopReason.make('USER_STOP').to_dict()
        window._apply_runtime_status(dict(active=False,stop_reason=reason,last_task='HLS',next_scan_at='next'))
        assert reason['message'] in window.lifecycle_label.text()
        assert 'HLS' in window.lifecycle_label.text() and 'next' in window.lifecycle_label.text()
        assert window.lifecycle_label.minimumHeight() >= window.lifecycle_label.fontMetrics().lineSpacing()*4
        assert window.statusBar().currentMessage()==reason['message']
    finally:window.close()
