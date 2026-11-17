"""Opt-in frozen sort recovery, real Chromium and both Qt presentations."""
import os,json,time,traceback
from pathlib import Path


def run_v211_smoke(root,report):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from .acceptance_assets import initialize_fixture_assets
    from djd_maker.adapters import browser as bm
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook_modal import ensure_notebook_interactable,wait_for_human_modal
    from djd_maker.core.runtime_operation import operation_scope
    from djd_maker.testing.sort_fixture import install_sort_fixture,HOME,NOTEBOOK_URL
    from djd_maker.gui.app import build_desktop
    from djd_maker.core.models import Job
    from djd_maker.core.repositories import JobRepository
    from PySide6.QtWidgets import QMessageBox
    root,report=Path(root).resolve(),Path(report).resolve();root.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'checks':[]};events=[];errors=[];dialogs=[];window=service=None
    original=bm.NOTEBOOK_HOME_URL;bm.NOTEBOOK_HOME_URL='data:text/html,<title>Sort recovery fixture</title>'
    manager=BrowserManager(root/'fixture-profile',headless=True)
    old_critical=QMessageBox.critical;QMessageBox.critical=lambda *args,**kwargs:dialogs.append('critical')
    try:
        initialize_fixture_assets(root)
        for name in ('input','raw_files','work','output'):(root/name).mkdir(exist_ok=True)
        source=root/'input/fixture.txt';source.write_text('isolated sort fixture',encoding='utf8')
        repo=JobRepository(root/'system/jobs');job=Job(str(source));repo.save(job)
        app,window,service=build_desktop(root);app.setQuitOnLastWindowClosed(False)
        window.controller.operation_failed.connect(lambda *args:errors.append(str(args)))
        deadline=time.monotonic()+12
        while window.controller.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        assert not window.controller.busy
        before={p:p.read_bytes() for p in repo.directory.glob('*.json')}
        page=manager.start();page.context.route('https://notebook.google.com/**',lambda route:route.fulfill(body='<main></main>'))
        page.goto(HOME);install_sort_fixture(page)
        def sink(stage,fields):
            events.append((stage,dict(fields)));service._runtime_update({'stage':stage,**fields});app.processEvents()
        with operation_scope(sink):
            ensure_notebook_interactable(page,target=page.locator('#title'),owned_context=page.context)
        assert not any(fields.get('human_modal_wait') for _,fields in events)
        assert page.evaluate('window.escapes')==1 and page.evaluate('window.selectionChanges')==0
        assert not page.get_by_role('menu').count() and not page.locator('.cdk-overlay-backdrop').count()
        assert not window.human_modal_banner.isVisible()
        page.locator('#title').click();assert page.url==NOTEBOOK_URL
        result['checks'].append('scoped_sort_escape_backdrop_gone_title_click_no_human_wait')
        page.goto(HOME);install_sort_fixture(page)
        service._runtime_update({'stage':'WAITING_FOR_HUMAN_MODAL_DISMISSAL','human_modal_wait':True,'message':'stale fixture'})
        app.processEvents()
        with operation_scope(sink):
            wait_for_human_modal(page,target=page.locator('#title'),verify_ready=None,diagnostic=lambda _:None,
                owned_context=page.context,lifecycle_guard=None,poll_seconds=.01)
        assert any(stage=='STALE_HUMAN_WAIT_CLEARED' for stage,_ in events)
        assert not window.human_modal_banner.isVisible()
        result['checks'].append('stale_wait_cleared_without_replaying_job')
        install_sort_fixture(page,ignore_escape=True)
        def human(message):
            if message=='MODAL_STILL_VISIBLE':
                page.evaluate("document.querySelector('[role=menu]').remove();document.querySelector('.cdk-overlay-backdrop').remove()")
        with operation_scope(sink):
            ensure_notebook_interactable(page,target=page.locator('#title'),owned_context=page.context,diagnostic=human,poll_seconds=.01)
        assert page.evaluate('window.escapes')==2
        assert any(stage=='SORT_MENU_DISMISS_NEEDS_REVIEW' for stage,_ in events)
        assert not window.human_modal_banner.isVisible()
        result['checks'].append('bounded_failure_human_review_then_resume_without_job_failure')
        install_sort_fixture(page)
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=dialog id=unknown style=\"position:fixed;inset:0;z-index:30\">Unknown fixture<button onclick=\"window.wrongClicks++\">Close</button></div>')")
        def human_unknown(message):
            if message=='MODAL_STILL_VISIBLE':page.evaluate("document.querySelector('#unknown').remove()")
        with operation_scope(sink):
            ensure_notebook_interactable(page,target=page.locator('#title'),owned_context=page.context,diagnostic=human_unknown,poll_seconds=.01)
        assert page.evaluate('window.unsafeEscapes+window.wrongClicks')==0
        assert any(stage=='UNKNOWN_MODAL_DETECTED' for stage,_ in events)
        result['checks'].append('true_unknown_protected_before_sort_dismiss')
        install_sort_fixture(page,opened=False)
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<aside role=status>ページを復元しますか？ Chromeは正しく終了しませんでした。<button onclick=\"window.wrongClicks++\">復元</button><button onclick=\"window.wrongClicks++\">×</button></aside>')")
        with operation_scope(sink):ensure_notebook_interactable(page,target=page.locator('#title'))
        assert page.evaluate('window.wrongClicks+window.escapes')==0
        result['checks'].append('harmless_notice_surrogate_no_restore_close_or_restart')
        for phase in ('PHASE1','PHASE2'):
            assert window.switch_gui(phase);window.show();app.processEvents()
            assert not window.human_modal_banner.isVisible()
            window.grab().save(str(report.with_name(report.stem+'-'+phase+'.png')))
        assert all(p.read_bytes()==data for p,data in before.items())
        assert not errors and not dialogs
        result['checks'].append('dual_gui_no_false_human_banner_job_bytes_preserved')
        result.update(passed=True,errors=errors,dialogs=len(dialogs),job_writes_during_recovery=0)
    except Exception:result['error']=traceback.format_exc()
    finally:
        manager.stop();bm.NOTEBOOK_HOME_URL=original
        if window is not None:window.close()
        if service is not None:service.shutdown()
        QMessageBox.critical=old_critical
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
