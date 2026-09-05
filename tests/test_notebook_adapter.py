from dataclasses import fields
from pathlib import Path

import pytest

from djd_maker.adapters.notebook import (
    ArtifactDeletionDisabled,
    NotebookDomAdapter,
    NotebookEngineAdapter,
    PlaywrightArtifactDownload,
    RemoteVideoStatus,
    ResumeMetadata,
)
from djd_maker.core.interfaces import RemoteDeletionDenied
from djd_maker.core.models import DownloadSafetyGate


class Locator:
    def __init__(self, *, count=1, text="", visible=True):
        self._count = count
        self.text = text
        self.visible = visible
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self, **_kwargs):
        return self.visible

    def inner_text(self):
        return self.text

    def get_by_role(self, *_args, **kwargs):
        if kwargs.get("name") == "再生":
            return Locator(count=1 if self.text == "ready" else 0)
        return self

    def filter(self, **_kwargs):
        return self

    def click(self):
        self.clicked = True

    def wait_for(self, **_kwargs):
        pass


class Page:
    def __init__(self, card):
        self.card = card

    def locator(self, selector):
        if selector == "artifact-library-item":
            return self.card
        return Locator(count=0)


def complete_gate():
    return DownloadSafetyGate(**{item.name: True for item in fields(DownloadSafetyGate)})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ready", RemoteVideoStatus.READY),
        ("動画解説を生成しています", RemoteVideoStatus.GENERATING),
        ("スケジュール設定されています", RemoteVideoStatus.WAITING),
        ("動画解説を生成できませんでした", RemoteVideoStatus.FAILED),
    ],
)
def test_status_is_derived_from_artifact_dom(text, expected):
    assert NotebookDomAdapter(Page(Locator(text=text))).inspect_status() is expected


def test_delete_is_rejected_before_safety_gate():
    adapter = NotebookDomAdapter(Page(Locator(text="ready")))
    with pytest.raises(RemoteDeletionDenied):
        adapter.delete_video_artifact("lecture", DownloadSafetyGate())


def test_delete_is_disabled_until_artifact_only_controls_are_verified():
    adapter = NotebookDomAdapter(Page(Locator(text="ready")))
    with pytest.raises(ArtifactDeletionDisabled):
        adapter.delete_video_artifact("lecture", complete_gate())


def test_adapter_exposes_no_notebook_delete_api():
    adapter = NotebookDomAdapter(Page(Locator()))
    assert not hasattr(adapter, "delete_notebook")


def test_playwright_download_validates_before_atomic_publish(tmp_path):
    calls = []

    class Validator:
        def validate(self, path, **kwargs):
            calls.append((Path(path), kwargs))

    class Download:
        def failure(self):
            return None

        def save_as(self, path):
            Path(path).write_bytes(b"downloaded-video")

    class DownloadInfo:
        value = Download()

    class DownloadContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        value = DownloadInfo.value

    class DownloadPage:
        context = None

        def get_by_role(self, *_args, **_kwargs):
            return Locator()

        def expect_download(self, **_kwargs):
            return DownloadContext()

    destination = tmp_path / "lesson.mp4"
    result = PlaywrightArtifactDownload(Validator())(
        DownloadPage(), Locator(), destination
    )
    assert result == destination.resolve()
    assert destination.read_bytes() == b"downloaded-video"
    assert calls[0][1] == {"reject_temporary": False}
    assert calls[-1] == (destination.resolve(), {})


def test_engine_submit_waits_for_source_before_rename_and_generation(tmp_path):
    source = tmp_path / "SD001_仕事とは.txt"
    source.write_text("script", encoding="utf-8")
    events = []

    class Dom:
        def create_notebook(self):
            events.append("create")
            return ResumeMetadata("id", "https://notebook.google.com/notebook/id", "")

        def upload_txt(self, path):
            events.append(("upload", path))

        def wait_for_source_ready(self):
            events.append("source_ready")

        def rename_notebook(self, title):
            events.append(("rename", title))

        def start_video_generation(self):
            events.append("generate")

    from djd_maker.core.models import Job

    result = NotebookEngineAdapter(Dom()).submit(Job(str(source)))
    assert result[0] == "id"
    assert events == [
        "create",
        ("upload", source),
        "source_ready",
        ("rename", "SD001_仕事とは"),
        "generate",
    ]
