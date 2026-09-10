"""Opt-in isolated GUI/current Chrome acceptance; never creates Google work."""
import ctypes
import json
import os
import threading
import time
from datetime import datetime,UTC,timedelta
from pathlib import Path


def current_chrome(root):
    from djd_maker.adapters.browser import BrowserManager
    manager=BrowserManager(root/'normal-auth-profile')
    errors=[]
    def login():
        try:manager.open_login('about:blank')
        except Exception as exc:errors.append(repr(exc))
    worker=threading.Thread(target=login)
    worker.start()
    try:
        deadline=time.monotonic()+30
        while not manager.auth_process_alive and time.monotonic()<deadline and not errors:time.sleep(.05)
        assert manager.auth_process_alive,errors
        manager._navigation_result='auth-chrome-closed'
        manager.last_closed='old close record'
        (root/'history.json').write_text('{"was_closed":true,"previous_session":"closed"}',encoding='utf-8')
        assert manager.auth_process_alive
        process=manager._auth_process
        handles=[]
        @ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
        def visit(handle,_):
            pid=ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
            if pid.value==process.pid and ctypes.windll.user32.IsWindowVisible(handle):handles.append(handle)
            return True
        deadline=time.monotonic()+20
        while not handles and time.monotonic()<deadline:
            ctypes.windll.user32.EnumWindows(visit,0);time.sleep(.1)
        assert handles,'Owned ordinary Chrome window did not appear'
        for handle in handles:ctypes.windll.user32.PostMessageW(handle,0x0010,0,0)
        worker.join(20)
        assert not worker.is_alive() and not errors
        manager._navigation_result='auth-chrome-opened'
        manager.previous_session='opened'
        assert not manager.auth_process_alive
        return dict(passed=True,normal_chrome=True,open_with_stale_closed=True,
                    closed_with_stale_open=True,history_file_ignored=True,owned_windows=len(handles),google_operations=0)
    finally:
        if manager.auth_process_alive:
            manager._auth_process.terminate()
        worker.join(10)
        manager.stop()


def run_lifecycle_smoke(root:Path,report:Path):
    if not __debug__:return 3  # Never report PASS after compiled-out assertions.
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from PySide6.QtCore import QTimer
    from djd_maker.core.models import Job,JobState
    from djd_maker.core.repositories import JobRepository
    from djd_maker.gui.app import build_desktop
    from djd_maker.orchestration.task_discovery import final_discovery
    root=root.resolve()
    if root.exists():raise ValueError('Fresh isolated acceptance directory required')
    root.mkdir(parents=True)
    result=dict(passed=False,assertions_enabled=__debug__,execution='FROZEN_EXE' if getattr(__import__('sys'),'frozen',False) else 'SOURCE',
                google_operations=0)
    try:
        result['chrome']=current_chrome(root)
        repo=JobRepository(root/'system/jobs')
        source=root/'input/canonical.txt';source.parent.mkdir();source.write_text('fixture only',encoding='utf-8')
        canonical=Job(str(source),id='canonical',created_at='2000')
        duplicate=Job(str(source),id='duplicate',created_at='2001',state=JobState.FAILED,
                      error_code='OUTPUT_NAME_COLLISION',failure_class='FATAL_FAILED')
        pending=Job(str(root/'input/pending.txt'),id='pending',state=JobState.WAITING_VIDEO,
                    next_poll_at=(datetime.now(UTC)+timedelta(days=1)).isoformat())
        error=Job(str(root/'input/error.txt'),id='error',state=JobState.FAILED,failure_class='SOURCE_UPLOAD_FAILED')
        for job in (canonical,duplicate,pending,error):repo.save(job)
        app,window,service=build_desktop(root)
        assert repo.get('duplicate').duplicate_of_job_id=='canonical'
        assert repo.get('duplicate').error_code is None
        result['startup_false_collision']=True
        window.preset_repository.select(window.preset_repository.create('acceptance','never sent').id)
        wait_times=[];messages=[];errors=[]
        long_wait=os.environ.get('DJD_PACKAGING_TEN_MINUTE_WAIT')=='1'
        wait_seconds=600 if long_wait else .5
        class FixturePipeline:
            scheduler=None
            wait_seconds=600 if long_wait else .5
            cycles=0
            def run_cycle(self):
                self.cycles+=1
                current=repo.get('canonical')
                if current.state is not JobState.COMPLETED:
                    current.state=JobState.COMPLETED;repo.save(current)
                    self.job_callback(current)
                    failed=repo.get('error');failed.attempt_by_stage['scheduler.recovery']=3
                    failed.failure_class='TERMINAL_FAILED';repo.save(failed)
                    self.job_callback(failed)
                    self.runtime_callback(dict(stage='job.error',job='error',level='WARNING',
                        message='ファイル error はエラーになりました。他のjobを継続します。'))
                if repo.get('pending').state is not JobState.COMPLETED:
                    wait_times.append(time.monotonic())
                    view=dict(priority=5,task='未完成動画待ち',capabilities={},terminal_failed=1,
                              next_scan_at=(datetime.now(UTC)+timedelta(seconds=wait_seconds)).isoformat())
                    self.runtime_callback(dict(stage='scheduler.wait',scheduler=view,
                        message='現在実行可能なjobがないため、次回確認まで待機しています。'))
            def final_discovery(self):return final_discovery(repo.list(),datetime.now(UTC))
        pipe=FixturePipeline()
        service.pipeline_factory=lambda:pipe
        service._error_callback=lambda *value:errors.append(value)
        window.controller.log_received.connect(lambda r:messages.append(r.get('message','')))
        window.controller.operation_failed.disconnect(window._operation_failed)
        window.controller.operation_failed.connect(lambda *value:errors.append(value))
        started=time.monotonic();phase=0;paused_at=None
        result['gui_modes']=[]
        def tick():
            nonlocal phase,paused_at
            try:
                if time.monotonic()-started>wait_seconds+120:raise TimeoutError('Lifecycle GUI acceptance timeout')
                if phase==0 and wait_times:
                    assert service.status()['active'] and service.status()['stop_reason'] is None
                    window.grab().save(str(root/'waiting.png'))
                    window.pause_button.click();phase=1
                elif phase==1 and service.cancellation.paused.is_set():
                    if paused_at is None:paused_at=time.monotonic()
                    if time.monotonic()-paused_at>.2:
                        window.start_button.click();phase=2
                elif phase==2 and len(wait_times)>=2 and wait_times[-1]-wait_times[0]>=wait_seconds:
                    window.stop_button.click();phase=3
                elif (phase==3 and not service.status()['active'] and not window.controller.busy
                      and not window._active_run and not window._running and not window._paused):
                    assert service.status()['stop_reason']['code']=='USER_STOP'
                    result['user_stop_message']=service.status()['stop_reason']['message']
                    assert window.switch_gui('PHASE1')
                    phase=31
                elif phase in (31,32) and window.lifecycle_label.isVisible():
                    # Allow the native event loop to show/repaint newly built
                    # children before capturing evidence of the switched UI.
                    mode='PHASE1' if phase==31 else 'PHASE2'
                    assert result['user_stop_message'] in window.lifecycle_label.text(), window.lifecycle_label.text()
                    assert window.job_table.isVisible()
                    visible=window.lifecycle_label.visibleRegion().boundingRect().height()
                    assert visible>=window.lifecycle_label.fontMetrics().lineSpacing()*4,(mode,visible)
                    window.grab().save(str(root/(mode+'-stop.png')))
                    result['gui_modes'].append(mode)
                    if phase==31:
                        assert window.switch_gui('PHASE2')
                        phase=32
                        return
                    job=repo.get('pending');job.state=JobState.COMPLETED;repo.save(job)
                    window.start_button.click();phase=4
                elif (phase==4 and service.status()['stop_reason'] and not service.status()['active']
                      and not window.controller.busy and
                      (window._current_runtime_status.get('stop_reason') or {}).get('code')=='ALL_TASKS_COMPLETED'):
                    assert service.status()['stop_reason']['code']=='ALL_TASKS_COMPLETED'
                    result['completion_message']=service.status()['stop_reason']['message']
                    assert result['completion_message'] in window.lifecycle_label.text()
                    window.grab().save(str(root/'all-completed.png'))
                    assert any('他のjobを継続' in message for message in messages)
                    assert any('待機しています' in message for message in messages)
                    assert not errors,errors
                    result.update(passed=True,wait_rescan_seconds=wait_times[-1]-wait_times[0],
                        pause_resume=True,job_error_continues=True,cycles=pipe.cycles,messages=messages)
                    timer.stop();window.close();app.quit()
            except Exception as exc:
                import traceback
                result['error']=repr(exc);result['traceback']=traceback.format_exc();timer.stop();window.close();app.quit()
        window.show()
        timer=QTimer();timer.timeout.connect(tick);timer.start(50)
        QTimer.singleShot(200,window.start_processing)
        app.exec()
        service.shutdown()
        result['app_close_reason']=service.status()['stop_reason']['code']
    except Exception as exc:
        import traceback
        result.update(error=repr(exc),traceback=traceback.format_exc())
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 9
