"""Opt-in isolated Studio credit UI and dual GUI acceptance, source or frozen."""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def run_v210_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from datetime import datetime, timedelta
    from djd_maker.adapters import browser as browser_module
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter, RemoteVideoStatus
    from djd_maker.adapters.studio_generation import StudioGeneration
    from djd_maker.core.cloud_limit import CloudLimitGate, CloudLimitReached, CREDIT_LIMIT_MESSAGE
    from djd_maker.core.models import Job, Preset
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.runtime_operation import operation_scope
    from djd_maker.testing.studio_fixture import install_studio_fixture, settings_html, BODY
    from djd_maker.gui.app import build_desktop
    from PySide6.QtWidgets import QMessageBox
    from .acceptance_assets import initialize_fixture_assets
    root, report = Path(root).resolve(), Path(report).resolve()
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=False, execution='FROZEN_EXE' if getattr(sys,'frozen',False) else 'SOURCE',
        checks=[], live=False, generate_probe_clicks=0, deferred_clicks=0)
    old_home=browser_module.NOTEBOOK_HOME_URL
    browser_module.NOTEBOOK_HOME_URL='data:text/html,<title>Credit UI fixture</title>'
    manager=BrowserManager(root/'fixture-profile',headless=True)
    window=service=None
    old_critical=QMessageBox.critical;dialogs=[]
    QMessageBox.critical=lambda *args,**kwargs:dialogs.append(str(args[1:]))
    try:
        initialize_fixture_assets(root)
        for name in ('input','raw_files','output','work'):
            (root/name).mkdir(exist_ok=True)
        page=manager.start()
        page.context.route('https://notebook.google.com/**',lambda route:route.fulfill(body='<main></main>'))
        url='https://notebook.google.com/notebook/00000000-0000-4000-8000-000000000210'
        page.goto(url)
        source=root/'lesson.txt';source.write_text('isolated fixture',encoding='utf8')
        repo=JobRepository(root/'system/jobs')
        job=Job(str(source),notebook_id=url.rsplit('/',1)[1],notebook_url=url,source_status='READY')
        job.snapshot_preset(Preset('fixture','paper',BODY,'created','updated'));repo.save(job)
        dom=NotebookDomAdapter(page);dom.studio_settings_owned=True
        install_studio_fixture(page,settings_html(),opened=True,deferred=True)
        events=[]
        with operation_scope(lambda stage,fields:events.append(dict(stage=stage,**fields))):
            try:
                StudioGeneration(dom,job,repo.save).run()
            except CloudLimitReached as exc:
                assert exc.observation.message==CREDIT_LIMIT_MESSAGE
                observation=exc.observation
            else:raise AssertionError('credit limit not detected')
        assert page.evaluate('window.badClicks+window.generateClicks+window.chatSends')==0
        assert not page.get_by_role('dialog').count()
        assert not job.studio_generate_claimed_at and job.studio_generation_attempt==0
        assert any(e['stage']=='CREDIT_MODAL_CLOSED' and e['scrim_gone'] for e in events)
        result['checks'].append('deferred_is_non_error_no_click_no_claim_closed_and_interactable')
        clock=[datetime.now().astimezone()]
        path=root/'system/cloud-limit.json';gate=CloudLimitGate(path,clock=lambda:clock[0])
        gate.block(observation,notebook_url=url)
        clock[0]+=timedelta(seconds=599);assert not gate.due
        clock[0]+=timedelta(seconds=1)
        gate=CloudLimitGate(path,clock=lambda:clock[0]);assert gate.due
        result['checks'].append('600_second_boundary_and_restart')
        engine=NotebookEngineAdapter(dom,persist_identity=repo.save)
        engine._open_job=lambda _:None  # Isolated routed Notebook, never real navigation.
        install_studio_fixture(page,settings_html(),opened=True,deferred=True)
        try:engine.probe_generation_credit(job)
        except CloudLimitReached as exc:gate.block(exc.observation,notebook_url=url)
        else:raise AssertionError('still limited not detected')
        assert gate.recheck_delay==600
        assert page.evaluate('window.badClicks+window.generateClicks+window.chatSends')==0
        result['checks'].append('still_limited_closes_and_reschedules_without_click')
        install_studio_fixture(page,settings_html(),opened=True)
        snapshot=job.to_dict()
        assert engine.probe_generation_credit(job)
        assert job.to_dict()==snapshot
        assert page.evaluate('window.badClicks+window.generateClicks+window.chatSends')==0
        result['checks'].append('current_generate_recovery_probe_is_read_only')
        # Show the same backend state through both presentations.
        app,window,service=build_desktop(root);app.setQuitOnLastWindowClosed(False)
        deadline=time.monotonic()+12
        while window.controller.busy and time.monotonic()<deadline:
            app.processEvents();time.sleep(.02)
        assert not window.controller.busy
        for phase in ('PHASE1','PHASE2'):
            app.processEvents()
            assert window.switch_gui(phase)
            window._display_limit(gate.status());window.show();app.processEvents()
            assert '10分ごと' in window.limit_label.text()
            assert not window.human_modal_banner.isVisible()
            window.grab().save(str(report.with_name(report.stem+'-'+phase+'.png')))
        result['checks'].append('dual_gui_credit_message_not_human_review')
        gate.release();window._display_limit(gate.status())
        assert '利用可能' in window.limit_label.text()
        # Separate normal dispatch after read-only recovery, using original snapshot.
        install_studio_fixture(page,settings_html())
        assert StudioGeneration(dom,job,repo.save).run().remote_status is RemoteVideoStatus.GENERATING
        assert page.evaluate('window.generateClicks')==1
        assert page.evaluate('window.badClicks+window.chatSends')==0
        assert repo.require(job.id).generation_settings_snapshot==snapshot['generation_settings_snapshot']
        result['checks'].append('normal_dispatch_after_recovery_uses_original_snapshot_once')
        assert not dialogs
        result.update(passed=True,critical_dialogs=len(dialogs))
    except Exception:
        result['error']=traceback.format_exc()
        result['dialogs']=dialogs
    finally:
        if service is not None:service.shutdown()
        if window is not None:window.hide();window.deleteLater()
        manager.stop();browser_module.NOTEBOOK_HOME_URL=old_home;QMessageBox.critical=old_critical
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
