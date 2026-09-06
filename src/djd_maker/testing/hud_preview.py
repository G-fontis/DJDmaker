"""Render deterministic Phase 2 HUD previews without production sample data."""

from __future__ import annotations

import argparse
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.settings import AppSettings
from djd_maker.gui.controller import AsyncControllerBridge
from djd_maker.gui.dialogs import JobDetailDialog, LogDialog, PresetDialog, SettingsDialog
from djd_maker.gui.hud import HUD_STYLESHEET
from djd_maker.gui.main_window import MainWindow


class _MemorySettings:
    def __init__(self, value: AppSettings) -> None:
        self.value = value

    def load(self) -> AppSettings:
        return self.value

    def save(self, value: AppSettings) -> None:
        self.value = value


class _MemoryJobs:
    def __init__(self, values: list[Job]) -> None:
        self.values = values

    def list(self) -> list[Job]:
        return list(self.values)


class _MemoryPresets:
    def __init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.values = [
            Preset("preview-a", "標準授業", "日本語の説明動画を生成", now, now),
            Preset("preview-b", "ペーパークラフト", "日本語・ペーパークラフト", now, now),
        ]
        self.selected_id: str | None = "preview-a"

    def list(self) -> list[Preset]:
        return list(self.values)

    def selected(self) -> Preset | None:
        return next((item for item in self.values if item.id == self.selected_id), None)

    def select(self, preset_id: str | None) -> None:
        self.selected_id = preset_id

    def create(self, name: str, prompt_text: str) -> Preset:
        now = datetime.now(UTC).isoformat()
        value = Preset(f"preview-{len(self.values) + 1}", name, prompt_text, now, now)
        self.values.append(value)
        return value

    def update(self, preset_id: str, name: str, prompt_text: str) -> Preset:
        current = next(item for item in self.values if item.id == preset_id)
        updated = replace(current, name=name, prompt_text=prompt_text)
        self.values[self.values.index(current)] = updated
        return updated

    def delete(self, preset_id: str) -> None:
        self.values = [item for item in self.values if item.id != preset_id]
        if self.selected_id == preset_id:
            self.selected_id = None

    def duplicate(self, preset_id: str) -> Preset:
        source = next(item for item in self.values if item.id == preset_id)
        return self.create(f"{source.name} のコピー", source.prompt_text)


class _PreviewController:
    def reload(self) -> list[Job]:
        return []

    def start(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def login(self) -> None: ...

    def recover_pending(self) -> None: ...

    def refresh_credit(self) -> None: ...

    def retry(self, _job_id: str, _stage: str) -> None: ...

    def shutdown(self) -> None: ...


def _sample_jobs() -> list[Job]:
    return [
        Job(
            "01_数と式の基礎.txt",
            state=JobState.HLS_ENCODING,
            raw_path="raw/01.mp4",
            edited_path="work/01_edited.mp4",
            progress_percent=68,
            generation_started_at="2026-09-07T14:10:00+09:00",
        ),
        Job(
            "02_二次関数.txt",
            state=JobState.COMPLETED,
            raw_path="raw/02.mp4",
            edited_path="work/02_edited.mp4",
            zip_path="output/02.zip",
            progress_percent=100,
            generation_started_at="2026-09-07T13:50:00+09:00",
        ),
        Job("03_図形の性質.txt", state=JobState.WAITING, progress_percent=0),
        Job(
            "04_化学反応とエネルギー.txt",
            state=JobState.RESERVED_WAITING_CREDIT_RESET,
            credit_state="CREDIT_EXHAUSTED",
            credit_percent=0,
            credit_reset_at="2026-09-08T14:30:00+09:00",
            progress_percent=22,
        ),
        Job(
            "05_現代社会と経済.txt",
            state=JobState.RECOVERY_PENDING,
            progress_percent=35,
            recovery_retry_count=1,
        ),
        Job(
            "06_英語長文読解.txt",
            state=JobState.FAILED,
            progress_percent=12,
            error_code="PREVIEW_FAILURE",
            error_message="再試行可能な表示例",
        ),
    ]


def _capture(widget, destination: Path, size: tuple[int, int] | None = None) -> None:  # type: ignore[no-untyped-def]
    if size is not None:
        widget.resize(*size)
    widget.show()
    QApplication.processEvents()
    if not widget.grab().save(str(destination), "PNG"):
        raise RuntimeError(f"SCREENSHOT_SAVE_FAILED: {destination}")
    widget.hide()


def render_previews(output_directory: Path) -> list[Path]:
    """Render all visual-acceptance images from isolated, in-memory sample state."""
    output_directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(HUD_STYLESHEET)
    results: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="djd-hud-preview-") as temporary:
        root = Path(temporary)
        ending = root / "ending.mp4"
        ending.write_bytes(b"preview-only")
        settings = AppSettings(
            input_directory=str(root / "台本 入力"),
            raw_directory=str(root / "RAW 保管"),
            output_directory=str(root / "完成 ZIP"),
            ending_video=str(ending),
        )
        for directory in (
            Path(settings.input_directory),
            Path(settings.raw_directory),
            Path(settings.output_directory),
        ):
            directory.mkdir(parents=True, exist_ok=True)
        jobs = _sample_jobs()
        presets = _MemoryPresets()
        pool = QThreadPool()
        bridge = AsyncControllerBridge(_PreviewController(), thread_pool=pool)
        window = MainWindow(
            app_root=root,
            settings_repository=_MemorySettings(settings),
            job_repository=_MemoryJobs(jobs),
            controller=bridge,
            preset_repository=presets,
        )
        window.set_jobs(jobs)
        window._apply_runtime_status(
            {
                "credit_state": "CREDIT_AVAILABLE",
                "credit_percent": 72,
                "credit_reset_at": None,
                "next_check": "14:35",
            }
        )
        for record in (
            {"timestamp": "14:28:12", "level": "INFO", "message": "[HLS] セグメント処理を開始しました"},
            {"timestamp": "14:28:10", "level": "INFO", "message": "[End処理] 最終音声＋0.5秒を確認しました"},
            {"timestamp": "14:27:58", "level": "WARNING", "message": "[Credit] 予約ジョブは回復時刻まで安全に待機します"},
            {"timestamp": "14:27:54", "level": "INFO", "message": "Google認証とPre-flightを確認しました"},
        ):
            window._append_log_record(record)
        for name, size in (
            ("main_1920x1080.png", (1920, 1080)),
            ("main_1600x900.png", (1600, 900)),
            ("main_1280x720.png", (1280, 720)),
        ):
            destination = output_directory / name
            _capture(window, destination, size)
            results.append(destination)

        settings_dialog = SettingsDialog(settings, preset_repository=presets)
        destination = output_directory / "settings.png"
        _capture(settings_dialog, destination, (820, 650))
        results.append(destination)

        preset_dialog = PresetDialog(
            name="ペーパークラフト授業",
            prompt_text="ソース読込後、日本語のペーパークラフト形式で説明動画を生成してください。",
        )
        destination = output_directory / "preset.png"
        _capture(preset_dialog, destination, (720, 520))
        results.append(destination)

        detail_dialog = JobDetailDialog(jobs[3])
        destination = output_directory / "job_detail.png"
        _capture(detail_dialog, destination, (820, 820))
        results.append(destination)

        log_dialog = LogDialog()
        for record in (
            {"timestamp": "14:30:01", "job_id": jobs[0].id, "engine": "HLS", "stage": "encode", "level": "INFO", "message": "6秒segmentを検証しました"},
            {"timestamp": "14:30:02", "job_id": jobs[3].id, "engine": "GNB", "stage": "credit", "level": "WARNING", "message": "Credit回復待ちとして予約済みです"},
            {"timestamp": "14:30:03", "job_id": jobs[5].id, "engine": "GNB", "stage": "recovery", "level": "ERROR", "message": "再試行可能です"},
        ):
            log_dialog.append_record(record)
        destination = output_directory / "log.png"
        _capture(log_dialog, destination, (1200, 650))
        results.append(destination)

        window._apply_runtime_status(
            {
                "credit_state": "CREDIT_EXHAUSTED",
                "credit_percent": 0,
                "credit_reset_at": "2026-09-08T14:30:00+09:00",
                "next_check": "14:35",
            }
        )
        destination = output_directory / "credit_exhausted.png"
        _capture(window, destination, (1600, 900))
        results.append(destination)

        window.job_table.selectRow(4)
        window.set_jobs([jobs[4], *jobs[:4], jobs[5]])
        destination = output_directory / "reservation_recovery.png"
        _capture(window, destination, (1600, 900))
        results.append(destination)

        window.close()
        pool.waitForDone(1000)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for path in render_previews(args.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
