from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from djd_maker.adapters.browser import (
    BrowserAuthenticationRequired,
    BrowserManager,
    BrowserStartError,
    NOTEBOOK_HOME_URL,
)


class Process:
    next_pid = 4000

    def __init__(self) -> None:
        Process.next_pid += 1
        self.pid = Process.next_pid
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0

    def kill(self):
        self.alive = False


class Page:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.closed = False
        self.gotos: list[str] = []

    def is_closed(self):
        return self.closed

    def goto(self, url, **_kwargs):
        self.url = url
        self.gotos.append(url)


class Context:
    def __init__(self, pages=None) -> None:
        self._pages = list(pages or [Page()])
        self.timeout = None
        self.created: list[Page] = []
        self.stale = False

    @property
    def pages(self):
        if self.stale:
            raise RuntimeError("stale context")
        return self._pages

    def new_page(self):
        page = Page()
        self._pages.append(page)
        self.created.append(page)
        return page

    def set_default_timeout(self, timeout):
        self.timeout = timeout


class Browser:
    def __init__(self, context) -> None:
        self.contexts = [context]
        self.connected = True

    def is_connected(self):
        return self.connected


class Driver:
    def __init__(self, browsers) -> None:
        self.browsers = iter(browsers)
        self.endpoints: list[str] = []
        self.stopped = False
        self.chromium = SimpleNamespace(connect_over_cdp=self.connect)

    def connect(self, endpoint):
        self.endpoints.append(endpoint)
        return next(self.browsers)

    def stop(self):
        self.stopped = True


def harness(tmp_path: Path, pages=None, *, authenticated=True, browsers=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"chrome")
    launches: list[tuple[Path, Path, bool, str, Process]] = []

    def launcher(executable, profile, headless, url):
        process = Process()
        launches.append((executable, profile, headless, url, process))
        return process, "http://127.0.0.1:9222"

    context = Context(pages)
    browser_values = browsers or [Browser(context)]
    driver = Driver(browser_values)
    manager = BrowserManager(
        tmp_path / "専用 profile",
        playwright_factory=lambda: SimpleNamespace(start=lambda: driver),
        chrome_executable=chrome,
        chrome_launcher=launcher,
        authentication_probe=lambda _page: authenticated,
    )
    return manager, context, driver, launches


def test_login_browser_reuse(tmp_path: Path) -> None:
    manager, _context, driver, launches = harness(tmp_path)
    first = manager.open_login()
    second = manager.open_login()
    assert len(launches) == 1
    assert first["pid"] == second["pid"]
    assert driver.endpoints == ["http://127.0.0.1:9222"]


def test_login_then_start_same_browser(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(tmp_path)
    manager.open_login()
    process = launches[0][4]
    page = manager.prepare_for_processing()
    assert manager._process is process and manager.process_alive
    assert page.url == NOTEBOOK_HOME_URL


def test_no_close_required(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(tmp_path)
    manager.open_login()
    manager.prepare_for_processing()
    assert launches[0][4].poll() is None


def test_about_blank_same_browser_navigation(tmp_path: Path) -> None:
    blank = Page()
    manager, context, _driver, _launches = harness(tmp_path, [blank])
    manager.open_login()
    assert manager.prepare_for_processing() is blank
    assert blank.gotos == [NOTEBOOK_HOME_URL]
    assert context.created == []


def test_wrong_domain_new_managed_page_preserves_user_tab(tmp_path: Path) -> None:
    user_page = Page("https://example.com/private-tab")
    manager, context, _driver, _launches = harness(tmp_path, [user_page])
    manager.open_login()
    selected = manager.prepare_for_processing()
    assert selected is context.created[0]
    assert selected.url == NOTEBOOK_HOME_URL
    assert user_page.url == "https://example.com/private-tab"


def test_existing_gemini_page_reuse(tmp_path: Path) -> None:
    other = Page("https://example.com/")
    gemini = Page("https://notebook.google.com/notebook/existing")
    manager, context, _driver, _launches = harness(tmp_path, [other, gemini])
    manager.open_login()
    assert manager.prepare_for_processing() is gemini
    assert context.created == [] and gemini.gotos == []


def test_stale_page_refresh(tmp_path: Path) -> None:
    closed = Page("https://notebook.google.com/notebook/stale")
    closed.closed = True
    live = Page("https://notebook.google.com/notebook/live")
    manager, _context, _driver, _launches = harness(tmp_path, [closed, live])
    manager._managed_page = closed
    assert manager.prepare_for_processing() is live


def test_stale_context_recovery(tmp_path: Path) -> None:
    stale = Context()
    stale.stale = True
    fresh = Context([Page("https://notebook.google.com/notebook/fresh")])
    manager, _context, driver, launches = harness(
        tmp_path, browsers=[Browser(stale), Browser(fresh)]
    )
    manager.open_login()
    manager.context = stale
    manager.browser = Browser(stale)
    assert manager.prepare_for_processing().url.endswith("/fresh")
    assert launches and len(driver.endpoints) == 2


def test_browser_closed_fallback_uses_same_profile(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(tmp_path)
    manager.open_login()
    launches[0][4].alive = False
    manager.start()
    assert len(launches) == 2
    assert launches[0][1] == launches[1][1]


def test_expired_session_flow_keeps_chrome_for_login(tmp_path: Path) -> None:
    manager, _context, _driver, _launches = harness(tmp_path, authenticated=False)
    manager.open_login()
    with pytest.raises(BrowserAuthenticationRequired, match="Googleへのログイン"):
        manager.prepare_for_processing()
    assert manager.process_alive


def test_duplicate_browser_prevention(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(
        tmp_path, browsers=[Browser(Context()) for _ in range(8)]
    )
    threads = [threading.Thread(target=manager.open_login) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(launches) == 1


def test_profile_lock_regression_reports_start_failure(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(tmp_path)

    def locked(*_args):
        raise PermissionError("SingletonLock")

    manager._chrome_launcher = locked
    with pytest.raises(BrowserStartError, match="専用profile"):
        manager.open_login()
    assert launches == []


def test_repeated_start_same_browser_and_connection(tmp_path: Path) -> None:
    manager, _context, driver, launches = harness(tmp_path)
    first = manager.start()
    second = manager.start()
    assert first is second
    assert len(launches) == 1 and len(driver.endpoints) == 1


def test_japanese_space_profile_path(tmp_path: Path) -> None:
    manager, _context, _driver, launches = harness(tmp_path / "日本語 path")
    manager.open_login()
    assert "日本語 path" in str(launches[0][1])
