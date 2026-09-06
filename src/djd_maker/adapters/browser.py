from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

NOTEBOOK_HOME_URL = "https://notebook.google.com/"
PREFLIGHT_CHECKS = (
    "auth_chrome_closed",
    "profile_unlocked",
    "automation_chrome_started",
    "gemini_connected",
    "google_authenticated",
    "notebook_home_dom",
    "required_selectors",
)


class BrowserStartError(RuntimeError):
    pass


class BrowserConnectionError(BrowserStartError):
    pass


class BrowserNavigationError(BrowserStartError):
    pass


class BrowserAuthenticationRequired(BrowserStartError):
    pass


class AuthChromeStillRunning(BrowserStartError):
    preserve_browser = True


class BrowserProfileLocked(BrowserStartError):
    pass


def find_chrome() -> Path | None:
    import shutil

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
    """Own the ordinary AUTH phase and separate automated browser phase."""

    def __init__(
        self,
        user_data_dir: Path,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        playwright_factory: Callable[[], Any] | None = None,
        chrome_executable: Path | None = None,
        auth_chrome_launcher: Callable[[Path, Path, str], Any] | None = None,
        authentication_probe: Callable[[Any], bool] | None = None,
        home_dom_probe: Callable[[Any], bool] | None = None,
        selector_probe: Callable[[Any], bool] | None = None,
        profile_lock_probe: Callable[[Path], bool] | None = None,
        lock_retry_delays: tuple[float, ...] = (0.1, 0.2, 0.4, 0.8),
    ) -> None:
        self.user_data_dir = user_data_dir.resolve()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._factory = playwright_factory
        self.chrome_executable = chrome_executable or find_chrome()
        self._auth_chrome_launcher = auth_chrome_launcher or self._launch_auth_chrome
        self._authentication_probe = authentication_probe
        self._home_dom_probe = home_dom_probe
        self._selector_probe = selector_probe
        self._profile_lock_probe = profile_lock_probe or self._default_profile_lock_probe
        self.lock_retry_delays = lock_retry_delays
        self._guard = threading.RLock()
        self._auth_process: Any = None
        self._playwright: Any = None
        self.context: Any = None
        self.browser: Any = None
        self._managed_page: Any = None
        self._last_managed_url = NOTEBOOK_HOME_URL
        self._navigation_result = "not-attempted"
        self._authentication_result = "not-checked"
        self._preflight_result = "NOT_RUN"
        self._preflight_checks = {name: "PENDING" for name in PREFLIGHT_CHECKS}
        self._auth_command: tuple[str, ...] = ()

    @property
    def auth_process_alive(self) -> bool:
        return self._auth_process is not None and self._auth_process.poll() is None

    @property
    def process_alive(self) -> bool:
        return self._context_alive(self.context)

    def open_login(self, url: str = NOTEBOOK_HOME_URL) -> dict[str, object]:
        """Run normal Chrome for human sign-in and wait until it is closed."""
        with self._guard:
            self._stop_automation()
            if self.chrome_executable is None or not self.chrome_executable.is_file():
                raise BrowserStartError("Google Chromeの実行ファイルが見つかりません")
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            if self.auth_process_alive:
                process = self._auth_process
            else:
                self._auth_command = (
                    str(self.chrome_executable),
                    f"--user-data-dir={self.user_data_dir}",
                    "--no-first-run",
                    url,
                )
                try:
                    process = self._auth_chrome_launcher(
                        self.chrome_executable, self.user_data_dir, url
                    )
                except Exception as exc:
                    raise BrowserStartError("Googleログイン用Chromeを起動できません") from exc
                self._auth_process = process
                if process.poll() is not None:
                    self._auth_process = None
                    raise BrowserStartError("Googleログイン用Chromeが起動直後に終了しました")
            self._navigation_result = "auth-chrome-opened"
        process.wait()
        with self._guard:
            if self._auth_process is process:
                self._auth_process = None
            self._navigation_result = "auth-chrome-closed"
            return self.runtime_status()

    @staticmethod
    def _launch_auth_chrome(executable: Path, profile: Path, url: str) -> Any:
        # Ordinary Chrome only: no debugging/CDP/Playwright/headless/automation flags.
        return subprocess.Popen(
            [str(executable), f"--user-data-dir={profile}", "--no-first-run", url],
            shell=False,
        )

    def start(self) -> Any:
        """Start automation without the release pre-flight (packaging smoke only)."""
        with self._guard:
            if self.auth_process_alive:
                raise AuthChromeStillRunning("Googleログイン用Chromeを閉じてから開始してください")
            self._wait_for_profile_unlock()
            self._ensure_automation_context()
            return self.ensure_page_alive()

    def prepare_for_processing(self) -> Any:
        """Run all seven side-effect-free checks before pipeline construction."""
        with self._guard:
            self._preflight_result = "RUNNING"
            self._preflight_checks = {name: "PENDING" for name in PREFLIGHT_CHECKS}
            if self.auth_process_alive:
                self._fail_check("auth_chrome_closed")
                raise AuthChromeStillRunning("Googleログイン用Chromeを閉じてから開始してください")
            self._pass_check("auth_chrome_closed")
            try:
                self._wait_for_profile_unlock()
            except Exception:
                self._fail_check("profile_unlocked")
                raise
            self._pass_check("profile_unlocked")
            try:
                self._ensure_automation_context()
            except Exception:
                self._fail_check("automation_chrome_started")
                raise
            self._pass_check("automation_chrome_started")
            try:
                page = self.ensure_gemini_page(force_home=True)
            except Exception:
                self._fail_check("gemini_connected")
                self._stop_automation()
                raise
            self._pass_check("gemini_connected")
            if not self.ensure_authenticated(page):
                self._fail_check("google_authenticated")
                self._stop_automation()
                raise BrowserAuthenticationRequired(
                    "Googleへのログインを確認できません。［Googleログイン］からログインし、"
                    "Chromeを閉じてからもう一度開始してください。"
                )
            self._pass_check("google_authenticated")
            if not self.ensure_home_dom(page):
                self._fail_check("notebook_home_dom")
                self._stop_automation()
                raise BrowserNavigationError("Gemini Notebookホーム画面を確認できません")
            self._pass_check("notebook_home_dom")
            if not self.ensure_required_selectors(page):
                self._fail_check("required_selectors")
                self._stop_automation()
                raise BrowserNavigationError("Notebook作成に必要なselectorを確認できません")
            self._pass_check("required_selectors")
            self._preflight_result = "PRE_FLIGHT_READY"
            return page

    def _pass_check(self, name: str) -> None:
        self._preflight_checks[name] = "PASS"

    def _fail_check(self, name: str) -> None:
        self._preflight_checks[name] = "FAIL"
        self._preflight_result = "FAILED"

    def _default_profile_lock_probe(self, profile: Path) -> bool:
        if self.auth_process_alive:
            return False
        for name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile / name).unlink(missing_ok=True)
            except (PermissionError, OSError):
                return False
        try:
            (profile / "DevToolsActivePort").unlink(missing_ok=True)
        except (PermissionError, OSError):
            return False
        return True

    def _wait_for_profile_unlock(self) -> None:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        attempts = (0.0, *self.lock_retry_delays)
        for index, delay in enumerate(attempts):
            if delay:
                time.sleep(delay)
            if self._profile_lock_probe(self.user_data_dir):
                return
            if index == len(attempts) - 1:
                break
        raise BrowserProfileLocked(
            "専用profileの解放を確認できません。Googleログイン用Chromeを閉じて再試行してください"
        )

    @staticmethod
    def _profile_lock_failure(exc: Exception) -> bool:
        text = str(exc).casefold()
        return any(
            marker in text
            for marker in (
                "profile", "singleton", "lockfile", "sharing violation",
                "winerror 32", "process singleton",
            )
        )

    def _ensure_automation_context(self) -> None:
        if self._context_alive(self.context):
            return
        self._stop_automation()
        if self.chrome_executable is None or not self.chrome_executable.is_file():
            raise BrowserStartError("Google Chromeの実行ファイルが見つかりません")
        if self._factory is None:
            from playwright.sync_api import sync_playwright

            self._factory = sync_playwright
        last_error: Exception | None = None
        for index in range(len(self.lock_retry_delays) + 1):
            try:
                self._playwright = self._factory().start()
                self.context = self._playwright.chromium.launch_persistent_context(
                    str(self.user_data_dir),
                    executable_path=str(self.chrome_executable),
                    headless=self.headless,
                    accept_downloads=True,
                )
                self.context.set_default_timeout(self.timeout_ms)
                self.browser = getattr(self.context, "browser", None)
                return
            except Exception as exc:
                last_error = exc
                self._stop_automation()
                if not self._profile_lock_failure(exc) or index >= len(self.lock_retry_delays):
                    break
                time.sleep(self.lock_retry_delays[index])
        if last_error is not None and self._profile_lock_failure(last_error):
            raise BrowserProfileLocked("専用profileが別のChromeで使用中です") from last_error
        raise BrowserConnectionError("automation Chromeの起動に失敗しました") from last_error

    @staticmethod
    def _context_alive(context: Any) -> bool:
        if context is None:
            return False
        try:
            list(context.pages)
            return True
        except Exception:
            return False

    @staticmethod
    def _page_alive(page: Any) -> bool:
        if page is None:
            return False
        try:
            return not page.is_closed() and isinstance(page.url, str)
        except Exception:
            return False

    def ensure_context_alive(self) -> Any:
        if not self._context_alive(self.context):
            self._ensure_automation_context()
        return self.context

    def ensure_page_alive(self) -> Any:
        context = self.ensure_context_alive()
        if self._page_alive(self._managed_page):
            return self._managed_page
        self._managed_page = None
        pages = [page for page in list(context.pages) if self._page_alive(page)]
        gemini = next((page for page in pages if self._is_gemini_url(page.url)), None)
        if gemini is not None:
            self._managed_page = gemini
        else:
            blank = next((page for page in pages if page.url == "about:blank"), None)
            self._managed_page = blank or context.new_page()
        return self._managed_page

    @staticmethod
    def _is_gemini_url(url: str) -> bool:
        try:
            return urlsplit(url).hostname == "notebook.google.com"
        except Exception:
            return False

    def ensure_gemini_page(self, *, force_home: bool = False) -> Any:
        context = self.ensure_context_alive()
        pages = [page for page in list(context.pages) if self._page_alive(page)]
        existing = next((page for page in pages if self._is_gemini_url(page.url)), None)
        if existing is not None:
            page = existing
        else:
            page = self.ensure_page_alive()
            if page.url != "about:blank" and not self._is_gemini_url(page.url):
                page = context.new_page()
        self._managed_page = page
        if force_home or not self._is_gemini_url(page.url):
            try:
                page.goto(NOTEBOOK_HOME_URL, wait_until="domcontentloaded")
            except Exception as exc:
                self._navigation_result = "failed"
                raise BrowserNavigationError("Gemini Notebookへの移動に失敗しました") from exc
            self._navigation_result = "navigated-home"
        else:
            self._navigation_result = "existing-gemini-page"
        self._last_managed_url = self._safe_url(page)
        return page

    def ensure_authenticated(self, page: Any | None = None) -> bool:
        page = page or self.ensure_gemini_page(force_home=True)
        authenticated = (
            bool(self._authentication_probe(page))
            if self._authentication_probe is not None
            else self._default_authentication_probe(page)
        )
        self._authentication_result = "authenticated" if authenticated else "login-required"
        return authenticated

    @staticmethod
    def _default_authentication_probe(page: Any) -> bool:
        try:
            return urlsplit(page.url).hostname == "notebook.google.com"
        except Exception:
            return False

    def ensure_home_dom(self, page: Any) -> bool:
        if self._home_dom_probe is not None:
            return bool(self._home_dom_probe(page))
        try:
            if urlsplit(page.url).hostname != "notebook.google.com":
                return False
            body = page.locator("body")
            return bool(body.count()) and body.first.is_visible(timeout=self.timeout_ms)
        except Exception:
            return False

    def ensure_required_selectors(self, page: Any) -> bool:
        if self._selector_probe is not None:
            return bool(self._selector_probe(page))
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            for name in ("新規作成", "ノートブックを新規作成", "Create new notebook"):
                try:
                    control = page.get_by_role("button", name=name, exact=True)
                    if control.count() and control.first.is_visible(timeout=300):
                        return True
                except Exception:
                    continue
            try:
                page.wait_for_timeout(250)
            except Exception:
                time.sleep(0.25)
        return False

    @staticmethod
    def _safe_url(page: Any) -> str:
        try:
            parts = urlsplit(page.url)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except Exception:
            return "unavailable"

    def runtime_status(self) -> dict[str, object]:
        pages = []
        if self._context_alive(self.context):
            try:
                pages = [page for page in self.context.pages if self._page_alive(page)]
            except Exception:
                pages = []
        return {
            "profile_path": str(self.user_data_dir),
            "auth_process_alive": self.auth_process_alive,
            "auth_pid": getattr(self._auth_process, "pid", None) if self.auth_process_alive else None,
            "automation_connected": self._context_alive(self.context),
            "page_count": len(pages),
            "gemini_page_count": sum(self._is_gemini_url(page.url) for page in pages),
            "selected_page_url": self._safe_url(self._managed_page),
            "navigation_result": self._navigation_result,
            "authentication_result": self._authentication_result,
            "preflight_result": self._preflight_result,
            "preflight_checks": dict(self._preflight_checks),
        }

    def _stop_automation(self) -> None:
        context = self.context
        playwright = self._playwright
        self.context = None
        self.browser = None
        self._managed_page = None
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

    def stop(self) -> None:
        with self._guard:
            auth_process = self._auth_process
            self._auth_process = None
            self._stop_automation()
            if auth_process is not None and auth_process.poll() is None:
                try:
                    auth_process.terminate()
                    auth_process.wait(timeout=5)
                except Exception:
                    try:
                        auth_process.kill()
                    except Exception:
                        pass

    def restart(self) -> Any:
        with self._guard:
            target = self._last_managed_url
            self._stop_automation()
            self._wait_for_profile_unlock()
            self._ensure_automation_context()
            page = self.ensure_page_alive()
            if target and target != "unavailable":
                page.goto(target, wait_until="domcontentloaded")
            return page
