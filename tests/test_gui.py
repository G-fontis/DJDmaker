from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QMessageBox

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.gui.controller import AsyncControllerBridge
from djd_maker.gui.dialogs import JobDetailDialog, LogDialog
from djd_maker.gui.main_window import MainWindow
from djd_maker.gui.viewmodels import LogRecord, sanitize_log_text


class MemorySettings:
    def __init__(self, value: AppSettings) -> None:
        self.value = value
        self.saved: list[AppSettings] = []

    def load(self) -> AppSettings:
        return self.value

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.value = settings
        self.saved.append(settings)


class MemoryJobs:
    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs = jobs or []

    def list(self) -> list[Job]:
        return self.jobs


class FakeController:
    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs = jobs or []
        self.calls: list[object] = []
        self.start_gate: threading.Event | None = None

    def reload(self):
        self.calls.append("reload")
        return self.jobs

    def start(self):
        self.calls.append("start")
        if self.start_gate:
            self.start_gate.wait(2)

    def pause(self):
        self.calls.append("pause")

    def stop(self):
        self.calls.append("stop")

    def retry(self, job_id: str, stage: str):
        self.calls.append(("retry", job_id, stage))

    def shutdown(self):
        self.calls.append("shutdown")
        if self.start_gate:
            self.start_gate.set()


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path, jobs: list[Job], ending: bool = True):
    _app()
    ending_path = tmp_path / "エンディング.mp4"
    if ending:
        ending_path.write_bytes(b"video")
    settings = MemorySettings(
        AppSettings(
            input_directory="入力",
            raw_directory="raw_files",
            output_directory="出力",
            ending_video=str(ending_path) if ending else "",
        )
    )
    controller = FakeController(jobs)
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    bridge = AsyncControllerBridge(controller, thread_pool=pool)
    window = MainWindow(
        app_root=tmp_path,
        settings_repository=settings,
        job_repository=MemoryJobs(jobs),
        controller=bridge,
    )
    return window, settings, controller, bridge


def _drain_until(condition, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    app = _app()
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def test_main_window_has_formal_identity_controls_and_fixed_job_columns(tmp_path: Path) -> None:
    job = Job("日本語の台本.txt", state=JobState.COMPLETED, raw_path="raw.mp4", zip_path="done.zip")
    window, _settings, controller, _bridge = _window(tmp_path, [job])
    assert window.windowTitle() == "台本から授業動画つくるマシーン v0.1"
    assert "GNBCreator" in window.ENGINE_CAPTION
    assert "ドウガッチンガー" in window.ENGINE_CAPTION
    assert "HLS Converter" in window.ENGINE_CAPTION
    assert window.CREDIT == "Created by 福ゼミ塾長"
    assert window.job_table.columnCount() == 6
    assert [window.job_table.horizontalHeaderItem(i).text() for i in range(6)] == list(window.JOB_COLUMNS)
    assert window.job_table.item(0, 1).text() == "日本語の台本"
    assert window.total_label.text() == "全Job: 1"
    assert window.zip_complete_label.text() == "ZIP完了: 1/1"
    assert window.completion_group.isVisibleTo(window)
    window.close()
    assert "shutdown" in controller.calls


def test_ending_not_configured_blocks_start(tmp_path: Path, monkeypatch) -> None:
    window, _settings, controller, _bridge = _window(tmp_path, [], ending=False)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(str(args[2])))
    assert not window.start_button.isEnabled()
    window.start_processing()
    assert "start" not in controller.calls
    assert warnings and "Ending" in warnings[0]
    window.close()


def test_controller_start_is_nonblocking_and_reports_by_signal(tmp_path: Path) -> None:
    window, _settings, controller, bridge = _window(tmp_path, [])
    gate = threading.Event()
    controller.start_gate = gate
    finished: list[str] = []
    bridge.operation_finished.connect(lambda name, _result: finished.append(name))
    before = time.monotonic()
    window.start_processing()
    assert time.monotonic() - before < 0.2
    assert bridge.busy
    gate.set()
    _drain_until(lambda: "start" in finished)
    window.close()


def test_job_detail_enables_only_safe_actions(tmp_path: Path) -> None:
    _app()
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"raw")
    failed = Job(
        "講義.txt",
        state=JobState.FAILED,
        raw_path=str(raw),
        error_code="ENDING_FAILED",
    )
    dialog = JobDetailDialog(failed)
    assert dialog.retry_job_button.isEnabled()
    assert dialog.retry_ending_button.isEnabled()
    assert not dialog.retry_hls_button.isEnabled()
    assert not dialog.redownload_button.isEnabled()
    assert dialog.open_raw_button.isEnabled()
    assert not dialog.open_zip_button.isEnabled()


def test_download_retry_requires_remote_identity() -> None:
    _app()
    no_identity = Job("a.txt", state=JobState.DOWNLOAD_VERIFY_FAILED)
    with_identity = Job(
        "b.txt",
        state=JobState.DOWNLOAD_VERIFY_FAILED,
        notebook_id="id",
        notebook_url="https://notebook.google.com/notebook/id",
    )
    assert not JobDetailDialog(no_identity).redownload_button.isEnabled()
    assert JobDetailDialog(with_identity).redownload_button.isEnabled()


def test_log_sanitization_and_multi_field_filtering() -> None:
    _app()
    text = sanitize_log_text(
        'Authorization: Bearer abc token=secret Cookie: value <input value="private">'
    )
    assert "abc" not in text
    assert "secret" not in text
    assert "private" not in text
    assert text.count("[REDACTED]") >= 3
    dialog = LogDialog()
    dialog.append_record(LogRecord("12:00", "job-a", "HLS", "ZIP", "INFO", "done"))
    dialog.append_record(LogRecord("12:01", "job-b", "Notebook", "upload", "ERROR", "failed"))
    assert dialog.proxy.rowCount() == 2
    dialog.filter_edits[1].setText("hls")
    assert dialog.proxy.rowCount() == 1
    dialog.filter_edits[3].setText("error")
    assert dialog.proxy.rowCount() == 0


def test_settings_are_bound_to_resolved_unicode_paths(tmp_path: Path) -> None:
    window, settings, _controller, _bridge = _window(tmp_path, [])
    assert window.input_path_edit.text() == str((tmp_path / "入力").resolve())
    assert window.output_path_edit.text() == str((tmp_path / "出力").resolve())
    assert window.ending_path_edit.text().endswith("エンディング.mp4")
    assert settings.saved == []
    window.close()
