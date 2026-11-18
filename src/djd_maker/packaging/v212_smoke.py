"""Opt-in restart and real profile-lock recovery acceptance, on fixtures only."""
import json
import os
from pathlib import Path
import time
import traceback


def run_v212_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from datetime import datetime, timedelta, timezone
    from djd_maker.adapters import browser as bm
    from djd_maker.adapters.browser import BrowserManager, BrowserProfileLocked
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter
    from djd_maker.adapters.studio_generation import StudioGeneration
    from djd_maker.core.cloud_limit import CloudLimitGate, LimitObservation, CloudLimitReached
    from djd_maker.core.models import Job, Preset
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.runtime_operation import operation_scope
    from djd_maker.testing.studio_fixture import install_studio_fixture, settings_html, BODY
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.gui.app import build_desktop
    from PySide6.QtWidgets import QMessageBox
    from .acceptance_assets import initialize_fixture_assets
    root,report=Path(root).resolve(),Path(report).resolve();root.mkdir(parents=True,exist_ok=False)
    original=bm.NOTEBOOK_HOME_URL;bm.NOTEBOOK_HOME_URL='data:text/html,<title>Restart fixture</title>'
    owner=BrowserManager(root/'fixture-profile',headless=True)
    manager=BrowserManager(root/'fixture-profile',headless=True,lock_retry_delays=())
    result={'passed':False,'checks':[],'foreign_kill':0,'foreign_attach':0,'lock_deletions':0}
    window=service=None;dialogs=[];old_critical=QMessageBox.critical
    QMessageBox.critical=lambda *a,**kw:dialogs.append('critical')
    try:
        initialize_fixture_assets(root)
        for name in ('input','raw_files','output','work'):(root/name).mkdir(exist_ok=True)
        owner.start();owner_pid=owner._automation_process.pid
        clock=[datetime(2026,11,18,9,tzinfo=timezone(timedelta(hours=9)))]
        path=root/'system/cloud-limit.json';gate=CloudLimitGate(path,clock=lambda:clock[0])
        gate.block(LimitObservation('fixture limit',clock[0].replace(hour=12,minute=21)))
        clock[0]=clock[0].replace(hour=13)
        gate=CloudLimitGate(path,clock=lambda:clock[0]);assert gate.due and gate.blocked
        result['checks'].append('0900_limit_1100_stop_1300_restart_expired_due_not_available')
        source=root/'lesson.txt';source.write_text('isolated fixture',encoding='utf8')
        url='https://notebook.google.com/notebook/00000000-0000-4000-8000-000000000212'
        repo=JobRepository(root/'system/jobs')
        job=Job(str(source),notebook_id=url.rsplit('/',1)[1],notebook_url=url,source_status='READY')
        job.snapshot_preset(Preset('fixture','fixture',BODY,'created','updated'));repo.save(job)
        class Cloud:
            engine=None
            def probe_generation_credit(self,target):
                manager.start()  # Real current-owner guard; never attach owner.
                return self.engine.probe_generation_credit(target)
            def submit(self,target):
                return StudioGeneration(self.engine.dom,target,repo.save).run()
            def inspect_status(self,target):return 'GENERATING'
        remote=Cloud()
        pipe=PipelineCoordinator(jobs=repo,notebook=remote,raw_store=None,ending=None,hls=None,validator=None,
            paths=PipelinePaths(root/'raw_files',root/'output',root/'work',None),cloud_limit=gate,
            generation_preset=Preset('fixture','fixture',BODY,'created','updated'))
        events=[];pipe.runtime_callback=events.append
        pipe._refresh_capabilities()
        assert gate.status()['credit_state']=='CREDIT_RECHECK_BROWSER_BUSY' and gate.recheck_delay==15
        assert owner._automation_process.pid==owner_pid and owner._automation_process.poll() is None
        assert not pipe.capabilities.can_generate and pipe.capabilities.can_local_process
        assert repo.require(job.id).studio_generation_attempt==0
        for _ in range(3):pipe._refresh_capabilities()
        assert gate.state['recheck_attempt']==1
        result['checks'].append('real_foreign_profile_lock_nonfatal_backoff_no_attach_kill_duplicate_launch')
        app,window,service=build_desktop(root);app.setQuitOnLastWindowClosed(False)
        deadline=time.monotonic()+12
        while window.controller.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        for phase in ('PHASE1','PHASE2'):
            assert window.switch_gui(phase);window.show();app.processEvents();window._display_limit(gate.status())
            assert 'Chrome接続' in window.limit_label.text() and not window.human_modal_banner.isVisible()
            window.grab().save(str(report.with_name(report.stem+'-'+phase+'.png')))
        result['checks'].append('dual_gui_browser_busy_distinct_from_quota_human_wait_fatal')
        owner.stop();clock[0]+=timedelta(seconds=15)
        page=manager.start();pid=manager._automation_process.pid
        assert manager.start() is page and manager._automation_process.pid==pid
        page.context.route('https://notebook.google.com/**',lambda route:route.fulfill(body='<main></main>'))
        page.goto(url)
        dom=NotebookDomAdapter(page);dom.studio_settings_owned=True
        remote.engine=NotebookEngineAdapter(dom)
        remote.engine._open_job=lambda _:None
        install_studio_fixture(page,settings_html(),opened=True,deferred=True)
        pipe._refresh_capabilities()
        assert gate.status()['credit_state']=='CREDIT_LIMIT_ACTIVE' and gate.recheck_delay==600
        assert page.evaluate('window.generateClicks+window.badClicks+window.chatSends')==0
        result['checks'].append('owner_release_reconnect_current_ui_still_limited_600_seconds')
        clock[0]+=timedelta(seconds=600)
        install_studio_fixture(page,settings_html(),opened=True)
        pipe._refresh_capabilities()
        assert not gate.blocked and pipe.capabilities.can_generate and pipe._generation_preempts()
        assert page.evaluate('window.generateClicks+window.chatSends')==0
        assert repo.require(job.id).studio_generation_attempt==0
        result['checks'].append('current_generate_available_releases_gate_priority_one_without_probe_generation')
        install_studio_fixture(page,settings_html())
        pipe.run_cycle()
        assert page.evaluate('window.generateClicks')==1 and repo.require(job.id).studio_generation_attempt==1
        pipe.run_cycle();assert page.evaluate('window.generateClicks')==1
        result['checks'].append('normal_scheduler_dispatch_once_after_recovery_no_source_chat_duplicate')
        stages={e.get('stage') for e in events}
        assert {'CREDIT_DEADLINE_EXPIRED','CREDIT_RECHECK_DUE','BROWSER_PROFILE_LOCK_DETECTED',
                'CREDIT_RECHECK_RETRIED','CREDIT_STILL_LIMITED','CREDIT_RECOVERED','GENERATION_RESUMED'}<=stages
        assert not dialogs
        result.update(passed=True,critical_dialogs=0,events=[e.get('stage') for e in events])
    except Exception:result['error']=traceback.format_exc()
    finally:
        if service is not None:service.shutdown()
        if window is not None:window.hide();window.deleteLater()
        manager.stop();owner.stop();bm.NOTEBOOK_HOME_URL=original;QMessageBox.critical=old_critical
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
