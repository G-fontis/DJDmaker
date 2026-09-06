from dataclasses import fields
from pathlib import Path

import pytest

from djd_maker.adapters.notebook import (
    ArtifactDeleteSelectors,
    ArtifactDeletionDisabled,
    ArtifactDeletionRetryableError,
    DomMismatchError,
    NotebookDomAdapter,
    NotebookEngineAdapter,
    PlaywrightArtifactDownload,
    PresetApplyMismatchError,
    RemoteVideoStatus,
    ResumeMetadata,
)
from djd_maker.core.interfaces import RemoteDeletionDenied
from djd_maker.core.models import DownloadSafetyGate, preset_body_sha256


# Minimal, secret-free extraction from GNBCreator's saved live diagnostic:
# diagnostics/20260905_101556/STATE_10_VIDEO_MENU/{elements.json,accessibility.txt}
LIVE_ARTIFACT_MENU_FIXTURE = (
    ("button", "再生", "artifact-action-button"),
    ("button", "その他", "artifact-more-button"),
    ("menuitem", "共有", "mat-mdc-menu-item"),
    ("menuitem", "名前を変更", "mat-mdc-menu-item"),
    ("menuitem", "ダウンロード", "mat-mdc-menu-item"),
    ("menuitem", "プロンプトとソースを表示", "mat-mdc-menu-item"),
    ("menuitem", "削除", "mat-mdc-menu-item"),
)


def test_verified_defaults_match_minimal_live_artifact_menu_fixture():
    role_names = {(role, name) for role, name, _css_class in LIVE_ARTIFACT_MENU_FIXTURE}
    defaults = ArtifactDeleteSelectors()
    assert ("button", defaults.more_button_names[0]) in role_names
    assert ("menuitem", defaults.artifact_menu_markers[0]) in role_names
    assert ("menuitem", defaults.delete_menu_names[0]) in role_names


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


class PageMustNotBeUsedForDelete:
    def __getattr__(self, name):
        raise AssertionError(f"page must not be used before safety gate: {name}")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ready", RemoteVideoStatus.READY),
        ("ショート動画の概要を生成しています...", RemoteVideoStatus.GENERATING),
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


@pytest.mark.parametrize("failed_field", [field.name for field in fields(DownloadSafetyGate)])
def test_each_of_twelve_safety_checks_blocks_artifact_delete(failed_field):
    values = {field.name: True for field in fields(DownloadSafetyGate)}
    values[failed_field] = False
    page = PageMustNotBeUsedForDelete()
    with pytest.raises(RemoteDeletionDenied):
        NotebookDomAdapter(page).delete_video_artifact(
            "lecture", DownloadSafetyGate(**values)
        )


def test_delete_is_disabled_until_artifact_only_controls_are_verified():
    adapter = NotebookDomAdapter(
        Page(Locator(text="ready")), artifact_delete_selectors=None
    )
    with pytest.raises(ArtifactDeletionDisabled):
        adapter.delete_video_artifact("lecture", complete_gate())


def test_adapter_exposes_no_notebook_delete_api():
    adapter = NotebookDomAdapter(Page(Locator()))
    assert not hasattr(adapter, "delete_notebook")


class DeleteNode:
    def __init__(self, *, visible=True, text="", on_click=None):
        self.visible = visible
        self.text = text
        self.on_click = on_click

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def count(self):
        return int(self.visible)

    def is_visible(self, **_kwargs):
        return self.visible

    def inner_text(self):
        return self.text

    def click(self):
        if self.on_click:
            self.on_click()


class DeleteCard(DeleteNode):
    def __init__(self, page, title, *, playable=True):
        super().__init__(text=title)
        self.page = page
        self.title = title
        self.playable = playable

    def get_by_role(self, role, *, name, exact):
        assert exact
        if role == "button" and name in ("再生", "Play"):
            return DeleteNode(visible=self.visible and self.playable)
        if role == "button" and name in ("その他", "More"):
            return DeleteNode(
                visible=self.visible and name == self.page.more_name,
                on_click=self.page._open_menu,
            )
        return DeleteNode(visible=False)


class DeleteCards(DeleteNode):
    def __init__(self, page, cards):
        self.page = page
        self.cards = cards

    @property
    def first(self):
        return self.cards[0]

    def nth(self, index):
        return self.cards[index]

    def count(self):
        return len(self.cards)

    def filter(self, *, has_text):
        self.page.events.append(("scope", has_text))
        return DeleteCards(
            self.page,
            [card for card in self.cards if has_text in card.title],
        )


class ArtifactDeletePage:
    def __init__(
        self,
        titles=("lecture",),
        *,
        confirmation=None,
        more_name="その他",
        delete_name="削除",
        toast=False,
    ):
        self.events = []
        self.more_name = more_name
        self.delete_name = delete_name
        self.confirmation = confirmation
        self.dialog_visible = False
        self.toast = toast
        self.menu_open = False
        self.reappear_on_reload = False
        self.server_deleted = False
        self.artifact_marker_available = True
        self.cards = [DeleteCard(self, title) for title in titles]

    def locator(self, selector):
        assert selector == "artifact-library-item"
        return DeleteCards(self, self.cards)

    def _remove(self):
        self.events.append("removed")
        for card in self.cards:
            card.visible = False

    def _open_menu(self):
        self.events.append("more")
        self.menu_open = True

    def get_by_role(self, role, **_kwargs):
        if role == "menu":
            page = self

            class Menu(DeleteNode):
                def count(self): return int(page.menu_open)
                def is_visible(self, **_kwargs): return page.menu_open
                def get_by_role(self, item_role, *, name, exact):
                    assert item_role == "menuitem" and exact
                    return DeleteNode(
                        visible=(
                            name == page.delete_name
                            or (
                                page.artifact_marker_available
                                and name in ("ダウンロード", "Download")
                            )
                        ),
                        on_click=(page._delete_clicked if name == page.delete_name else None),
                    )

            return Menu()
        if role == "dialog":
            page = self

            class Dialog(DeleteNode):
                def count(self): return int(page.dialog_visible)
                def is_visible(self, **_kwargs): return page.dialog_visible
                def inner_text(self): return page.confirmation or ""
                def get_by_role(self, item_role, *, name, exact):
                    assert item_role == "button" and exact
                    return DeleteNode(
                        visible=name == page.delete_name,
                        on_click=page._remove,
                    )

            return Dialog()
        return DeleteNode(visible=False)

    def _delete_clicked(self):
        self.events.append("delete")
        if self.confirmation is None:
            self._remove()
        else:
            self.dialog_visible = True

    def get_by_text(self, _marker, *, exact):
        assert not exact
        return DeleteNode(visible=self.toast)

    def wait_for_timeout(self, _milliseconds):
        pass

    def reload(self, *, wait_until):
        assert wait_until == "domcontentloaded"
        self.events.append("refresh")
        if self.server_deleted:
            for card in self.cards:
                card.visible = False
        if self.reappear_on_reload:
            for card in self.cards:
                card.visible = True


def test_artifact_delete_uses_verified_scoped_sequence_without_dialog():
    page = ArtifactDeletePage()
    NotebookDomAdapter(page).delete_video_artifact("lecture", complete_gate())
    assert page.events == [
        ("scope", "lecture"), "more", "delete", "removed", "refresh",
        ("scope", "lecture"),
    ]


def test_artifact_delete_supports_fallback_names_and_optional_confirmation():
    page = ArtifactDeletePage(
        confirmation="Delete this video?", more_name="More", delete_name="Delete"
    )
    NotebookDomAdapter(page).delete_video_artifact("lecture", complete_gate())
    assert page.events[1:4] == ["more", "delete", "removed"]


def test_artifact_delete_supports_japanese_confirmation():
    page = ArtifactDeletePage(confirmation="この動画を削除しますか？")
    NotebookDomAdapter(page).delete_video_artifact("lecture", complete_gate())
    assert "removed" in page.events


def test_nested_angular_material_dialog_selects_inner_modal():
    class Dialog:
        def __init__(self, modal):
            self.modal = modal

        def count(self):
            return 1

        @property
        def first(self):
            return self

        def is_visible(self, **_kwargs):
            return True

        def get_attribute(self, name):
            assert name == "aria-modal"
            return self.modal

    outer = Dialog("false")
    inner = Dialog("true")

    class Dialogs:
        def count(self):
            return 2

        def nth(self, index):
            return (outer, inner)[index]

    class Page:
        def get_by_role(self, role):
            assert role == "dialog"
            return Dialogs()

    assert NotebookDomAdapter(Page())._visible_dialog() is inner


def test_artifact_delete_refuses_notebook_confirmation():
    page = ArtifactDeletePage(confirmation="このノートブックを削除しますか？")
    diagnostics = []
    adapter = NotebookDomAdapter(page, diagnostic=diagnostics.append)
    with pytest.raises(DomMismatchError, match="Notebook"):
        adapter.delete_video_artifact("lecture", complete_gate())
    assert "removed" not in page.events
    assert diagnostics == ["DELETE_ABORTED:notebook_confirmation"]


def test_artifact_delete_requires_one_playable_target():
    page = ArtifactDeletePage(("lecture", "lecture old"))
    diagnostics = []
    with pytest.raises(DomMismatchError, match="exactly one playable"):
        NotebookDomAdapter(page, diagnostic=diagnostics.append).delete_video_artifact(
            "missing title", complete_gate()
        )
    assert "more" not in page.events
    assert diagnostics == ["DOM_MISMATCH:delete_artifact_not_unique"]


def test_artifact_delete_can_confirm_by_explicitly_configured_toast():
    page = ArtifactDeletePage(toast=False)
    # Simulate a stale card locator despite a successful server-side action.
    def server_delete():
        page.events.append("server-delete")
        page.toast = True
        page.server_deleted = True
    page._remove = server_delete
    selectors = ArtifactDeleteSelectors(success_toast_markers=("動画を削除しました",))
    NotebookDomAdapter(
        page, artifact_delete_selectors=selectors
    ).delete_video_artifact("lecture", complete_gate())
    assert "server-delete" in page.events


def test_wrong_menu_is_rejected_before_delete_click():
    page = ArtifactDeletePage()
    page.artifact_marker_available = False
    with pytest.raises(DomMismatchError, match="Download"):
        NotebookDomAdapter(page).delete_video_artifact("lecture", complete_gate())
    assert "delete" not in page.events
    assert "removed" not in page.events


def test_artifact_must_stay_absent_after_refresh():
    page = ArtifactDeletePage()
    page.reappear_on_reload = True
    diagnostics = []
    with pytest.raises(ArtifactDeletionRetryableError, match="reappeared"):
        NotebookDomAdapter(page, diagnostic=diagnostics.append).delete_video_artifact(
            "lecture", complete_gate()
        )
    assert "refresh" in page.events
    assert diagnostics[-1] == "DELETE_RETRYABLE:artifact_reappeared"


@pytest.mark.parametrize("retryable", [False, True])
def test_raw_survives_remote_delete_and_retry(tmp_path, retryable):
    raw = tmp_path / "raw_files" / "lecture.mp4"
    raw.parent.mkdir()
    original = b"immutable-raw-video"
    raw.write_bytes(original)
    page = ArtifactDeletePage()
    if retryable:
        page._remove = lambda: page.events.append("server-result-unknown")
        with pytest.raises(ArtifactDeletionRetryableError):
            NotebookDomAdapter(page, timeout_ms=1).delete_video_artifact(
                "lecture", complete_gate()
            )
    else:
        NotebookDomAdapter(page).delete_video_artifact("lecture", complete_gate())
    assert raw.read_bytes() == original


def test_unobserved_delete_result_is_retryable():
    page = ArtifactDeletePage()
    page._remove = lambda: page.events.append("server-result-unknown")
    diagnostics = []
    with pytest.raises(ArtifactDeletionRetryableError):
        NotebookDomAdapter(
            page, timeout_ms=1, diagnostic=diagnostics.append
        ).delete_video_artifact("lecture", complete_gate())
    assert diagnostics[-1] == "DELETE_RETRYABLE:success_not_observed"


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


def test_chrome_cdp_download_survives_initiating_page_close(tmp_path):
    class Client:
        def __init__(self):
            self.detached = False

        def on(self, _event, _callback):
            pass

        def send(self, _method, params):
            Path(params["downloadPath"], "video.mp4").write_bytes(b"mp4")

        def detach(self):
            self.detached = True

    client = Client()

    class Context:
        def new_cdp_session(self, _page):
            return client

    class ClosedPage:
        context = Context()

        def is_closed(self):
            return True

    temporary = tmp_path / "download.mp4"
    used = PlaywrightArtifactDownload(object())._download_with_chrome(
        ClosedPage(), Locator(), temporary
    )
    assert used is True
    assert temporary.read_bytes() == b"mp4"
    assert client.detached is True


def test_engine_submit_renames_before_source_and_generation(tmp_path):
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

        def start_video_generation(self, prompt):
            events.append(("generate", prompt))

    from djd_maker.core.models import Job

    prompt = "選択したプリセット本文"
    result = NotebookEngineAdapter(Dom()).submit(
        Job(
            str(source),
            preset_body_snapshot=prompt,
            preset_body_sha256=preset_body_sha256(prompt),
            generation_prompt="ignored legacy prompt",
        )
    )
    assert result[0] == "id"
    assert events == [
        "create",
        ("rename", "SD001_仕事とは"),
        ("upload", source),
        "source_ready",
        ("generate", "選択したプリセット本文"),
    ]


def test_video_generation_fills_selected_preset_before_generate_click():
    events = []
    diagnostics = []

    class Node:
        def __init__(self, name):
            self.name = name
            self.value = ""

        def click(self):
            events.append(("click", self.name))

        def fill(self, value):
            self.value = value
            events.append(("fill", self.name, value))

        def input_value(self):
            return self.value

    adapter = NotebookDomAdapter(object(), diagnostic=diagnostics.append)
    adapter._first_visible = lambda _selectors, name: Node(name)  # type: ignore[method-assign]
    adapter.start_video_generation("プリセットAの本文")
    assert events == [
        ("click", "Video Overview作成"),
        ("fill", "動画解説のカスタムトピック欄", "プリセットAの本文"),
        ("click", "動画生成ボタン"),
    ]
    assert diagnostics == [
        "PRESET_APPLY_EXPECTED:sha256="
        f"{preset_body_sha256('プリセットAの本文')},length=9",
        "PRESET_APPLY_READBACK:sha256="
        f"{preset_body_sha256('プリセットAの本文')},length=9",
    ]


def test_video_generation_readback_mismatch_blocks_generate_click():
    events = []

    class Node:
        def __init__(self, name):
            self.name = name

        def click(self):
            events.append(("click", self.name))

        def fill(self, value):
            events.append(("fill", self.name, value))

        def input_value(self):
            return "different DOM value"

    adapter = NotebookDomAdapter(object())
    adapter._first_visible = lambda _selectors, name: Node(name)  # type: ignore[method-assign]

    with pytest.raises(PresetApplyMismatchError, match="PRESET_APPLY_MISMATCH"):
        adapter.start_video_generation("selected preset snapshot")

    assert not any(event == ("click", "動画生成ボタン") for event in events)


def test_engine_rejects_missing_preset_before_creating_notebook():
    class Dom:
        def create_notebook(self):
            raise AssertionError("Notebook must not be created")

    from djd_maker.adapters.notebook import NotebookAdapterError
    from djd_maker.core.models import Job

    with pytest.raises(NotebookAdapterError, match="PRESET_SNAPSHOT_MISSING"):
        NotebookEngineAdapter(Dom()).submit(Job("lesson.txt"))


def test_engine_status_waits_for_delayed_artifact_dom():
    from djd_maker.core.models import Job

    class DelayedPage:
        url = "https://notebook.google.com/notebook/id"

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000

    class DelayedDom:
        timeout_ms = 60_000
        page = DelayedPage()

        def __init__(self):
            self.states = [RemoteVideoStatus.UNKNOWN, RemoteVideoStatus.READY]

        def inspect_status(self):
            return self.states.pop(0)

    job = Job(
        "lesson.txt",
        notebook_id="id",
        notebook_url="https://notebook.google.com/notebook/id",
    )
    assert NotebookEngineAdapter(DelayedDom()).inspect_status(job) == "READY"


def test_engine_adopts_keeper_page_after_download_closed_origin():
    from djd_maker.core.models import Job

    class Keeper:
        url = "about:blank"

        def is_closed(self):
            return False

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            self.url = url

    keeper = Keeper()

    class Context:
        pages = [keeper]

    class ClosedPage:
        context = Context()

        def is_closed(self):
            return True

    dom = type("Dom", (), {"page": ClosedPage()})()
    engine = NotebookEngineAdapter(dom)
    job = Job(
        "lesson.txt",
        notebook_id="id",
        notebook_url="https://notebook.google.com/notebook/id",
    )
    engine._open_job(job)
    assert dom.page is keeper
    assert keeper.url == job.notebook_url


def test_engine_recovers_closed_context_with_browser_manager_callback():
    from djd_maker.core.models import Job

    class RecoveredPage:
        url = "about:blank"

        def is_closed(self):
            return False

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            self.url = url

    recovered = RecoveredPage()

    class Context:
        pages = []

    class ClosedPage:
        context = Context()

        def is_closed(self):
            return True

    dom = type("Dom", (), {"page": ClosedPage()})()
    engine = NotebookEngineAdapter(dom, recover_page=lambda: recovered)
    job = Job(
        "lesson.txt",
        notebook_id="id",
        notebook_url="https://notebook.google.com/notebook/id",
    )

    engine._open_job(job)

    assert dom.page is recovered
    assert recovered.url == job.notebook_url
