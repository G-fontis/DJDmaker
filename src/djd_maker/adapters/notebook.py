from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from djd_maker.core.interfaces import require_remote_deletion_gate
from djd_maker.core.models import DownloadSafetyGate, Job
from djd_maker.media.validator import VideoValidator


class NotebookAdapterError(RuntimeError):
    pass


class DomMismatchError(NotebookAdapterError):
    pass


class ArtifactDeletionDisabled(NotebookAdapterError):
    pass


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
class ArtifactDeleteSelectors:
    """実画面で動画artifact専用と確認できたselectorだけを設定する。"""

    more_button_name: str
    delete_menu_name: str
    confirmation_dialog_name: str
    confirm_button_name: str


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
            if keeper is not None:
                try:
                    keeper.close()
                except Exception:
                    pass


CREATE_NOTEBOOK = (
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
SOURCE_READY = (
    ("text", "", "1 件のソース"),
    ("css", "button.source-stretched-button", ""),
    ("text", "", "1 source"),
    ("css", "[data-testid*='source'][aria-busy='false']", ""),
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
VIDEO_GENERATE = (
    ("role", "button", "生成"),
    ("role", "button", "Generate"),
)


class NotebookDomAdapter:
    """NotebookLM DOM adapter。Notebookを削除するAPIは意図的に持たない。"""

    HOME_URL = "https://notebook.google.com/"
    ACTIVE_MARKERS = ("動画解説を生成しています", "生成中", "generating", "preparing")
    WAITING_MARKERS = ("スケジュール設定されています", "scheduled for later")
    FAILED_MARKERS = ("動画解説を生成できませんでした", "failed to generate video")

    def __init__(
        self,
        page: Any,
        *,
        download_handoff: DownloadHandoff | None = None,
        artifact_delete_selectors: ArtifactDeleteSelectors | None = None,
        timeout_ms: int = 30_000,
        diagnostic: Callable[[str], None] | None = None,
    ) -> None:
        self.page = page
        self.download_handoff = download_handoff
        self.artifact_delete_selectors = artifact_delete_selectors
        self.timeout_ms = timeout_ms
        self.diagnostic = diagnostic or (lambda _reason: None)

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
        for candidate in candidates:
            try:
                locator = self._locator(*candidate)
                if locator.count() and locator.first.is_visible(timeout=300):
                    return locator.first
            except Exception:
                continue
        self.diagnostic(f"DOM_MISMATCH:{name}")
        raise DomMismatchError(f"{name}を特定できません")

    def create_notebook(self) -> ResumeMetadata:
        self.page.goto(self.HOME_URL, wait_until="domcontentloaded")
        self._first_visible(CREATE_NOTEBOOK, "Notebook作成ボタン").click()
        self.page.wait_for_url("**/notebook/**", timeout=self.timeout_ms)
        url = self.page.url
        parsed = urlparse(url)
        notebook_id = Path(parsed.path).name
        if parsed.hostname != "notebook.google.com" or not notebook_id:
            raise DomMismatchError("作成後Notebook URLを確認できません")
        return ResumeMetadata(notebook_id, url, "")

    def rename_notebook(self, title: str) -> None:
        expected = " ".join(title.split())
        if not expected:
            raise ValueError("Notebook名は空にできません")
        editor = self._first_visible(TITLE_INPUT, "Notebookタイトル入力")
        editor.fill(title)
        editor.press("Enter")
        actual = " ".join(editor.input_value().split())
        page_title = " ".join(self.page.title().split())
        if actual != expected or not (
            page_title == expected or page_title.startswith(expected + " - ")
        ):
            self.diagnostic("DOM_MISMATCH:notebook_title_readback")
            raise DomMismatchError("Notebook名の確定をreadbackできません")

    def upload_txt(self, source_path: Path) -> None:
        source = source_path.resolve()
        if not source.is_file() or source.suffix.casefold() != ".txt":
            raise FileNotFoundError(f"有効なTXTではありません: {source}")
        self._first_visible(FILE_INPUT, "TXT file input").set_input_files(str(source))

    def wait_for_source_ready(self) -> None:
        deadline = time.monotonic() + self.timeout_ms / 1000
        while time.monotonic() < deadline:
            for candidate in SOURCE_READY:
                try:
                    locator = self._locator(*candidate)
                    if locator.count() and locator.first.is_visible(timeout=300):
                        return
                except Exception:
                    continue
            self.page.wait_for_timeout(500)
        self.diagnostic("DOM_MISMATCH:source_ready_timeout")
        raise DomMismatchError("TXT sourceの解析完了を確認できません")

    def start_video_generation(self) -> None:
        self._first_visible(VIDEO_CREATE, "Video Overview作成").click()
        self._first_visible(VIDEO_GENERATE, "動画生成ボタン").click()

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
        cards = self.page.locator("artifact-library-item").filter(has_text=artifact_title)
        if cards.count() != 1:
            self.diagnostic("DOM_MISMATCH:download_artifact_not_unique")
            raise DomMismatchError("download対象動画artifactを一意に特定できません")
        return self.download_handoff(self.page, cards.first, destination)

    def delete_video_artifact(
        self, artifact_title: str, gate: DownloadSafetyGate
    ) -> None:
        require_remote_deletion_gate(gate)
        selectors = self.artifact_delete_selectors
        if selectors is None:
            raise ArtifactDeletionDisabled(
                "動画artifact専用削除selectorが実画面で未確認のため削除しません"
            )
        cards = self.page.locator("artifact-library-item").filter(has_text=artifact_title)
        if cards.count() != 1:
            raise DomMismatchError("削除対象動画artifactを一意に特定できません")
        card = cards.first
        card.get_by_role(
            "button", name=selectors.more_button_name, exact=True
        ).click()
        self.page.get_by_role(
            "menuitem", name=selectors.delete_menu_name, exact=True
        ).click()
        dialog = self.page.get_by_role(
            "dialog", name=selectors.confirmation_dialog_name, exact=True
        )
        dialog.wait_for(state="visible", timeout=self.timeout_ms)
        dialog.get_by_role(
            "button", name=selectors.confirm_button_name, exact=True
        ).click()
        cards.wait_for(state="detached", timeout=self.timeout_ms)


class NotebookEngineAdapter:
    """DOM操作をpipeline用のjob単位interfaceへまとめる。"""

    def __init__(self, dom: NotebookDomAdapter) -> None:
        self.dom = dom

    def submit(self, job: Job) -> tuple[str, str]:
        metadata = self.dom.create_notebook()
        self.dom.upload_txt(Path(job.source_path))
        self.dom.wait_for_source_ready()
        self.dom.rename_notebook(job.script_name)
        self.dom.start_video_generation()
        return metadata.notebook_id, metadata.notebook_url

    def _open_job(self, job: Job) -> None:
        if not job.notebook_url:
            raise NotebookAdapterError("jobにNotebook URLがありません")
        parsed = urlparse(job.notebook_url)
        if parsed.scheme != "https" or parsed.hostname != "notebook.google.com":
            raise NotebookAdapterError("jobのNotebook URLが不正です")
        self.dom.page.goto(job.notebook_url, wait_until="domcontentloaded")

    def inspect_status(self, job: Job) -> str:
        self._open_job(job)
        return self.dom.inspect_status().value

    def download_artifact(self, job: Job, destination: Path) -> Path:
        self._open_job(job)
        return self.dom.download_artifact(job.script_name, destination)

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None:
        self._open_job(job)
        self.dom.delete_video_artifact(job.script_name, gate)
