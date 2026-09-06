from __future__ import annotations

import os
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from djd_maker.core.interfaces import HlsResult, MediaResult
from djd_maker.core.models import DownloadSafetyGate, Job, JobState
from djd_maker.core.repositories import JobRepository
from djd_maker.core.settings import AppSettings
from djd_maker.gui.controller import AsyncControllerBridge
from djd_maker.gui.main_window import MainWindow
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler


def _full_gate() -> DownloadSafetyGate:
    return DownloadSafetyGate(
        **{item.name: True for item in fields(DownloadSafetyGate)}
    )


class RecoveryNotebook:
    def __init__(self, fixture: Path, *, status: str = "READY") -> None:
        self.fixture = fixture
        self.status = status
        self.submit_calls: list[str] = []
        self.inspect_calls: list[str] = []
        self.download_calls: list[str] = []
        self.delete_calls: list[str] = []

    def submit(self, job: Job):
        self.submit_calls.append(job.id)
        raise AssertionError("recovery must not create a Notebook or request a video")

    def inspect_status(self, job: Job) -> str:
        self.inspect_calls.append(job.id)
        return self.status

    def download_artifact(self, job: Job, destination: Path) -> Path:
        self.download_calls.append(job.id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.fixture.read_bytes())
        return destination

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None:
        assert gate.remote_deletion_allowed
        self.delete_calls.append(job.id)


class RecoveryRawStore:
    def save(self, source: Path, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        media = MediaResult(destination, 1.0, destination.stat().st_size)
        return SimpleNamespace(media=media, safety_gate=_full_gate())

    def verify_existing(self, source: Path, destination: Path):
        assert source.read_bytes() == destination.read_bytes()
        media = MediaResult(destination, 1.0, destination.stat().st_size)
        return SimpleNamespace(media=media, safety_gate=_full_gate())


class RecoveryEnding:
    def process(
        self,
        raw_video: Path,
        ending_video: Path,
        output_path: Path,
        padding_seconds: float,
    ) -> MediaResult:
        assert padding_seconds == 0.5
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(raw_video.read_bytes() + ending_video.read_bytes())
        return MediaResult(output_path, 2.0, output_path.stat().st_size)


class RecoveryHls:
    def convert_validate_and_zip(self, video: Path, output_zip: Path) -> HlsResult:
        hls_directory = output_zip.parent / ".hls-recovery" / output_zip.stem
        hls_directory.mkdir(parents=True, exist_ok=True)
        playlist = hls_directory / "playlist.m3u8"
        segment = hls_directory / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n", encoding="utf-8")
        segment.write_bytes(video.read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "w", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(hls_directory, playlist, (segment,), output_zip)


class RecoveryValidator:
    def validate(self, path: Path):
        return SimpleNamespace(valid=path.is_file() and path.stat().st_size > 0)


def _recovery_pipeline(
    tmp_path: Path, jobs: JobRepository, notebook: RecoveryNotebook
) -> PipelineCoordinator:
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"ending")
    return PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RecoveryRawStore(),
        ending=RecoveryEnding(),
        hls=RecoveryHls(),
        validator=RecoveryValidator(),
        paths=PipelinePaths(
            tmp_path / "raw_files",
            tmp_path / "output",
            tmp_path / "work",
            ending,
        ),
    )


def _reserved_job(tmp_path: Path, reset_at: datetime) -> Job:
    source = tmp_path / "input" / "予約授業.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("台本", encoding="utf-8")
    return Job(
        str(source),
        id="reserved-job",
        state=JobState.RESERVED_WAITING_CREDIT_RESET,
        notebook_id="notebook-1",
        notebook_url="https://notebook.google.com/notebook/notebook-1",
        credit_state="CREDIT_EXHAUSTED",
        credit_percent=0,
        credit_reset_at=reset_at.isoformat(),
        reservation_created_at=(reset_at - timedelta(hours=1)).isoformat(),
        expected_generation_after=reset_at.isoformat(),
        last_checked_at=(reset_at - timedelta(minutes=30)).isoformat(),
        artifact_status="SCHEDULED_REMOTE",
        download_status="PENDING",
        raw_status="PENDING",
        recovery_retry_count=2,
    )


def test_recovery_job_json_survives_restart_with_full_stack_metadata(tmp_path: Path) -> None:
    reset_at = datetime(2026, 9, 8, 14, 30, tzinfo=UTC)
    repository = JobRepository(tmp_path / "system" / "jobs")
    expected = _reserved_job(tmp_path, reset_at)
    repository.save(expected)

    restarted = JobRepository(tmp_path / "system" / "jobs")
    restored = restarted.load_recoverable()

    assert len(restored) == 1
    actual = restored[0]
    assert actual.state is JobState.RESERVED_WAITING_CREDIT_RESET
    assert actual.notebook_url == expected.notebook_url
    assert actual.credit_reset_at == expected.credit_reset_at
    assert actual.reservation_created_at == expected.reservation_created_at
    assert actual.expected_generation_after == expected.expected_generation_after
    assert actual.last_checked_at == expected.last_checked_at
    assert actual.artifact_status == "SCHEDULED_REMOTE"
    assert actual.download_status == "PENDING"
    assert actual.raw_status == "PENDING"
    assert actual.recovery_retry_count == 2
    assert len(list((tmp_path / "system" / "jobs").glob("*.json"))) == 1


def test_recovery_before_credit_reset_is_a_remote_noop(tmp_path: Path) -> None:
    now = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    repository = JobRepository(tmp_path / "jobs")
    repository.save(_reserved_job(tmp_path, now + timedelta(hours=2)))
    notebook = RecoveryNotebook(fixture)

    processed = _recovery_pipeline(tmp_path, repository, notebook).run_recovery_cycle(
        now=now
    )

    assert processed == []
    assert notebook.submit_calls == []
    assert notebook.inspect_calls == []
    assert notebook.download_calls == []
    assert repository.require("reserved-job").state is JobState.RESERVED_WAITING_CREDIT_RESET


def test_recovery_after_reset_checks_existing_artifact_and_finishes_without_duplicates(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    repository = JobRepository(tmp_path / "jobs")
    repository.save(_reserved_job(tmp_path, now - timedelta(minutes=30)))
    notebook = RecoveryNotebook(fixture)
    pipeline = _recovery_pipeline(tmp_path, repository, notebook)

    assert pipeline.run_recovery_cycle(now=now) == ["reserved-job"]
    completed = repository.require("reserved-job")
    assert completed.state is JobState.COMPLETED
    assert completed.artifact_status == "READY"
    assert completed.download_status == "DOWNLOADED"
    assert completed.raw_status == "READY"
    assert Path(completed.raw_path or "").is_file()
    assert Path(completed.zip_path or "").is_file()
    assert notebook.submit_calls == []
    assert notebook.inspect_calls == ["reserved-job"]
    assert notebook.download_calls == ["reserved-job"]

    assert pipeline.run_recovery_cycle(now=now + timedelta(minutes=1)) == []
    assert notebook.submit_calls == []
    assert notebook.inspect_calls == ["reserved-job"]
    assert notebook.download_calls == ["reserved-job"]


class MemorySettings:
    def __init__(self, value: AppSettings) -> None:
        self.value = value

    def load(self) -> AppSettings:
        return self.value

    def save(self, settings: AppSettings) -> None:
        self.value = settings


class MemoryJobs:
    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs

    def list(self) -> list[Job]:
        return self.jobs


class WindowController:
    def reload(self):
        return []

    def start(self):
        return None

    def recover_pending(self):
        return None

    def refresh_credit(self):
        return {
            "credit_state": "CREDIT_UNKNOWN",
            "credit_percent": None,
            "credit_reset_at": None,
        }

    def pause(self):
        return None

    def stop(self):
        return None

    def login(self):
        return None

    def retry(self, job_id: str, stage: str):
        return None

    def shutdown(self):
        return None


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path, jobs: list[Job]) -> MainWindow:
    _app()
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"ending")
    settings = MemorySettings(AppSettings(ending_video=str(ending)))
    bridge = AsyncControllerBridge(WindowController())
    return MainWindow(
        app_root=tmp_path,
        settings_repository=settings,
        job_repository=MemoryJobs(jobs),
        controller=bridge,
    )


@pytest.mark.parametrize(
    "state",
    [
        JobState.RESERVED_WAITING_CREDIT_RESET,
        JobState.WAITING_VIDEO,
        JobState.DOWNLOAD_PENDING,
        JobState.RECOVERY_PENDING,
    ],
)
def test_recover_button_is_enabled_only_for_pending_recovery_states(
    tmp_path: Path, state: JobState
) -> None:
    window = _window(tmp_path, [Job("pending.txt", state=state)])
    assert window.recover_button.isEnabled()
    window.close()


def test_recover_button_excludes_completed_jobs(tmp_path: Path) -> None:
    window = _window(tmp_path, [Job("done.txt", state=JobState.COMPLETED)])
    assert not window.recover_button.isEnabled()
    window.close()


def test_credit_ui_shows_unknown_and_exact_percent(tmp_path: Path) -> None:
    window = _window(tmp_path, [])

    window._apply_runtime_status(
        {
            "credit_state": "CREDIT_UNKNOWN",
            "credit_percent": None,
            "credit_reset_at": None,
        }
    )
    assert window.credit_state_label.text() == "取得不可  /  CREDIT_UNKNOWN"
    assert window.credit_percent_label.text() == "クレジット残量: 取得不可"
    assert window.credit_reset_label.text() == "リセット予定: －"

    reset_at = "2026-09-08T14:30:00+09:00"
    window._apply_runtime_status(
        {
            "credit_state": "CREDIT_AVAILABLE",
            "credit_percent": 72,
            "credit_reset_at": reset_at,
        }
    )
    assert window.credit_state_label.text() == "利用可能  /  CREDIT_AVAILABLE"
    assert window.credit_percent_label.text() == "クレジット残量: 72%"
    assert window.credit_reset_label.text() == f"リセット予定: {reset_at}"
    window.close()


class ControllerRecoveryPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def run_recovery_cycle(self) -> list[str]:
        self.calls += 1
        return ["pending"]


def test_controller_recovery_counts_only_pending_and_runs_one_recovery_cycle(
    tmp_path: Path,
) -> None:
    pending = Job("pending.txt", id="pending", state=JobState.WAITING_VIDEO)
    completed = Job("done.txt", id="done", state=JobState.COMPLETED)
    jobs = MemoryJobs([pending, completed])
    recovery = ControllerRecoveryPipeline()
    cleanup_calls: list[None] = []
    controller = GuiPipelineController(
        jobs=jobs,
        settings=AppSettings(input_directory="input"),
        app_root=tmp_path,
        pipeline=SimpleNamespace(scheduler=None),
        scheduler=PersistentPollScheduler(jobs),
        recovery_pipeline_factory=lambda: recovery,
        cleanup=lambda: cleanup_calls.append(None),
    )
    published: list[object] = []
    controller.bind(
        jobs=published.append,
        status=lambda _status: None,
        log=lambda _record: None,
        error=lambda _operation, _message: None,
    )

    result = controller.recover_pending()

    assert result == {"pending": 1, "checked": 1}
    assert recovery.calls == 1
    assert len(cleanup_calls) == 1
    assert published == [[pending, completed]]
    assert controller.status()["phase"] == "idle"
