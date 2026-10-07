"""Opt-in frozen GUI acceptance with an isolated, local Notebook DOM fixture."""
import json
import os
import shutil
import time
from pathlib import Path


def run_human_modal_smoke(root: Path, report: Path) -> int:
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from PySide6.QtCore import QTimer
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters import browser as browser_module
    from djd_maker.core.models import Job
    from djd_maker.gui.app import build_desktop
    from djd_maker.packaging.preflight import application_root

    root = root.resolve()
    if root.exists():
        raise ValueError('Fresh isolated acceptance directory required')
    root.mkdir(parents=True)
    (root / 'config').mkdir()
    shutil.copy2(application_root() / 'config/download-quality-profile.json',
                 root / 'config/download-quality-profile.json')
    manager = BrowserManager(root / 'browser/chrome-profile', headless=True)
    original_home = browser_module.NOTEBOOK_HOME_URL
    browser_module.NOTEBOOK_HOME_URL = 'data:text/html,<title>Local modal acceptance</title>'
    app, window, service = build_desktop(root, browser_manager=manager)
    app.setQuitOnLastWindowClosed(False)
    ending = root / 'ending-fixture.mp4'
    ending.write_bytes(b'GUI path fixture; never encoded')
    window.settings.ending_video = str(ending)
    window.settings_repository.save(window.settings)
    window.apply_settings(window.settings)
    preset = window.preset_repository.create('human modal fixture', 'never sent to Google')
    window.preset_repository.select(preset.id)
    job = Job(str(root / 'input/fixture.txt'))
    service.jobs.save(job)
    before = service.jobs.get(job.id).to_dict()
    events, errors, results = [], [], []
    state = {'case': 0, 'waiting': False, 'finished': False, 'start': time.monotonic()}
    cases = ('resume', 'stop', 'close')
    original_runtime = service._runtime_update

    def runtime(record):
        events.append(record.get('stage'))
        report.write_text(json.dumps({'in_progress': True, 'events': events[-10:],
            'state': state, 'errors': errors}, ensure_ascii=False), encoding='utf-8')
        original_runtime(record)

    service._runtime_update = runtime
    service._error_callback = lambda *args: errors.append(args)

    class Pipeline:
        scheduler = None
        wait_seconds = 0

        def run_cycle(self):
            page = manager.start()
            context = manager.context
            context.route('https://notebook.google.com/**', lambda route: route.fulfill(
                content_type='text/html', body='''<main><chat-panel><textarea></textarea></chat-panel></main>
                <div data-testid="modal-scrim" style="position:fixed;inset:0;background:#0008"></div>
                <div role="dialog" style="position:fixed;inset:20%;background:white">
                <h2>Unclassified fixture</h2><button id="human-close">Close</button></div>
                <script>window.modalClicks=0;document.querySelector('#human-close').onclick=()=>{
                window.modalClicks++;document.querySelector('[role=dialog]').remove();
                document.querySelector('[data-testid=modal-scrim]').remove();};</script>'''))
            page.goto('https://notebook.google.com/notebook/fixture')
            external = context.new_page()
            external.goto('data:text/html,<title>Preserved external fixture</title>')
            if cases[state['case']] == 'resume':
                # Fixture-only human-equivalent DOM removal. Product never calls this.
                page.evaluate('''()=>setTimeout(()=>{document.querySelector('[role=dialog]').remove();
                    document.querySelector('[data-testid=modal-scrim]').remove()},2500)''')
            manager.ensure_notebook_ready(page)
            assert page.evaluate('window.modalClicks') == 0
            assert not external.is_closed()
            assert service.jobs.get(job.id).to_dict() == before
            state['finished'] = True

        def all_tasks_completed(self):
            return state['finished']

        def final_discovery(self):
            return {'complete': state['finished'], 'runnable': False,
                    'attention_required': [], 'terminal_failed': []}

    service.pipeline_factory = Pipeline

    def tick():
        if time.monotonic() - state['start'] > 100:
            errors.append(('timeout', 'human modal smoke exceeded 100s'))
            service.stop()
            app.quit()
            return
        status = service.status()
        if status.get('active') and status.get('runtime', {}).get('human_modal_wait'):
            assert service.jobs.get(job.id).to_dict() == before
            if not window.human_modal_banner.isVisible():
                return  # Wait for the queued GUI status signal to be painted.
            state['waiting'] = True
            if cases[state['case']] == 'stop':
                window.stop_button.click()
            elif cases[state['case']] == 'close':
                window.close()
        if state['waiting'] and not service.status()['running']:
            case = cases[state['case']]
            results.append({'case': case, 'human_wait_seen': True,
                            'auto_resumed': state['finished'],
                            'checkpoint_preserved': service.jobs.get(job.id).to_dict() == before})
            if state['case'] == 2:
                app.quit()
            else:
                state['case'] += 1
                state['waiting'] = state['finished'] = False
                QTimer.singleShot(200, window.start_processing)

    window.show()
    timer = QTimer()
    def checked_tick():
        try:
            tick()
        except Exception as exc:
            errors.append(('GUI acceptance', str(exc)))
            service.stop()
            app.quit()
    timer.timeout.connect(checked_tick)
    timer.start(50)
    QTimer.singleShot(200, window.start_processing)
    app.exec()
    timer.stop()
    service.shutdown()
    browser_module.NOTEBOOK_HOME_URL = original_home
    passed = (len(results) == 3 and results[0]['auto_resumed']
              and all(r['checkpoint_preserved'] for r in results) and not errors
              and 'PIPELINE_RESUMED_AFTER_MODAL' in events)
    report.write_text(json.dumps({'passed': passed, 'fixture_only': True,
        'manual_equivalent_fixture': True, 'real_ad_modal': False, 'auto_dismiss': 0,
        'external_tab_close': 0, 'results': results, 'events': events, 'errors': errors},
        ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed else 9
