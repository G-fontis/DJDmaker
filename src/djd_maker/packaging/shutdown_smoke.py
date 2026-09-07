"""Opt-in packaged GUI Stop/Close and local-browser modal fixture acceptance."""
import json
import os
import threading
import time
from pathlib import Path


def run_shutdown_smoke(root: Path, report: Path) -> int:
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from PySide6.QtCore import QTimer
    from djd_maker.gui.app import build_desktop
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook_modal import ensure_notebook_interactable
    from djd_maker.core.cancellation import interruptible_sleep
    from djd_maker.core.models import Job

    root=root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    browser=BrowserManager(root/'browser'/'chrome-profile',headless=True)
    app,window,service=build_desktop(root,browser_manager=browser)
    app.setQuitOnLastWindowClosed(False)
    ending=root/'ending-fixture.mp4'
    ending.write_bytes(b'GUI-path-only fixture; never passed to an encoder')
    window.settings.ending_video=str(ending)
    window.settings_repository.save(window.settings)
    window.apply_settings(window.settings)
    preset=window.preset_repository.create('shutdown fixture','never sent')
    window.preset_repository.select(preset.id)
    service.jobs.save(Job(str(root/'input'/'fixture.txt')))
    ready=threading.Event();events=[];errors=[];results=[]
    class Pipeline:
        scheduler=None
        def run_cycle(self):
            page=browser.start()
            page.set_content('''<chat-panel><textarea></textarea></chat-panel>
              <div class="cdk-overlay-backdrop cdk-overlay-backdrop-showing" id="scrim"></div>
              <div role="dialog" id="modal"><h2>NotebookLMの新機能</h2>
              <button onclick="document.querySelector('#scrim').remove();document.querySelector('#modal').remove()">閉じる</button></div>''')
            ensure_notebook_interactable(page,diagnostic=events.append)
            assert page.locator('#modal').count()==0
            assert page.locator('#scrim').count()==0
            page.locator('chat-panel textarea').click()
            ready.set()
            interruptible_sleep(600)
    service.pipeline_factory=lambda:Pipeline()
    service._error_callback=lambda *args:errors.append(args)
    state={'stage':0,'requested':None,'worker':None,'start':time.monotonic()}
    def tick():
        if time.monotonic()-state['start']>45:
            errors.append(('timeout','shutdown smoke exceeded 45s'))
            service.stop();app.quit();return
        if state['stage'] in (0,2) and ready.is_set():
            state['requested']=time.monotonic();state['worker']=service._worker
            if state['stage']==0:window.stop_button.click()
            else:window.close()
            state['stage']+=1
        if state['stage'] in (1,3) and state['worker'] and not state['worker'].is_alive():
            results.append({'operation':'stop' if state['stage']==1 else 'close',
                'seconds':time.monotonic()-state['requested'],'worker_alive':False,
                'owned_job_closed':browser._owned_process_job is None,
                'owned_processes':browser.shutdown_diagnostic()['owned_processes']})
            if state['stage']==1:
                state['stage']=2;ready.clear()
                QTimer.singleShot(200,window.start_processing)
            else:
                app.quit()
    window.show()
    timer=QTimer();timer.timeout.connect(tick);timer.start(50)
    QTimer.singleShot(200,window.start_processing)
    app.exec();timer.stop()
    service.shutdown()
    passed=(len(results)==2 and all(r['seconds']<10 and r['owned_job_closed'] and r['owned_processes']==0 for r in results)
            and not errors and len([e for e in events if e.startswith('MODAL_DISMISSED')])==2)
    report.write_text(json.dumps({'passed':passed,'results':results,'events':events,'errors':errors,
        'live_google_modal':False,'fixture_only':True},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if passed else 9
