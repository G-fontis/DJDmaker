from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

NOTEBOOK_HOME_URL = "https://notebook.google.com/"


class BrowserStartError(RuntimeError):
    pass


class BrowserConnectionError(BrowserStartError):
    pass


class BrowserNavigationError(BrowserStartError):
    pass


class BrowserAuthenticationRequired(BrowserStartError):
    preserve_browser = True


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
    """Own one attachable Chrome process/profile/session for the application."""

    def __init__(
        self,
        user_data_dir: Path,
        *,
        headless: bool = False,
        timeout_ms: int = 30_000,
        playwright_factory: Callable[[], Any] | None = None,
        chrome_executable: Path | None = None,
        chrome_launcher: Callable[[Path, Path, bool, str], tuple[Any, str]] | None = None,
        authentication_probe: Callable[[Any], bool] | None = None,
    ) -> None:
        self.user_data_dir = user_data_dir.resolve()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._factory = playwright_factory
        self.chrome_executable = chrome_executable or find_chrome()
        self._chrome_launcher = chrome_launcher or self._launch_chrome
        self._authentication_probe = authentication_probe
        self._guard = threading.RLock()
        self._process: Any = None
        self._endpoint = ""
        self._playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self._managed_page: Any = None
        self._navigation_result = "not-attempted"
        self._authentication_result = "not-checked"

    def open_login(self, url: str = NOTEBOOK_HOME_URL) -> dict[str, object]:
        """Open the reusable Chrome for human login and return immediately."""
        with self._guard:
            launched = not self.process_alive
            self._ensure_chrome_process(url)
            if not launched:
                self._ensure_attached()
                self.ensure_gemini_page()
                self._disconnect_keep_chrome()
            self._navigation_result = "login-page-opened"
            return self.runtime_status()

    def start(self) -> Any:
        """Attach to the existing Chrome, starting it only as a fallback."""
        with self._guard:
            self._ensure_chrome_process(NOTEBOOK_HOME_URL)
            self._ensure_attached()
            return self.ensure_page_alive()

    def prepare_for_processing(self) -> Any:
        with self._guard:
            self.start()
            page = self.ensure_gemini_page()
            if not self.ensure_authenticated(page):
                raise BrowserAuthenticationRequired(
                    "Googleへのログインが必要です。同じChromeでログインしてから、"
                    "もう一度［授業動画作成開始］を押してください。Chromeは閉じないでください。"
                )
            return page

    @property
    def process_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _ensure_chrome_process(self, url: str) -> None:
        if self.process_alive and self._endpoint:
            return
        self._discard_connection()
        self._process = None
        self._endpoint = ""
        if self.chrome_executable is None or not self.chrome_executable.is_file():
            raise BrowserStartError("Google Chromeの実行ファイルが見つかりません")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._process, self._endpoint = self._chrome_launcher(
                self.chrome_executable, self.user_data_dir, self.headless, url
            )
        except Exception as exc:
            raise BrowserStartError(
                "Google Chromeを起動できません。専用profileとChromeの状態を確認してください。"
            ) from exc
        if not self.process_alive or not self._endpoint:
            raise BrowserStartError("Google Chromeが起動直後に終了しました")

    def _launch_chrome(
        self, executable: Path, profile: Path, headless: bool, url: str
    ) -> tuple[Any, str]:
        port_file = profile / "DevToolsActivePort"
        try:
            port_file.unlink(missing_ok=True)
        except PermissionError as exc:
            raise BrowserStartError("Chromeのdebug port情報を更新できません") from exc
        arguments = [
            str(executable),
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
        ]
        if headless:
            arguments.extend(("--headless=new", "--disable-gpu"))
        arguments.append(url)
        process = subprocess.Popen(arguments, shell=False)
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserStartError("Chromeがdebug接続準備前に終了しました")
            try:
                lines = port_file.read_text(encoding="utf-8").splitlines()
                port = int(lines[0])
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                time.sleep(0.05)
                continue
            return process, f"http://127.0.0.1:{port}"
        process.terminate()
        raise BrowserConnectionError("Chromeのdebug接続準備が完了しませんでした")

    def _browser_connected(self) -> bool:
        if self.browser is None:
            return False
        try:
            checker = getattr(self.browser, "is_connected", None)
            return bool(checker()) if callable(checker) else True
        except Exception:
            return False

    def _ensure_attached(self) -> None:
        if self._browser_connected() and self._context_alive(self.context):
            return
        self._discard_connection()
        try:
            if self._factory is None:
                from playwright.sync_api import sync_playwright
                self._factory = sync_playwright
            self._playwright = self._factory().start()
            self.browser = self._playwright.chromium.connect_over_cdp(self._endpoint)
            contexts = list(self.browser.contexts)
            if not contexts:
                raise BrowserConnectionError("Chrome contextを取得できません")
            self.context = contexts[0]
            self.context.set_default_timeout(self.timeout_ms)
        except Exception as exc:
            self._discard_connection()
            if isinstance(exc, BrowserConnectionError):
                raise
            raise BrowserConnectionError("起動中Chromeへの接続に失敗しました") from exc

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
            self._ensure_attached()
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

    def ensure_gemini_page(self) -> Any:
        context = self.ensure_context_alive()
        pages = [page for page in list(context.pages) if self._page_alive(page)]
        existing = next((page for page in pages if self._is_gemini_url(page.url)), None)
        if existing is not None:
            self._managed_page = existing
            self._navigation_result = "existing-gemini-page"
            return existing
        page = self.ensure_page_alive()
        if page.url != "about:blank" and not self._is_gemini_url(page.url):
            page = context.new_page()
            self._managed_page = page
        try:
            page.goto(NOTEBOOK_HOME_URL, wait_until="domcontentloaded")
        except Exception as exc:
            self._navigation_result = "failed"
            raise BrowserNavigationError("Gemini Notebookへの移動に失敗しました") from exc
        self._navigation_result = "navigated"
        return page

    def ensure_authenticated(self, page: Any | None = None) -> bool:
        page = page or self.ensure_gemini_page()
        authenticated = (
            bool(self._authentication_probe(page))
            if self._authentication_probe is not None
            else self._default_authentication_probe(page)
        )
        self._authentication_result = "authenticated" if authenticated else "login-required"
        return authenticated

    def _default_authentication_probe(self, page: Any) -> bool:
        deadline = time.monotonic() + min(self.timeout_ms, 60_000) / 1000
        while time.monotonic() < deadline:
            try:
                parsed = urlsplit(page.url)
                if parsed.hostname in {"accounts.google.com", "accounts.youtube.com"}:
                    return False
                if parsed.hostname == "notebook.google.com":
                    if "/notebook/" in parsed.path:
                        return True
                    for name in ("新規作成", "ノートブックを新規作成", "Create new notebook"):
                        control = page.get_by_role("button", name=name, exact=True)
                        if control.count() and control.first.is_visible(timeout=300):
                            return True
            except Exception:
                pass
            try:
                page.wait_for_timeout(2_000)
            except Exception:
                time.sleep(2)
        return False

    @staticmethod
    def _safe_url(page: Any) -> str:
        try:
            parts = urlsplit(page.url)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except Exception:
            return "unavailable"

    def runtime_status(self) -> dict[str, object]:
        contexts: list[Any] = []
        if self._browser_connected():
            try:
                contexts = list(self.browser.contexts)
            except Exception:
                contexts = []
        pages = [
            page for context in contexts if self._context_alive(context)
            for page in list(context.pages) if self._page_alive(page)
        ]
        return {
            "profile_path": str(self.user_data_dir),
            "process_alive": self.process_alive,
            "pid": getattr(self._process, "pid", None) if self.process_alive else None,
            "browser_connected": self._browser_connected(),
            "context_count": len(contexts),
            "page_count": len(pages),
            "gemini_page_count": sum(self._is_gemini_url(page.url) for page in pages),
            "selected_page_url": self._safe_url(self._managed_page),
            "navigation_result": self._navigation_result,
            "authentication_result": self._authentication_result,
        }

    def _disconnect_keep_chrome(self) -> None:
        playwright = self._playwright
        self._playwright = None
        self.browser = None
        self.context = None
        self._managed_page = None
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _discard_connection(self) -> None:
        self._disconnect_keep_chrome()

    def stop(self) -> None:
        with self._guard:
            process = self._process
            self._process = None
            self._endpoint = ""
            self._discard_connection()
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    def restart(self) -> Any:
        self.stop()
        return self.start()
