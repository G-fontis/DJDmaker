"""Opt-in real Chrome popup download and deferred-close acceptance on loopback."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def run_download_completion_smoke(root: Path, report: Path) -> int:
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from .preflight import application_root
    from .portable_e2e import _make_fixture
    from djd_maker.adapters import browser as browser_module
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook import PlaywrightArtifactDownload
    from djd_maker.adapters.notebook_modal import ensure_notebook_interactable
    from djd_maker.core.download_guard import DownloadLifecycleGuard, DeferredTaskType
    from djd_maker.media.validator import VideoValidator
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    tools = application_root() / 'runtime/ffmpeg'
    fixture = root / 'fixture.mp4'
    _make_fixture(tools / 'ffmpeg.exe', fixture, 'blue', 2)
    payload = fixture.read_bytes()
    guard = DownloadLifecycleGuard(root / 'queue.json')
    manager = BrowserManager(root / 'profile', headless=True, lifecycle_guard=guard)
    events, errors, transfer_observations = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/video.mp4':
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Disposition', 'attachment; filename="fixture.mp4"')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    transfer_observations.append(guard.active_download_count)
                    guard.defer(DeferredTaskType.NEXT_JOB, command_id='next', callback=lambda: events.append('next'))
                    guard.defer(DeferredTaskType.USER_STOP, command_id='stop', callback=lambda: events.append('stop'))
                    manager.stop()  # Must enqueue close; the owner releases it after validation.
                    assert manager.context is not None and guard.active_download_count == 1
                    time.sleep(.5)
                    for offset in range(0, len(payload), 1024):
                        self.wfile.write(payload[offset:offset+1024])
                        self.wfile.flush()
                        time.sleep(.02)
                except Exception as exc:
                    errors.append(str(exc))
            else:
                body='''<main><artifact-library-item><button aria-label="その他"
                    onclick="document.querySelector('#menu').hidden=false">その他</button></artifact-library-item>
                    <div id="menu" hidden><button role="menuitem" onclick="window.open('/video.mp4','_blank')">
                    ダウンロード</button></div></main>'''.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original_home = browser_module.NOTEBOOK_HOME_URL
    browser_module.NOTEBOOK_HOME_URL = 'data:text/html,<title>Download fixture</title>'
    result = {'passed': False, 'fixture_only': True, 'google_operations': 0}
    try:
        page = manager.start()
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        ensure_notebook_interactable(page, diagnostic=events.append)
        popups = []
        page.on('popup', lambda popup: popups.append(popup))
        destination = root / 'completed.mp4'
        downloaded = PlaywrightArtifactDownload(VideoValidator(tools / 'ffprobe.exe'),
            timeout_ms=30000, lifecycle_guard=guard)(page,
                page.locator('artifact-library-item'), destination, job_id='fixture')
        result.update(download_completion=downloaded.read_bytes() == payload,
            popup_count=len(popups), deferred_close=manager.context is None,
            transfer_observations=transfer_observations, deferred_events=events,
            errors=errors, active_download_count=guard.active_download_count)
        result['passed'] = (result['download_completion'] and len(popups) == 1 and result['deferred_close']
            and transfer_observations == [1] and events[:2] == ['stop', 'next']
            and not errors and guard.active_download_count == 0)
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        manager.stop()
        browser_module.NOTEBOOK_HOME_URL = original_home
        server.shutdown()
        server.server_close()
        worker.join(3)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 9
