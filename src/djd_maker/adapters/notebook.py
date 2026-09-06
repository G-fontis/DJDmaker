from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from djd_maker.core.interfaces import require_remote_deletion_gate
from djd_maker.core.models import DownloadSafetyGate, Job, preset_body_sha256
from djd_maker.adapters.credit import CreditDetector, CreditSnapshot, CreditState
from djd_maker.media.validator import VideoValidator


class NotebookAdapterError(RuntimeError):
    pass


class PresetApplyMismatchError(NotebookAdapterError):
    pass


class SourceReadyTimeoutError(NotebookAdapterError):
    pass


class SourceProcessingError(NotebookAdapterError):
    pass


class ArtifactDeletionRetryableError(NotebookAdapterError):
    """Deletion was not confirmed; the job may safely be retried later."""


class DomMismatchError(ArtifactDeletionRetryableError):
    pass


class ArtifactDeletionDisabled(NotebookAdapterError):
    pass


class ReservationUnavailableError(NotebookAdapterError):
    """Credit is exhausted but NotebookLM exposes no verified schedule action."""


class ReservationFailedError(NotebookAdapterError):
    """A schedule action was requested but its waiting state was not observed."""


class RemoteVideoStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GENERATING = "GENERATING"
    WAITING = "WAITING"
    READY = "READY"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ResumeMetadata:
    notebook_id: str
    notebook_url: str
    artifact_title: str


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    remote_status: RemoteVideoStatus
    reserved: bool
    credit: CreditSnapshot
    reservation_created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotebookSubmissionResult:
    """Structured result with two-value tuple compatibility for older callers."""

    notebook_id: str
    notebook_url: str
    generation: GenerationOutcome

    @property
    def reserved(self) -> bool:
        return self.generation.reserved

    @property
    def credit(self) -> CreditSnapshot:
        return self.generation.credit

    @property
    def reservation_created_at(self) -> str | None:
        value = self.generation.reservation_created_at
        return value.isoformat() if value else None

    @property
    def remote_status(self) -> RemoteVideoStatus:
        return self.generation.remote_status

    def __iter__(self):
        yield self.notebook_id
        yield self.notebook_url

    def __getitem__(self, index: int) -> str:
        return (self.notebook_id, self.notebook_url)[index]


@dataclass(frozen=True, slots=True)
class ArtifactDeleteSelectors:
    """Artifact-only controls ordered from verified names to fallbacks.

    Japanese ``その他`` and the artifact menu's ``削除`` were captured on the
    live NotebookLM UI on 2026-09-05 in GNBCreator's STATE_10_VIDEO_MENU.
    No artifact-delete confirmation dialog was captured, so dialogs are treated
    as optional and are rejected if they mention deletion of a Notebook.
    """

    more_button_names: tuple[str, ...] = ("その他", "More")
    delete_menu_names: tuple[str, ...] = ("削除", "Delete")
    artifact_menu_markers: tuple[str, ...] = ("ダウンロード", "Download")
    confirm_button_names: tuple[str, ...] = ("削除", "Delete")
    success_toast_markers: tuple[str, ...] = ()


VERIFIED_ARTIFACT_DELETE_SELECTORS = ArtifactDeleteSelectors()
_USE_VERIFIED_DELETE_SELECTORS = object()


class DownloadHandoff(Protocol):
    def __call__(self, page: Any, artifact_card: Any, destination: Path) -> Path: ...


class PlaywrightArtifactDownload:
    """Download one already-scoped artifact and publish only after validation."""

    def __init__(self, validator: VideoValidator, timeout_ms: int = 120_000) -> None:
        self.validator = validator
        self.timeout_ms = timeout_ms

    def __call__(self, page: Any, artifact_card: Any, destination: Path) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"download先を上書きしません: {destination}")
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.download.mp4"
        )
        keeper = None
        context = getattr(page, "context", None)
        try:
            if context is not None and len(getattr(context, "pages", ())) == 1:
                keeper = context.new_page()
            artifact_card.get_by_role("button", name="その他", exact=True).click()
            item = page.get_by_role("menuitem", name="ダウンロード", exact=True)
            item.wait_for(state="visible", timeout=self.timeout_ms)
            if not self._download_with_chrome(page, item, temporary):
                with page.expect_download(timeout=self.timeout_ms) as info:
                    item.click()
                download = info.value
                failure = download.failure()
                if failure:
                    raise NotebookAdapterError(f"artifact download failed: {failure}")
                download.save_as(str(temporary))
            self.validator.validate(temporary, reject_temporary=False)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise FileExistsError(f"download先を上書きしません: {destination}") from None
            self.validator.validate(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
            if keeper is not None and not getattr(page, "is_closed", lambda: False)():
                try:
                    keeper.close()
                except Exception:
                    pass

    def _download_with_chrome(self, page: Any, item: Any, temporary: Path) -> bool:
        """Use GNBCreator's CDP download path when Notebook closes its tab."""

        context = getattr(page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            return False
        download_dir = temporary.parent / f".gnb-download-{uuid4().hex}"
        download_dir.mkdir()
        client = None
        clicked = False
        progress: dict[str, object] = {}
        stable_size: int | None = None
        stable_since: float | None = None
        try:
            client = context.new_cdp_session(page)
            client.on("Browser.downloadProgress", lambda event: progress.update(event))
            client.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir),
                    "eventsEnabled": True,
                },
            )
            item.click()
            clicked = True
            deadline = time.monotonic() + self.timeout_ms / 1000
            while time.monotonic() < deadline:
                files = [candidate for candidate in download_dir.iterdir() if candidate.is_file()]
                completed = [
                    candidate
                    for candidate in files
                    if not candidate.name.endswith(".crdownload")
                ]
                if progress.get("state") == "canceled":
                    raise NotebookAdapterError("Chrome download was canceled")
                if completed:
                    completed[0].replace(temporary)
                    return True
                partials = [
                    candidate
                    for candidate in files
                    if candidate.name.endswith(".crdownload")
                ]
                if partials:
                    current_size = partials[0].stat().st_size
                    if current_size != stable_size:
                        stable_size = current_size
                        stable_since = time.monotonic()
                    if (
                        getattr(page, "is_closed", lambda: False)()
                        and current_size > 0
                        and stable_since is not None
                        and time.monotonic() - stable_since >= 1
                    ):
                        partials[0].replace(temporary)
                        return True
                try:
                    page.wait_for_timeout(100)
                except Exception:
                    time.sleep(0.1)
            raise NotebookAdapterError("Chrome download timed out")
        except Exception:
            if not clicked:
                return False
            raise
        finally:
            if client is not None:
                try:
                    client.detach()
                except Exception:
                    pass
            try:
                download_dir.rmdir()
            except OSError:
                # Preserve a partial download for diagnosis.
                pass


CREATE_NOTEBOOK = (
    ("role", "button", "新規作成"),
    ("role", "button", "ノートブックを新規作成"),
    ("role", "button", "Create new notebook"),
    ("css", "button[aria-label*='notebook' i]", ""),
)
TITLE_INPUT = (
    ("css", "editable-project-title input.title-input", ""),
    ("css", "input.title-input", ""),
)
FILE_INPUT = (
    ("label", "", "Upload sources"),
    ("label", "", "ソースをアップロード"),
    ("css", "input[type='file']", ""),
)
ADD_SOURCE = (
    ("role", "button", "ソースを追加"),
    ("role", "button", "Add source"),
    ("css", "button[aria-label*='source' i]", ""),
)
INFORMATION_DIALOG_CLOSE = (
    ("role", "button", "ダイアログを閉じる"),
    ("role", "button", "Close dialog"),
)
UPLOAD_FILE_BUTTON = (
    ("role", "button", "ファイルをアップロード"),
    ("role", "button", "Upload file"),
    ("text", "", "ファイルをアップロード"),
    ("text", "", "Upload file"),
)
SOURCE_READY = (
    ("text", "", "1 件のソース"),
    ("css", "button.source-stretched-button", ""),
    ("text", "", "1 source"),
    ("css", "[data-testid*='source'][aria-busy='false']", ""),
)
SOURCE_READY_TIMEOUT_MS = 300_000
SOURCE_ERROR_MARKERS = (
    "ソースを処理できません",
    "ソースの処理に失敗",
    "アップロードに失敗",
    "source processing failed",
    "failed to process source",
    "upload failed",
)
VIDEO_CREATE = (
    (
        "css",
        "basic-create-artifact-button:has([aria-label='動画解説']) .option-icon mat-icon.edit-icon",
        "",
    ),
    (
        "css",
        "basic-create-artifact-button:has([aria-label='Video Overview']) .option-icon mat-icon.edit-icon",
        "",
    ),
)
VIDEO_CUSTOM_TOPIC = (
    ("role", "textbox", "この動画で重視するポイントは何ですか？"),
    ("role", "textbox", "What should the AI hosts focus on?"),
    ("label", "", "この動画で重視するポイントは何ですか？"),
    ("css", "textarea", ""),
)
VIDEO_GENERATE = (
    ("role", "button", "生成"),
    ("role", "button", "Generate"),
)
VIDEO_SCHEDULE = tuple(
    ("role", "button", label)
    for label in (
        "予約して生成",
        "後で生成",
        "スケジュール設定",
        "Schedule",
        "Generate later",
        "Schedule generation",
    )
)
CHAT_INPUT = (
    ("role", "textbox", "クエリボックス"),
    ("label", "", "クエリボックス"),
    ("role", "textbox", "Start typing"),
    ("role", "textbox", "Ask a question"),
    ("role", "textbox", "入力を開始"),
    ("role", "textbox", "質問してみましょう"),
    ("css", "textarea.query-box-input", ""),
    ("css", "textarea", ""),
    ("css", "[contenteditable='true'][role='textbox']", ""),
)
CHAT_SEND = (
    ("role", "button", "送信"),
    ("role", "button", "Send"),
    ("label", "", "送信"),
    ("label", "", "Send"),
    ("css", "button.submit-button[type='submit']", ""),
    ("css", "button[type='submit']", ""),
)
CHAT_MESSAGE_SURFACES = (
    "[data-message-author-role='user']",
    "[class*='user-message' i]",
    "[class*='query-message' i]",
    "chat-message",
    "[data-testid*='message' i]",
    ".message-content",
)


class NotebookDomAdapter:
    """NotebookLM DOM adapter。Notebookを削除するAPIは意図的に持たない。"""

    HOME_URL = "https://notebook.google.com/"
    ACTIVE_MARKERS = (
        "ショート動画の概要を生成しています",
        "動画解説の概要を生成しています",
        "動画解説を生成しています",
        "生成中",
        "生成しています",
        "generating",
        "preparing",
    )
    WAITING_MARKERS = ("スケジュール設定されています", "scheduled for later")
    FAILED_MARKERS = ("動画解説を生成できませんでした", "failed to generate video")

    def __init__(
        self,
        page: Any,
        *,
        download_handoff: DownloadHandoff | None = None,
        artifact_delete_selectors: ArtifactDeleteSelectors | None | object = _USE_VERIFIED_DELETE_SELECTORS,
        timeout_ms: int = 30_000,
        diagnostic: Callable[[str], None] | None = None,
        credit_detector: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.page = page
        self.download_handoff = download_handoff
        self.artifact_delete_selectors = (
            VERIFIED_ARTIFACT_DELETE_SELECTORS
            if artifact_delete_selectors is _USE_VERIFIED_DELETE_SELECTORS
            else artifact_delete_selectors
        )
        self.timeout_ms = timeout_ms
        self.diagnostic = diagnostic or (lambda _reason: None)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._injected_credit_detector = credit_detector
        self._generation_prompt_for_fallback: str | None = None

    def inspect_credit(self) -> CreditSnapshot:
        detector = self._injected_credit_detector or CreditDetector(
            self.page, clock=self.clock
        )
        return detector.detect()

    def _locator(self, kind: str, role: str, value: str) -> Any:
        if kind == "role":
            return self.page.get_by_role(role, name=value, exact=True)
        if kind == "label":
            return self.page.get_by_label(value, exact=True)
        if kind == "text":
            return self.page.get_by_text(value, exact=True)
        if kind == "css":
            return self.page.locator(role)
        raise ValueError(f"unsupported selector kind: {kind}")

    def _first_visible(self, candidates: Iterable[tuple[str, str, str]], name: str) -> Any:
        # Keep the same delayed-DOM tolerance as GNBCreator.  Notebook's home
        # shell reaches ``domcontentloaded`` before its Angular controls mount.
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            for candidate in candidates:
                try:
                    locator = self._locator(*candidate)
                    if locator.count() and locator.first.is_visible(timeout=300):
                        return locator.first
                except Exception:
                    continue
            self.page.wait_for_timeout(250)
        self.diagnostic(f"DOM_MISMATCH:{name}")
        raise DomMismatchError(f"{name}を特定できません")

    def _first_enabled_visible(
        self,
        candidates: Iterable[tuple[str, str, str]],
        name: str,
    ) -> Any:
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            for candidate in candidates:
                try:
                    locator = self._locator(*candidate)
                    for index in range(locator.count()):
                        item = locator.nth(index)
                        try:
                            visible = item.is_visible(timeout=300)
                            enabled = item.is_enabled(timeout=300)
                        except TypeError:
                            visible = item.is_visible()
                            enabled = item.is_enabled()
                        if visible and enabled:
                            return item
                except Exception:
                    continue
            self.page.wait_for_timeout(250)
        self.diagnostic(f"DOM_MISMATCH:{name}_not_enabled")
        raise DomMismatchError(f"{name}が有効になりません")

    def _try_first_visible(self, candidates: Iterable[tuple[str, str, str]]) -> Any | None:
        for candidate in candidates:
            try:
                locator = self._locator(*candidate)
                if locator.count() and locator.first.is_visible(timeout=300):
                    return locator.first
            except Exception:
                continue
        return None

    def _try_first_attached(self, candidates: Iterable[tuple[str, str, str]]) -> Any | None:
        for candidate in candidates:
            try:
                locator = self._locator(*candidate)
                if locator.count():
                    return locator.first
            except Exception:
                continue
        return None

    @classmethod
    def preflight_home_page(cls, page: Any, timeout_ms: int = 30_000) -> bool:
        """Confirm the home create control without clicking or creating anything."""
        adapter = cls(page, timeout_ms=timeout_ms)
        try:
            adapter._first_visible(CREATE_NOTEBOOK, "Notebook作成ボタン")
        except DomMismatchError:
            return False
        return True

    def _dismiss_optional_dialogs(self, timeout_ms: int = 5_000) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        quiet_since = time.monotonic()
        while time.monotonic() < deadline:
            close = self._try_first_visible(INFORMATION_DIALOG_CLOSE)
            if close is not None:
                close.click()
                quiet_since = time.monotonic()
                self.page.wait_for_timeout(250)
                continue
            if time.monotonic() - quiet_since >= 2:
                return
            self.page.wait_for_timeout(250)

    def create_notebook(self) -> ResumeMetadata:
        self.page.goto(self.HOME_URL, wait_until="domcontentloaded")
        self._first_visible(CREATE_NOTEBOOK, "Notebook作成ボタン").click()
        self.page.wait_for_url("**/notebook/**", timeout=self.timeout_ms)
        deadline = time.monotonic() + self.timeout_ms / 1000
        notebook_id = ""
        url = self.page.url
        while time.monotonic() < deadline:
            url = self.page.url
            parsed = urlparse(url)
            candidate = Path(parsed.path).name
            if parsed.hostname == "notebook.google.com" and candidate not in {"", "creating"}:
                notebook_id = candidate
                break
            self.page.wait_for_timeout(250)
        if not notebook_id:
            raise DomMismatchError("作成後Notebook URLを確認できません")
        return ResumeMetadata(notebook_id, url, "")

    def rename_notebook(self, title: str) -> None:
        expected = " ".join(title.split())
        if not expected:
            raise ValueError("Notebook名は空にできません")
        editor = self._first_visible(TITLE_INPUT, "Notebookタイトル入力")
        editor.fill(title)
        editor.press("Enter")
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            actual = ""
            page_title = ""
            try:
                actual = " ".join(editor.input_value().split())
            except Exception:
                pass
            try:
                page_title = " ".join(self.page.title().split())
            except Exception:
                pass
            if actual == expected and (
                page_title == expected or page_title.startswith(expected + " - ")
            ):
                return
            self.page.wait_for_timeout(250)
        self.diagnostic("DOM_MISMATCH:notebook_title_readback")
        raise DomMismatchError("Notebook名の確定をreadbackできません")

    def upload_txt(self, source_path: Path) -> None:
        source = source_path.resolve()
        if not source.is_file() or source.suffix.casefold() != ".txt":
            raise FileNotFoundError(f"有効なTXTではありません: {source}")
        # Ported from GNBCreator: the input may already exist, or it may only
        # mount after the explicit Add source action. Informational release
        # dialogs are dismissed by their dedicated close control only.
        self._dismiss_optional_dialogs()
        file_input = self._try_first_attached(FILE_INPUT)
        if file_input is None:
            self._first_visible(ADD_SOURCE, "ソース追加ボタン").click()
            self._dismiss_optional_dialogs()
            file_input = self._try_first_attached(FILE_INPUT)
        if file_input is None:
            upload_button = self._first_visible(
                UPLOAD_FILE_BUTTON, "ファイルをアップロードボタン"
            )
            with self.page.expect_file_chooser(timeout=self.timeout_ms) as chooser_info:
                upload_button.click()
            chooser_info.value.set_files(str(source))
            return
        file_input.set_input_files(str(source))

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split())

    def _body_text(self) -> str:
        try:
            return self._normalize_text(self.page.locator("body").inner_text())
        except Exception:
            return ""

    def _any_visible(self, selector: str) -> bool:
        try:
            items = self.page.locator(selector)
            return any(
                items.nth(index).is_visible(timeout=300)
                for index in range(items.count())
            )
        except Exception:
            return False

    def _source_registered(self, filename: str) -> bool:
        expected = self._normalize_text(Path(filename).name)
        for selector in ("button.source-stretched-button", ".source-title"):
            try:
                items = self.page.locator(selector)
                for index in range(items.count()):
                    item = items.nth(index)
                    if not item.is_visible(timeout=300):
                        continue
                    values: list[str] = []
                    try:
                        values.append(self._normalize_text(item.inner_text()))
                    except Exception:
                        pass
                    try:
                        values.append(
                            self._normalize_text(item.get_attribute("aria-label") or "")
                        )
                    except Exception:
                        pass
                    if any(expected in value for value in values):
                        return True
            except Exception:
                continue
        return False

    def _source_processing(self) -> bool:
        for selector in (
            "[role='progressbar']",
            "[aria-busy='true']",
            "mat-progress-spinner",
            "mat-spinner",
            ".source-loading",
            ".source-processing",
        ):
            if self._any_visible(selector):
                return True
        body = self._body_text().casefold()
        return any(
            marker in body
            for marker in ("ソースを処理中", "ソースを解析中", "processing source")
        )

    def _chat_source_count_positive(self) -> bool:
        body = self._body_text()
        counts = [int(value) for value in re.findall(r"(\d+)\s*個のソース", body)]
        counts.extend(int(value) for value in re.findall(r"(\d+)\s+sources?", body, re.I))
        return any(value > 0 for value in counts)

    def _studio_video_card_active(self) -> bool:
        """The user-visible readiness gate is the enabled Studio video card."""
        for name in ("動画解説", "Video Overview"):
            try:
                cards = self.page.get_by_role("button", name=name, exact=True)
                for index in range(cards.count()):
                    card = cards.nth(index)
                    if card.is_visible(timeout=300) and card.is_enabled(timeout=300):
                        return True
            except Exception:
                continue
        for selector in (
            "basic-create-artifact-button:has([aria-label='動画解説']) button",
            "basic-create-artifact-button:has([aria-label='Video Overview']) button",
            "basic-create-artifact-button [aria-label='動画解説']",
            "basic-create-artifact-button [aria-label='Video Overview']",
        ):
            try:
                cards = self.page.locator(selector)
                for index in range(cards.count()):
                    card = cards.nth(index)
                    if card.is_visible(timeout=300) and card.is_enabled(timeout=300):
                        return True
            except Exception:
                continue
        return False

    def wait_for_source_ready(
        self,
        filename: str,
        *,
        timeout_ms: int = SOURCE_READY_TIMEOUT_MS,
    ) -> None:
        """Wait for indexing and the right-side Studio card to become active."""
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since: float | None = None
        while time.monotonic() < deadline:
            body = self._body_text().casefold()
            if any(marker in body for marker in SOURCE_ERROR_MARKERS):
                self.diagnostic("SOURCE_PROCESSING_FAILED")
                raise SourceProcessingError("Notebookのソース処理に失敗しました")
            ready = (
                self._source_registered(filename)
                and not self._source_processing()
                and self._chat_source_count_positive()
                and self._studio_video_card_active()
            )
            if ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 2:
                    self.diagnostic("SOURCE_READY_CONFIRMED:studio_video_card_active")
                    return
            else:
                stable_since = None
            self.page.wait_for_timeout(500)
        self.diagnostic("SOURCE_READY_TIMEOUT:300s")
        raise SourceReadyTimeoutError("TXT sourceの解析が5分以内に完了しませんでした")

    def start_video_generation(self, prompt: str) -> GenerationOutcome | None:
        """Legacy diagnostic path; production submission uses chat instead."""
        if not prompt.strip():
            raise ValueError("動画生成プリセット本文が空です")
        credit = self.inspect_credit()
        if credit.state is CreditState.EXHAUSTED:
            return self.request_scheduled_video_generation(prompt, credit)
        self._first_visible(VIDEO_CREATE, "Video Overview作成").click()
        topic = self._first_visible(
            VIDEO_CUSTOM_TOPIC, "動画解説のカスタムトピック欄"
        )
        self.diagnostic(
            "PRESET_APPLY_EXPECTED:"
            f"sha256={preset_body_sha256(prompt)},length={len(prompt)}"
        )
        topic.fill(prompt)
        try:
            readback = topic.input_value()
        except Exception as exc:
            self.diagnostic("PRESET_APPLY_MISMATCH:readback_failed")
            raise PresetApplyMismatchError(
                "PRESET_APPLY_MISMATCH: preset DOM readback failed"
            ) from exc
        self.diagnostic(
            "PRESET_APPLY_READBACK:"
            f"sha256={preset_body_sha256(readback)},length={len(readback)}"
        )
        if readback != prompt:
            self.diagnostic("PRESET_APPLY_MISMATCH:value_differs")
            raise PresetApplyMismatchError(
                "PRESET_APPLY_MISMATCH: preset snapshot and DOM readback differ"
            )
        self._first_visible(VIDEO_GENERATE, "動画生成ボタン").click()

    def request_scheduled_video_generation(
        self,
        prompt: str,
        credit: CreditSnapshot,
        *,
        timeout_ms: int = 120_000,
    ) -> GenerationOutcome:
        """Use only a verified schedule action and require its remote status."""
        if credit.state is not CreditState.EXHAUSTED:
            raise ReservationFailedError(
                "明示的なクレジット枯渇根拠なしでは予約生成しません"
            )
        if self.page.locator("artifact-library-item").count():
            raise ReservationFailedError(
                "予約操作前に動画artifactが存在するため重複生成を停止しました"
            )
        self._first_visible(VIDEO_CREATE, "Video Overview作成").click()
        topic = self._first_visible(
            VIDEO_CUSTOM_TOPIC, "動画解説のカスタムトピック欄"
        )
        topic.fill(prompt)
        if self._input_text(topic) != prompt:
            raise PresetApplyMismatchError(
                "PRESET_APPLY_MISMATCH: scheduled preset and DOM readback differ"
            )
        try:
            schedule = self._first_enabled_visible(
                VIDEO_SCHEDULE, "クレジット不足時の予約生成ボタン"
            )
        except DomMismatchError as exc:
            raise ReservationUnavailableError(
                "クレジット不足を検出しましたが予約生成UIを確認できません"
            ) from exc
        created_at = self.clock()
        schedule.click()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            status = self.inspect_status()
            if status is RemoteVideoStatus.WAITING:
                self.diagnostic("CREDIT_RESERVATION_CONFIRMED:SCHEDULED_REMOTE")
                return GenerationOutcome(status, True, credit, created_at)
            if status is RemoteVideoStatus.FAILED:
                raise ReservationFailedError(
                    "NotebookLMが予約生成の失敗を表示しました"
                )
            self.page.wait_for_timeout(2_000)
        raise ReservationFailedError(
            "予約生成操作後にSCHEDULED_REMOTEを確認できませんでした"
        )

    @staticmethod
    def _input_text(control: Any) -> str:
        try:
            return control.input_value()
        except Exception:
            try:
                return control.text_content() or ""
            except Exception:
                return ""

    def _sent_message_visible(self, prompt: str) -> bool:
        for selector in CHAT_MESSAGE_SURFACES:
            try:
                messages = self.page.locator(selector)
                for index in range(messages.count()):
                    message = messages.nth(index)
                    if message.is_visible(timeout=300) and message.inner_text() == prompt:
                        return True
            except Exception:
                continue
        return False

    def _wait_for_generation_chat_ready(self, *, timeout_ms: int = 60_000) -> Any:
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since: float | None = None
        textbox: Any = None
        while time.monotonic() < deadline:
            textbox = self._try_first_visible(CHAT_INPUT)
            enabled = False
            if textbox is not None:
                try:
                    enabled = bool(textbox.is_enabled(timeout=300))
                except TypeError:
                    enabled = bool(textbox.is_enabled())
                except Exception:
                    enabled = False
            ready = enabled and self._studio_video_card_active()
            if ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 2:
                    self.diagnostic("GENERATION_CHAT_READY_STABLE")
                    return textbox
            else:
                stable_since = None
            self.page.wait_for_timeout(250)
        raise NotebookAdapterError(
            "メインチャットとStudioカードの安定した準備完了を確認できませんでした"
        )

    def _wait_for_chat_message_sent(
        self,
        prompt: str,
        textbox: Any,
        *,
        timeout_ms: int = 60_000,
    ) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since: float | None = None
        while time.monotonic() < deadline:
            sent = self._input_text(textbox) == "" and self._sent_message_visible(prompt)
            if sent:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 2:
                    self.diagnostic("PRESET_CHAT_SENT_CONFIRMED_STABLE")
                    return
            else:
                stable_since = None
            self.page.wait_for_timeout(250)
        raise PresetApplyMismatchError(
            "PRESET_APPLY_MISMATCH: sent preset message was not confirmed in DOM"
        )

    def _wait_for_notebook_auto_generation(
        self,
        *,
        timeout_ms: int = 120_000,
    ) -> GenerationOutcome:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            status = self.inspect_status()
            if status in {
                RemoteVideoStatus.GENERATING,
                RemoteVideoStatus.WAITING,
                RemoteVideoStatus.READY,
            }:
                credit = self.inspect_credit()
                if (
                    status is RemoteVideoStatus.WAITING
                    and credit.state is not CreditState.EXHAUSTED
                ):
                    raise ReservationFailedError(
                        "クレジット不足の明示表示なしで予約状態を検出しました"
                    )
                card = self.page.locator("artifact-library-item").first
                text = ""
                try:
                    text = self._normalize_text(card.inner_text()).casefold()
                except Exception:
                    pass
                if any(marker in text for marker in ("短め", "short", "brief")):
                    raise NotebookAdapterError(
                        "SHORT_DEFAULT_USED: Notebookが短め動画として生成を開始しました"
                    )
                self.diagnostic(f"NOTEBOOK_AUTO_GENERATION_STARTED:{status.value}")
                return GenerationOutcome(
                    status,
                    status is RemoteVideoStatus.WAITING
                    and credit.state is CreditState.EXHAUSTED,
                    credit,
                    self.clock() if status is RemoteVideoStatus.WAITING else None,
                )
            if status is RemoteVideoStatus.FAILED:
                raise NotebookAdapterError("Notebookの自動動画生成開始に失敗しました")
            credit = self.inspect_credit()
            if credit.state is CreditState.EXHAUSTED:
                prompt = self._generation_prompt_for_fallback
                if not prompt:
                    raise ReservationFailedError(
                        "クレジット不足を検出しましたが予約用promptがありません"
                    )
                return self.request_scheduled_video_generation(prompt, credit)
            self.page.wait_for_timeout(500)
        raise NotebookAdapterError("Notebookの自動動画生成開始を確認できませんでした")

    def start_video_generation_from_chat(self, prompt: str) -> GenerationOutcome:
        """Send the exact job snapshot to main chat and let Notebook generate."""
        if not prompt.strip():
            raise ValueError("動画生成プリセット本文が空です")
        if self.page.locator("artifact-library-item").count():
            raise NotebookAdapterError(
                "チャット送信前に動画artifactが存在するため重複生成を停止しました"
            )
        credit = self.inspect_credit()
        if credit.state is CreditState.EXHAUSTED:
            return self.request_scheduled_video_generation(prompt, credit)
        textbox = self._wait_for_generation_chat_ready()
        self.diagnostic(
            "PRESET_CHAT_EXPECTED:"
            f"sha256={preset_body_sha256(prompt)},length={len(prompt)}"
        )
        textbox.fill(prompt)
        readback = self._input_text(textbox)
        self.diagnostic(
            "PRESET_CHAT_READBACK:"
            f"sha256={preset_body_sha256(readback)},length={len(readback)}"
        )
        if readback != prompt:
            raise PresetApplyMismatchError(
                "PRESET_APPLY_MISMATCH: preset snapshot and main chat readback differ"
            )
        send = self._first_enabled_visible(CHAT_SEND, "Notebookメインチャット送信ボタン")
        send.click()
        self._wait_for_chat_message_sent(prompt, textbox)
        self._generation_prompt_for_fallback = prompt
        try:
            outcome = self._wait_for_notebook_auto_generation()
        finally:
            self._generation_prompt_for_fallback = None
        if isinstance(outcome, GenerationOutcome):
            return outcome
        # Compatibility with diagnostic/test doubles written before outcomes
        # were structured. Production always returns the branch above.
        return GenerationOutcome(RemoteVideoStatus.UNKNOWN, False, credit)

    def inspect_status(self) -> RemoteVideoStatus:
        cards = self.page.locator("artifact-library-item")
        count = cards.count()
        if count > 1:
            self.diagnostic("DOM_MISMATCH:multiple_artifacts")
            return RemoteVideoStatus.UNKNOWN
        if count == 1:
            card = cards.first
            try:
                play = card.get_by_role("button", name="再生", exact=True)
                if play.count() and play.first.is_visible(timeout=300):
                    return RemoteVideoStatus.READY
            except Exception:
                pass
            text = " ".join(card.inner_text().split()).casefold()
        else:
            text = ""
            for selector in ("artifact-library", "studio-panel"):
                try:
                    text += " " + self.page.locator(selector).first.inner_text()
                except Exception:
                    pass
            text = " ".join(text.split()).casefold()
        if any(item.casefold() in text for item in self.FAILED_MARKERS):
            return RemoteVideoStatus.FAILED
        if any(item.casefold() in text for item in self.WAITING_MARKERS):
            return RemoteVideoStatus.WAITING
        if any(item.casefold() in text for item in self.ACTIVE_MARKERS):
            return RemoteVideoStatus.GENERATING
        return RemoteVideoStatus.NOT_STARTED if not text else RemoteVideoStatus.UNKNOWN

    def download_artifact(self, artifact_title: str, destination: Path) -> Path:
        if self.download_handoff is None:
            raise NotebookAdapterError("download handoffが設定されていません")
        if destination.exists():
            raise FileExistsError(f"download先を上書きしません: {destination}")
        # Notebook's Angular artifact list mounts after ``domcontentloaded``.
        # This follows GNBCreator's recovery wait instead of treating the first
        # empty DOM frame after a browser restart as a selector mismatch.
        deadline = time.monotonic() + min(self.timeout_ms, 60_000) / 1000
        all_cards = self.page.locator("artifact-library-item")
        while all_cards.count() == 0 and time.monotonic() < deadline:
            try:
                self.page.wait_for_timeout(2_000)
            except Exception:
                time.sleep(2)
            all_cards = self.page.locator("artifact-library-item")
        cards = all_cards.filter(has_text=artifact_title)
        if cards.count() == 1:
            target = cards.first
        else:
            # GNBCreator's proven fallback: generated artifact titles do not
            # necessarily equal the Notebook title, so accept only the sole
            # card with a verified Play control.
            try:
                target, _title_scoped = self._playable_artifact(artifact_title)
            except DomMismatchError:
                self.diagnostic("DOM_MISMATCH:download_artifact_not_unique")
                raise DomMismatchError(
                    "download対象動画artifactを一意に特定できません"
                ) from None
        return self.download_handoff(self.page, target, destination)

    @staticmethod
    def _visible(locator: Any, timeout: int = 300) -> bool:
        try:
            return bool(locator.count()) and locator.first.is_visible(timeout=timeout)
        except Exception:
            return False

    def _first_role(self, root: Any, role: str, names: tuple[str, ...]) -> Any:
        for name in names:
            try:
                locator = root.get_by_role(role, name=name, exact=True)
                if self._visible(locator):
                    return locator.first
            except Exception:
                continue
        raise DomMismatchError(
            f"visible {role} was not found for candidates: {', '.join(names)}"
        )

    def _playable_artifact(self, artifact_title: str) -> tuple[Any, bool]:
        cards = self.page.locator("artifact-library-item")
        try:
            matching = cards.filter(has_text=artifact_title)
            if matching.count() == 1:
                candidate = matching.first
                if any(
                    self._visible(candidate.get_by_role("button", name=name, exact=True))
                    for name in ("再生", "Play")
                ):
                    return candidate, True
            playable = []
            for index in range(cards.count()):
                card = cards.nth(index)
                if any(
                    self._visible(card.get_by_role("button", name=name, exact=True))
                    for name in ("再生", "Play")
                ):
                    playable.append(card)
            if len(playable) == 1:
                return playable[0], False
        except Exception as exc:
            self.diagnostic("DOM_MISMATCH:artifact_scope_inspection")
            raise DomMismatchError("video artifact scope could not be inspected") from exc
        self.diagnostic("DOM_MISMATCH:delete_artifact_not_unique")
        raise DomMismatchError("delete target is not exactly one playable video artifact")

    def _wait_for_artifact_cards(self) -> None:
        """Wait for GNBCreator's delayed Studio artifact mount after navigation."""

        deadline = time.monotonic() + min(self.timeout_ms, 60_000) / 1000
        cards = self.page.locator("artifact-library-item")
        while cards.count() == 0 and time.monotonic() < deadline:
            try:
                self.page.wait_for_timeout(2_000)
            except Exception:
                time.sleep(2)
            cards = self.page.locator("artifact-library-item")

    def _target_is_present(self, artifact_title: str, title_scoped: bool) -> bool:
        cards = self.page.locator("artifact-library-item")
        if title_scoped:
            cards = cards.filter(has_text=artifact_title)
        for index in range(cards.count()):
            card = cards.nth(index)
            if any(
                self._visible(card.get_by_role("button", name=name, exact=True))
                for name in ("再生", "Play")
            ):
                return True
        return False

    def _verify_absent_after_refresh(
        self, artifact_title: str, title_scoped: bool
    ) -> None:
        try:
            self.page.reload(wait_until="domcontentloaded")
        except Exception as exc:
            self.diagnostic(f"DELETE_RETRYABLE:refresh_{type(exc).__name__}")
            raise ArtifactDeletionRetryableError(
                "artifact disappeared but refresh verification failed"
            ) from exc
        if self._target_is_present(artifact_title, title_scoped):
            self.diagnostic("DELETE_RETRYABLE:artifact_reappeared")
            raise ArtifactDeletionRetryableError(
                "deleted artifact reappeared after refresh"
            )

    def _visible_dialog(self) -> Any | None:
        try:
            dialogs = self.page.get_by_role("dialog")
            visible = [
                dialogs.nth(index)
                for index in range(dialogs.count())
                if self._visible(dialogs.nth(index))
            ]
        except Exception:
            return None
        if len(visible) > 1:
            # Angular Material exposes both the outer mat-dialog-container and
            # its nested semantic dialog to Playwright's role engine. Scope to
            # the actual aria-modal dialog; unrelated simultaneous dialogs
            # remain fail-closed.
            modal = []
            for dialog in visible:
                try:
                    if dialog.get_attribute("aria-modal") == "true":
                        modal.append(dialog)
                except Exception:
                    continue
            if len(modal) == 1:
                return modal[0]
            raise DomMismatchError("multiple confirmation dialogs are visible")
        return visible[0] if visible else None

    def _success_toast_visible(self, selectors: ArtifactDeleteSelectors) -> bool:
        for marker in selectors.success_toast_markers:
            try:
                if self._visible(self.page.get_by_text(marker, exact=False)):
                    return True
            except Exception:
                continue
        return False

    def delete_video_artifact(
        self, artifact_title: str, gate: DownloadSafetyGate
    ) -> None:
        require_remote_deletion_gate(gate)
        selectors = self.artifact_delete_selectors
        if not isinstance(selectors, ArtifactDeleteSelectors):
            raise ArtifactDeletionDisabled(
                "artifact-only deletion controls are explicitly disabled"
            )
        self._wait_for_artifact_cards()
        card, title_scoped = self._playable_artifact(artifact_title)
        try:
            toast_was_visible = self._success_toast_visible(selectors)
            existing_menus = self.page.get_by_role("menu")
            if any(
                self._visible(existing_menus.nth(index))
                for index in range(existing_menus.count())
            ):
                raise DomMismatchError("a menu was already open before artifact scoping")
            self._first_role(card, "button", selectors.more_button_names).click()
            menus = self.page.get_by_role("menu")
            if menus.count() != 1 or not self._visible(menus.first):
                raise DomMismatchError("exactly one artifact action menu is required")
            # The captured artifact menu contains Download; the Notebook project
            # menu is rejected even if it also contains an exact Delete label.
            self._first_role(
                menus.first, "menuitem", selectors.artifact_menu_markers
            )
            self._first_role(
                menus.first, "menuitem", selectors.delete_menu_names
            ).click()

            # Evidence proves the artifact menu item, but not whether the
            # current UI always asks for confirmation. Briefly observe both
            # variants; a late dialog remains fail-closed instead of being
            # clicked through speculatively.
            dialog = None
            probe_deadline = time.monotonic() + min(
                1.0, self.timeout_ms / 1000
            )
            while time.monotonic() < probe_deadline:
                if not self._visible(card):
                    self._verify_absent_after_refresh(artifact_title, title_scoped)
                    return
                if (
                    not toast_was_visible
                    and self._success_toast_visible(selectors)
                ):
                    self._verify_absent_after_refresh(artifact_title, title_scoped)
                    return
                dialog = self._visible_dialog()
                if dialog is not None:
                    break
                self.page.wait_for_timeout(50)
            if dialog is not None:
                text = " ".join(dialog.inner_text().split()).casefold()
                if "notebook" in text or "ノートブック" in text:
                    self.diagnostic("DELETE_ABORTED:notebook_confirmation")
                    raise DomMismatchError(
                        "refusing a dialog that could delete the Notebook"
                    )
                self._first_role(
                    dialog, "button", selectors.confirm_button_names
                ).click()

            deadline = time.monotonic() + self.timeout_ms / 1000
            while time.monotonic() < deadline:
                new_success_toast = (
                    not toast_was_visible and self._success_toast_visible(selectors)
                )
                if not self._visible(card) or new_success_toast:
                    self._verify_absent_after_refresh(artifact_title, title_scoped)
                    return
                self.page.wait_for_timeout(100)
        except (DomMismatchError, ArtifactDeletionRetryableError):
            raise
        except Exception as exc:
            self.diagnostic(f"DELETE_RETRYABLE:{type(exc).__name__}")
            raise ArtifactDeletionRetryableError(
                "artifact deletion interaction failed and may be retried"
            ) from exc
        self.diagnostic("DELETE_RETRYABLE:success_not_observed")
        raise ArtifactDeletionRetryableError(
            "artifact deletion was not confirmed by card disappearance or configured toast"
        )


class NotebookEngineAdapter:
    """DOM操作をpipeline用のjob単位interfaceへまとめる。"""

    def __init__(
        self,
        dom: NotebookDomAdapter,
        recover_page: Callable[[], Any] | None = None,
        persist_identity: Callable[[Job], None] | None = None,
    ) -> None:
        self.dom = dom
        self.recover_page = recover_page
        self.persist_identity = persist_identity

    @staticmethod
    def _set_if_available(job: Job, name: str, value: Any) -> None:
        if hasattr(job, name):
            setattr(job, name, value)

    def _record_submission(
        self,
        job: Job,
        metadata: ResumeMetadata,
        outcome: GenerationOutcome,
    ) -> None:
        job.notebook_id = metadata.notebook_id
        job.notebook_url = metadata.notebook_url
        self._set_if_available(job, "credit_state", outcome.credit.state.value)
        self._set_if_available(job, "credit_percent", outcome.credit.percent)
        self._set_if_available(
            job,
            "credit_reset_at",
            outcome.credit.reset_at.isoformat() if outcome.credit.reset_at else None,
        )
        if outcome.reserved:
            created = outcome.reservation_created_at or datetime.now().astimezone()
            self._set_if_available(job, "reservation_created_at", created.isoformat())
            self._set_if_available(
                job,
                "expected_generation_after",
                outcome.credit.reset_at.isoformat() if outcome.credit.reset_at else None,
            )
            self._set_if_available(job, "last_checked_at", created.isoformat())
            self._set_if_available(job, "artifact_status", "RESERVED")
            self._set_if_available(job, "download_status", "PENDING")
            self._set_if_available(job, "raw_status", "PENDING")
            self._set_if_available(job, "recovery_retry_count", 0)
        if self.persist_identity is not None:
            self.persist_identity(job)

    def submit(self, job: Job) -> NotebookSubmissionResult:
        try:
            prompt = job.require_preset_body_snapshot()
        except ValueError as exc:
            raise NotebookAdapterError(str(exc)) from exc
        source = Path(job.source_path)
        metadata = self.dom.create_notebook()
        self.dom.rename_notebook(job.script_name)
        self.dom.upload_txt(source)
        try:
            self.dom.wait_for_source_ready(source.name)
        except SourceReadyTimeoutError:
            self.dom.rename_notebook(f"FAILED_{job.script_name}")
            metadata = self.dom.create_notebook()
            self.dom.rename_notebook(job.script_name)
            self.dom.upload_txt(source)
            try:
                self.dom.wait_for_source_ready(source.name)
            except SourceReadyTimeoutError:
                self.dom.rename_notebook(f"FAILED_{job.script_name}")
                raise
        # Persist/adopt the remote identity before the irreversible generation
        # action when the caller supplied a repository callback.
        job.notebook_id = metadata.notebook_id
        job.notebook_url = metadata.notebook_url
        if self.persist_identity is not None:
            self.persist_identity(job)
        outcome = self.dom.start_video_generation_from_chat(prompt)
        if not isinstance(outcome, GenerationOutcome):
            outcome = GenerationOutcome(
                RemoteVideoStatus.UNKNOWN,
                False,
                CreditSnapshot(),
            )
        self._record_submission(job, metadata, outcome)
        return NotebookSubmissionResult(
            metadata.notebook_id,
            metadata.notebook_url,
            outcome,
        )

    def _open_job(self, job: Job) -> None:
        if not job.notebook_url:
            raise NotebookAdapterError("jobにNotebook URLがありません")
        parsed = urlparse(job.notebook_url)
        if parsed.scheme != "https" or parsed.hostname != "notebook.google.com":
            raise NotebookAdapterError("jobのNotebook URLが不正です")
        # Notebook downloads can close their initiating tab. GNBCreator keeps
        # a second tab alive; adopt it before cleanup and navigate back to the
        # same persisted Notebook identity.
        try:
            page_closed = bool(self.dom.page.is_closed())
        except Exception:
            page_closed = False
        if page_closed:
            context = getattr(self.dom.page, "context", None)
            pages = [
                candidate
                for candidate in getattr(context, "pages", ())
                if not getattr(candidate, "is_closed", lambda: True)()
            ]
            if pages:
                self.dom.page = pages[0]
            elif self.recover_page is None:
                raise NotebookAdapterError(
                    "download後のbrowser contextを復旧できません"
                )
            else:
                self.dom.page = self.recover_page()

        # Preserve the already-mounted Studio DOM between status, download and
        # cleanup. GNBCreator does not navigate again after observing READY.
        try:
            current = urlparse(self.dom.page.url)
        except Exception:
            current = urlparse("")
        if current.hostname != parsed.hostname or current.path != parsed.path:
            self.dom.page.goto(job.notebook_url, wait_until="domcontentloaded")

    def inspect_status(self, job: Job) -> str:
        self._open_job(job)
        # GNBCreator's remote recovery observes every two seconds for up to
        # sixty seconds after navigation. This is essential because Angular's
        # artifact cards mount well after ``domcontentloaded``.
        deadline = time.monotonic() + min(self.dom.timeout_ms, 60_000) / 1000
        status = RemoteVideoStatus.UNKNOWN
        while time.monotonic() < deadline:
            status = self.dom.inspect_status()
            if status not in {
                RemoteVideoStatus.NOT_STARTED,
                RemoteVideoStatus.UNKNOWN,
            }:
                return status.value
            self.dom.page.wait_for_timeout(2_000)
        return status.value

    def download_artifact(self, job: Job, destination: Path) -> Path:
        self._open_job(job)
        return self.dom.download_artifact(job.script_name, destination)

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None:
        self._open_job(job)
        self.dom.delete_video_artifact(job.script_name, gate)
