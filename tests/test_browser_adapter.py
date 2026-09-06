from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from djd_maker.adapters.browser import (
    AuthChromeStillRunning,
    BrowserAuthenticationRequired,
    BrowserConnectionError,
    BrowserManager,
    BrowserNavigationError,
    BrowserProfileLocked,
    NOTEBOOK_HOME_URL,
    PREFLIGHT_CHECKS,
)


class Process:
    next_pid = 5000

    def __init__(self, *, blocking: bool = False) -> None:
        Process.next_pid += 1
        self.pid = Process.next_pid
        self.alive = True
        self.blocking = blocking
        self.closed = threading.Event()

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        if self.blocking:
            self.closed.wait(timeout)
        self.alive = False
        return 0

    def terminate(self):
        self.alive = False
        self.closed.set()

    kill = terminate


class Page:
    def __init__(self, url: str = "about:blank", *, goto_error: bool = False) -> None:
        self.url = url
        self.closed = False
        self.goto_error = goto_error
        self.gotos: list[str] = []

    def is_closed(self):
        return self.closed

    def goto(self, url, **_kwargs):
        if self.goto_error:
            raise RuntimeError("navigation failed")
        self.url = url
        self.gotos.append(url)


class Context:
    def __init__(self, pages=None) -> None:
        self._pages = list(pages or [Page()])
        self.created: list[Page] = []
        self.timeout = None
        self.stale = False
        self.browser = None

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

    def close(self):
        self.stale = True


class Driver:
    def __init__(self, values) -> None:
        self.values = iter(values)
        self.launches: list[tuple[str, dict[str, object]]] = []
        self.chromium = SimpleNamespace(launch_persistent_context=self.launch)

    def launch(self, profile, **kwargs):
        self.launches.append((profile, kwargs))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value

    def stop(self):
        pass


def harness(
    tmp_path: Path,
    *,
    contexts=None,
    authenticated=True,
    home=True,
    selectors=True,
    auth_process: Process | None = None,
    lock_probe=None,
    lock_delays=(),
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"chrome")
    contexts = list(contexts or [Context()])
    driver = Driver(contexts)
    auth_launches: list[tuple[Path, Path, str, Process]] = []

    def auth_launcher(executable, profile, url):
        process = auth_process or Process()
        auth_launches.append((executable, profile, url, process))
        return process

    manager = BrowserManager(
        tmp_path / "専用 profile",
        playwright_factory=lambda: SimpleNamespace(start=lambda: driver),
        chrome_executable=chrome,
        auth_chrome_launcher=auth_launcher,
        authentication_probe=lambda _page: authenticated,
        home_dom_probe=lambda _page: home,
        selector_probe=lambda _page: selectors,
        profile_lock_probe=lock_probe or (lambda _profile: True),
        lock_retry_delays=lock_delays,
    )
    return manager, driver, auth_launches


def test_auth_chrome_is_ordinary_and_waits_for_close(tmp_path: Path) -> None:
    manager, driver, auth_launches = harness(tmp_path)
    result = manager.open_login()
    assert len(auth_launches) == 1 and driver.launches == []
    command = manager._auth_command
    assert command == (
        str(manager.chrome_executable),
        f"--user-data-dir={manager.user_data_dir}",
        "--no-first-run",
        NOTEBOOK_HOME_URL,
    )
    forbidden = " ".join(command).casefold()
    assert "remote-debugging" not in forbidden
    assert "cdp" not in forbidden and "headless" not in forbidden
    assert "automation" not in forbidden and "remote-allow-origins" not in forbidden
    assert result["auth_process_alive"] is False


def test_auth_chrome_still_running_blocks_without_automation(tmp_path: Path) -> None:
    process = Process(blocking=True)
    manager, driver, _auth_launches = harness(tmp_path, auth_process=process)
    thread = threading.Thread(target=manager.open_login)
    thread.start()
    assert process.alive
    with pytest.raises(AuthChromeStillRunning, match="閉じて"):
        manager.prepare_for_processing()
    assert driver.launches == []
    assert manager.runtime_status()["preflight_checks"]["auth_chrome_closed"] == "FAIL"
    process.terminate()
    thread.join(timeout=1)


def test_login_close_then_automation_uses_same_profile_separate_phase(tmp_path: Path) -> None:
    manager, driver, auth_launches = harness(tmp_path)
    manager.open_login()
    page = manager.prepare_for_processing()
    assert auth_launches[0][1] == Path(driver.launches[0][0])
    assert page.url == NOTEBOOK_HOME_URL
    assert driver.launches[0][1]["accept_downloads"] is True


def test_preflight_all_seven_checks_pass_before_return(tmp_path: Path) -> None:
    manager, _driver, _auth = harness(tmp_path)
    manager.prepare_for_processing()
    status = manager.runtime_status()
    assert status["preflight_result"] == "PRE_FLIGHT_READY"
    assert status["preflight_checks"] == {name: "PASS" for name in PREFLIGHT_CHECKS}


def test_expired_session_stops_automation_and_creates_nothing(tmp_path: Path) -> None:
    context = Context()
    manager, _driver, _auth = harness(tmp_path, contexts=[context], authenticated=False)
    with pytest.raises(BrowserAuthenticationRequired, match="Googleへのログイン"):
        manager.prepare_for_processing()
    status = manager.runtime_status()
    assert status["preflight_checks"]["google_authenticated"] == "FAIL"
    assert status["automation_connected"] is False
    assert context.created == []


def test_profile_lock_delayed_release_is_bounded_and_then_passes(tmp_path: Path) -> None:
    values = iter((False, False, True))
    manager, driver, _auth = harness(
        tmp_path, lock_probe=lambda _path: next(values), lock_delays=(0, 0)
    )
    manager.prepare_for_processing()
    assert len(driver.launches) == 1


def test_profile_lock_exhaustion_starts_no_automation(tmp_path: Path) -> None:
    manager, driver, _auth = harness(
        tmp_path, lock_probe=lambda _path: False, lock_delays=(0, 0)
    )
    with pytest.raises(BrowserProfileLocked):
        manager.prepare_for_processing()
    assert driver.launches == []
    assert manager.runtime_status()["preflight_checks"]["profile_unlocked"] == "FAIL"


def test_about_blank_is_navigated_and_never_ready_as_blank(tmp_path: Path) -> None:
    blank = Page()
    context = Context([blank])
    manager, _driver, _auth = harness(tmp_path, contexts=[context])
    assert manager.prepare_for_processing() is blank
    assert blank.gotos == [NOTEBOOK_HOME_URL]


def test_wrong_domain_is_preserved_and_new_page_is_managed(tmp_path: Path) -> None:
    other = Page("https://example.com/private")
    context = Context([other])
    manager, _driver, _auth = harness(tmp_path, contexts=[context])
    selected = manager.prepare_for_processing()
    assert selected is context.created[0]
    assert other.url == "https://example.com/private"


def test_stale_page_is_replaced(tmp_path: Path) -> None:
    stale = Page("https://notebook.google.com/notebook/stale")
    stale.closed = True
    live = Page()
    manager, _driver, _auth = harness(tmp_path, contexts=[Context([stale, live])])
    manager._managed_page = stale
    assert manager.prepare_for_processing() is live


def test_automation_launch_transient_profile_lock_retries(tmp_path: Path) -> None:
    context = Context()
    manager, driver, _auth = harness(
        tmp_path,
        contexts=[RuntimeError("SingletonLock profile in use"), context],
        lock_delays=(0,),
    )
    manager.prepare_for_processing()
    assert len(driver.launches) == 2


def test_automation_non_lock_crash_fails_closed(tmp_path: Path) -> None:
    manager, _driver, _auth = harness(
        tmp_path, contexts=[RuntimeError("browser executable corrupt")]
    )
    with pytest.raises(BrowserConnectionError):
        manager.prepare_for_processing()
    assert manager.runtime_status()["preflight_checks"]["automation_chrome_started"] == "FAIL"


def test_navigation_failure_fails_before_auth_and_selectors(tmp_path: Path) -> None:
    manager, _driver, _auth = harness(
        tmp_path, contexts=[Context([Page(goto_error=True)])]
    )
    with pytest.raises(BrowserNavigationError):
        manager.prepare_for_processing()
    checks = manager.runtime_status()["preflight_checks"]
    assert checks["gemini_connected"] == "FAIL"
    assert checks["google_authenticated"] == "PENDING"


@pytest.mark.parametrize(
    ("home", "selectors", "failed"),
    ((False, True, "notebook_home_dom"), (True, False, "required_selectors")),
)
def test_dom_gate_failure_is_side_effect_free(
    tmp_path: Path, home: bool, selectors: bool, failed: str
) -> None:
    context = Context()
    manager, _driver, _auth = harness(
        tmp_path, contexts=[context], home=home, selectors=selectors
    )
    with pytest.raises(BrowserNavigationError):
        manager.prepare_for_processing()
    assert context.created == []
    assert manager.runtime_status()["preflight_checks"][failed] == "FAIL"


def test_restart_after_context_crash_reuses_profile_and_last_url(tmp_path: Path) -> None:
    first = Context([Page("https://notebook.google.com/notebook/lesson")])
    second = Context([Page()])
    manager, driver, _auth = harness(tmp_path, contexts=[first, second])
    manager.prepare_for_processing()
    manager._last_managed_url = "https://notebook.google.com/notebook/lesson"
    first.stale = True
    recovered = manager.restart()
    assert len(driver.launches) == 2
    assert driver.launches[0][0] == driver.launches[1][0]
    assert recovered.url.endswith("/notebook/lesson")


def test_warm_app_restart_needs_no_auth_launch(tmp_path: Path) -> None:
    profile_root = tmp_path / "portable" / "browser" / "chrome-profile"
    first, _driver, first_auth = harness(profile_root.parent)
    first.user_data_dir = profile_root
    first.open_login()
    first.stop()
    second, _driver2, second_auth = harness(profile_root.parent)
    second.user_data_dir = profile_root
    second.prepare_for_processing()
    assert len(first_auth) == 1 and second_auth == []


@pytest.mark.parametrize(
    "relative",
    (
        Path("日本語 path"),
        Path("Dropbox 相当") / "深い" / "folder with spaces" / "profile",
    ),
)
def test_unicode_space_and_deep_profile_path(tmp_path: Path, relative: Path) -> None:
    manager, driver, auth = harness(tmp_path / relative)
    manager.open_login()
    manager.prepare_for_processing()
    assert auth[0][1] == Path(driver.launches[0][0])


def test_repeated_login_close_start_has_no_duplicate_phase(tmp_path: Path) -> None:
    manager, driver, auth = harness(tmp_path, contexts=[Context(), Context()])
    for _ in range(2):
        manager.open_login()
        manager.prepare_for_processing()
        manager._stop_automation()
    assert len(auth) == 2
    assert len(driver.launches) == 2
