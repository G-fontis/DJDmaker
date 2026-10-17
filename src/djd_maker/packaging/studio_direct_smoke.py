"""Opt-in frozen primary Studio acceptance; isolated synthetic Notebook DOM."""
import json
import os
import sys
import traceback
from pathlib import Path


def run_studio_direct_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from djd_maker.adapters import browser as browser_module
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter, RemoteVideoStatus
    from djd_maker.adapters.studio_generation import StudioGeneration, StudioOutcomePending
    from djd_maker.core.models import Job, Preset
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.cloud_limit import CloudLimitReached
    from djd_maker.adapters.usage_limit import parse_limit_text
    from djd_maker.testing.studio_fixture import install_studio_fixture, settings_html, BODY
    from datetime import datetime

    root, report = Path(root).resolve(), Path(report).resolve()
    root.mkdir(parents=True, exist_ok=False)
    data = dict(passed=False, execution='FROZEN_EXE' if getattr(sys, 'frozen', False) else 'SOURCE',
                mode='SYNTHETIC_OBSERVED_STUDIO_DOM', checks=[])
    original = browser_module.NOTEBOOK_HOME_URL
    browser_module.NOTEBOOK_HOME_URL = 'data:text/html,<title>Isolated Studio acceptance</title>'
    manager = BrowserManager(root/'fixture-profile', headless=True)
    try:
        page = manager.start()
        page.context.route('https://notebook.google.com/**', lambda route: route.fulfill(body='<main></main>'))
        url = 'https://notebook.google.com/notebook/00000000-0000-4000-8000-000000000125'
        page.goto(url)
        html = settings_html()
        source = root/'lesson.txt'
        source.write_text('isolated acceptance source', encoding='utf8')
        repo = JobRepository(root/'jobs')

        def fresh(opened=False):
            install_studio_fixture(page, html, opened=opened)
            job = Job(str(source), notebook_id=url.rsplit('/', 1)[1], notebook_url=url)
            job.snapshot_preset(Preset('fixture', 'paper', BODY, 'created', 'updated'))
            repo.save(job)
            dom = NotebookDomAdapter(page)
            dom.studio_settings_owned = True
            return job, dom, StudioGeneration(dom, job, repo.save, timeout_seconds=.5, poll_seconds=.02)

        for opened in (False, True):
            job, dom, flow = fresh(opened)
            assert flow.run().remote_status is RemoteVideoStatus.GENERATING
            assert page.evaluate('window.generateClicks') == 1
            assert repo.require(job.id).studio_generation_stage == 'GENERATION_STARTED'
            assert flow.run().remote_status is RemoteVideoStatus.GENERATING
            assert page.evaluate('window.generateClicks') == 1
            assert page.evaluate('window.badClicks+window.chatSends') == 0
            data['checks'].append('settings_open_restart' if opened else 'scoped_settings_generate_remote_start')

        # A real persisted job is reloaded; remote READY must bypass Generate.
        page.locator('artifact-library').evaluate("e=>e.innerHTML='<artifact-library-item><button aria-label=再生>再生</button></artifact-library-item>'")
        assert StudioGeneration(dom, repo.require(job.id), repo.save).run().remote_status is RemoteVideoStatus.READY
        assert page.evaluate('window.generateClicks') == 1
        data['checks'].append('restart_ready_no_duplicate')

        job, dom, flow = fresh()
        page.locator('artifact-library').evaluate("e=>e.innerHTML='<artifact-library-item>動画解説を生成できませんでした。</artifact-library-item>'")
        engine = NotebookEngineAdapter(dom, persist_identity=repo.save)
        assert engine.retry_failed_generation(job).remote_status == 'GENERATING'
        assert page.locator('artifact-library-item').count() == 2
        assert page.evaluate('window.generateClicks') == 1
        assert page.evaluate('window.badClicks+window.chatSends') == 0
        data['checks'].append('failed_retry_direct_snapshot_retained_artifact')

        job, dom, flow = fresh(True)
        page.evaluate('''()=>{document.body.insertAdjacentHTML('beforeend',
          '<div role=dialog id=unknown>Human review<button onclick="window.badClicks++">Close</button></div>');
          setTimeout(()=>document.querySelector('#unknown').remove(),150)}''')
        assert flow.run().remote_status is RemoteVideoStatus.GENERATING
        assert page.evaluate('window.badClicks') == 0
        data['checks'].append('unknown_modal_human_equivalent_dismiss_resume')

        job, dom, flow = fresh()
        page.evaluate('window.suppressGeneration=true')
        for _ in range(2):
            try:
                flow.run()
            except StudioOutcomePending:
                pass
            else:
                raise AssertionError('Unknown outcome was accepted')
        assert page.evaluate('window.generateClicks') == 1
        data['checks'].append('unknown_claim_no_reclick')

        job, dom, flow = fresh()
        original_check = dom.check_usage_limit
        observation = parse_limit_text('チャットは 23:59 まで無効', now=datetime.now().astimezone())
        def blocked():
            raise CloudLimitReached(observation)
        dom.check_usage_limit = blocked
        try:
            flow.run()
        except CloudLimitReached:
            pass
        else:
            raise AssertionError('Actual limit did not block')
        assert page.evaluate('window.generateClicks') == 0
        dom.check_usage_limit = original_check
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=alert>You are near your AI usage limit</div>')")
        assert flow.run().remote_status is RemoteVideoStatus.GENERATING
        data['checks'].append('actual_limit_recovery_near_warning_not_blocked')
        data.update(passed=True, chat_sends=0, wrong_clicks=0, studio_retry_clicks=0, duplicate_generations=0)
    except Exception:
        data['error'] = traceback.format_exc()
    finally:
        manager.stop()
        browser_module.NOTEBOOK_HOME_URL = original
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if data['passed'] else 9
