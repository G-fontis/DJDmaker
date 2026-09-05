from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


class BrowserStartError(RuntimeError):
    pass


def find_chrome() -> Path | None:
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        return Path(found).resolve()
    candidates = (
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


class BrowserManager:
    """Visible persistent Chrome lifecycle ported from GNBCreator."""

    def __init__(
        self,
        user_data_dir: Path,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        playwright_factory: Callable[[], Any] | None = None,
        chrome_executable: Path | None = None,
    ) -> None:
        self.user_data_dir = user_data_dir.resolve()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._factory = playwright_factory
        self.chrome_executable = chrome_executable or find_chrome()
        self._playwright: Any = None
        self.context: Any = None

    def start(self) -> Any:
        if self.context is not None:
            return self.page
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            if self._factory is None:
                from playwright.sync_api import sync_playwright

                self._factory = sync_playwright
            self._playwright = self._factory().start()
            options: dict[str, Any] = {
                "headless": self.headless,
                "accept_downloads": True,
            }
            if self.chrome_executable:
                options["executable_path"] = str(self.chrome_executable)
            else:
                options["channel"] = "chrome"
            self.context = self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir), **options
            )
            self.context.set_default_timeout(self.timeout_ms)
            return self.page
        except Exception as exc:
            self.stop()
            raise BrowserStartError(
                "Google Chromeを起動できません。Chromeのインストールと、"
                "専用profileが別processで使用中でないことを確認してください。"
            ) from exc

    @property
    def page(self) -> Any:
        if self.context is None:
            raise BrowserStartError("browser is not started")
        return self.context.pages[0] if self.context.pages else self.context.new_page()

    def stop(self) -> None:
        context, playwright = self.context, self._playwright
        self.context = None
        self._playwright = None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


def run_manual_login(
    profile_dir: str | Path,
    url: str = "https://notebook.google.com/",
    *,
    executable: str | Path | None = None,
) -> int:
    """Run ordinary Chrome without Playwright during the initial Google login.

    This is the GNBCreator login handoff: Chrome owns the dedicated profile,
    and the application neither reads nor copies credentials or cookies.
    """

    chrome = Path(executable).resolve() if executable else find_chrome()
    if chrome is None or not chrome.is_file():
        raise BrowserStartError("Google Chromeの実行ファイルが見つかりません")
    profile = Path(profile_dir).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(chrome), f"--user-data-dir={profile}", "--no-first-run", url],
        shell=False,
    )
    return process.wait()
