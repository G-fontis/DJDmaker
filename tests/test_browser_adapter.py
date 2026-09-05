from pathlib import Path
from types import SimpleNamespace

from djd_maker.adapters.browser import BrowserManager, run_manual_login


class Context:
    def __init__(self):
        self.pages = [object()]
        self.timeout = None
        self.closed = False

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class Playwright:
    def __init__(self, context):
        self.context = context
        self.options = None
        self.profile = None
        self.stopped = False
        self.chromium = SimpleNamespace(launch_persistent_context=self.launch)

    def launch(self, profile, **options):
        self.profile = profile
        self.options = options
        return self.context

    def stop(self):
        self.stopped = True


def test_browser_manager_uses_dedicated_profile_without_reading_credentials(tmp_path: Path) -> None:
    context = Context()
    playwright = Playwright(context)
    manager = BrowserManager(
        tmp_path / "専用プロファイル",
        playwright_factory=lambda: SimpleNamespace(start=lambda: playwright),
        chrome_executable=tmp_path / "chrome.exe",
    )

    assert manager.start() is context.pages[0]
    assert Path(playwright.profile) == (tmp_path / "専用プロファイル").resolve()
    assert playwright.options["accept_downloads"] is True
    assert playwright.options["headless"] is False
    assert context.timeout == 30_000
    manager.stop()
    assert context.closed and playwright.stopped


def test_manual_login_uses_normal_chrome_without_automation_flags(monkeypatch, tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"exe")
    captured = {}

    class Process:
        def wait(self):
            return 0

    def popen(arguments, **options):
        captured["arguments"] = arguments
        captured["options"] = options
        return Process()

    monkeypatch.setattr("djd_maker.adapters.browser.subprocess.Popen", popen)
    profile = tmp_path / "専用 profile"
    assert run_manual_login(profile, executable=chrome) == 0
    arguments = captured["arguments"]
    assert f"--user-data-dir={profile.resolve()}" in arguments
    assert "--no-first-run" in arguments
    assert not any("remote-debugging" in value for value in arguments)
    assert not any("automation" in value.casefold() for value in arguments)
    assert captured["options"] == {"shell": False}
