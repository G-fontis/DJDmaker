from __future__ import annotations

import os
import time
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from djd_maker.core.interfaces import HlsResult, MediaResult
from djd_maker.core.models import DownloadSafetyGate, Job, JobState, Preset
from djd_maker.core.repositories import PresetRepository
from djd_maker.core.settings import AppSettings
from djd_maker.gui.controller import AsyncControllerBridge
from djd_maker.gui.main_window import MainWindow
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler, SchedulerMode
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from djd_maker.adapters.browser import BrowserAuthenticationRequired


class MemoryJobs:
    def __init__(self, *jobs):
        self.data = {job.id: job for job in jobs}

    def save(self, job):
        self.data[job.id] = Job.from_dict(job.to_dict())

    def get(self, job_id):
        return self.data.get(job_id)

    def list(self):
        return [Job.from_dict(job.to_dict()) for job in self.data.values()]


class Pipeline:
    def __init__(self, jobs):
        self.jobs = jobs
        self.scheduler = None
        self.cycles = 0
        self.retries = []

    def run_cycle(self):
        self.cycles += 1
        for job in self.jobs.list():
            if job.state is JobState.WAITING:
                job.state = JobState.COMPLETED
                job.raw_path = "raw.mp4"
                job.zip_path = "done.zip"
                self.jobs.save(job)

    def retry_download(self, job_id):
        self.retries.append((job_id, "download"))
        return self.jobs.get(job_id)

    def create_retry(self, job_id, restart):
        self.retries.append((job_id, restart))
        return self.jobs.get(job_id)


def controller(tmp_path: Path, *jobs):
    repository = MemoryJobs(*jobs)
    scheduler = PersistentPollScheduler(repository)
    pipeline = Pipeline(repository)
    instance = GuiPipelineController(
        jobs=repository,
        settings=AppSettings(input_directory="入力"),
        app_root=tmp_path,
        pipeline=pipeline,
        scheduler=scheduler,
        manual_login=lambda: 0,
        cycle_interval_seconds=0.01,
    )
    return instance, repository, pipeline, scheduler


def test_reload_discovers_unicode_txt_without_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "入力" / "授業一.txt"
    source.parent.mkdir()
    source.write_text("台本", encoding="utf-8")
    instance, repository, _pipeline, _scheduler = controller(tmp_path)

    assert [job.script_name for job in instance.reload()] == ["授業一"]
    assert [job.script_name for job in instance.reload()] == ["授業一"]
    assert len(repository.list()) == 1


def test_background_pipeline_publishes_state_and_stops_when_complete(tmp_path: Path) -> None:
    job = Job(str(tmp_path / "入力" / "lesson.txt"))
    instance, repository, pipeline, scheduler = controller(tmp_path, job)
    published = []
    statuses = []
    instance.bind(
        jobs=published.append,
        status=statuses.append,
        log=lambda _value: None,
        error=lambda _op, _message: None,
    )

    instance.start()
    deadline = time.monotonic() + 2
    while repository.list()[0].state is not JobState.COMPLETED and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.shutdown()

    assert pipeline.cycles >= 1
    assert repository.list()[0].state is JobState.COMPLETED
    assert published and statuses
    assert scheduler.mode is SchedulerMode.STOPPED


def test_pause_resume_stop_and_retry_mapping(tmp_path: Path) -> None:
    failed = Job("failed.txt", state=JobState.FAILED)
    instance, _repository, pipeline, scheduler = controller(tmp_path, failed)
    instance.start()
    instance.pause()
    assert scheduler.mode is SchedulerMode.PAUSED
    assert instance.status()["paused"]
    instance.start()
    assert scheduler.mode is SchedulerMode.RUNNING
    instance.retry(failed.id, "ending")
    assert pipeline.retries[-1] == (failed.id, JobState.RAW_READY)
    instance.stop()
    assert scheduler.mode is SchedulerMode.STOPPED


def test_unauthenticated_start_closes_automation_and_requests_login(tmp_path: Path) -> None:
    repository = MemoryJobs(Job("lesson.txt"))
    scheduler = PersistentPollScheduler(repository)
    cleanup_calls: list[str] = []
    errors: list[tuple[str, str]] = []

    def pipeline_factory():
        raise BrowserAuthenticationRequired("Googleへのログインが必要です")

    instance = GuiPipelineController(
        jobs=repository,
        settings=AppSettings(),
        app_root=tmp_path,
        pipeline=None,
        pipeline_factory=pipeline_factory,
        cleanup=lambda: cleanup_calls.append("closed"),
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    instance.bind(
        jobs=lambda _value: None,
        status=lambda _value: None,
        log=lambda _value: None,
        error=lambda operation, message: errors.append((operation, message)),
    )
    instance.start()
    deadline = time.monotonic() + 2
    while instance.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert cleanup_calls == ["closed"]
    assert errors == [("startup", "Googleへのログインが必要です")]
    assert scheduler.mode is SchedulerMode.STOPPED
    assert repository.list()[0].state is JobState.WAITING


def test_successful_preflight_discovers_input_without_reload_operation(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "fresh lesson.txt").write_text("lesson", encoding="utf-8")
    repository = MemoryJobs()
    scheduler = PersistentPollScheduler(repository)

    def pipeline_factory():
        return Pipeline(repository)

    instance = GuiPipelineController(
        jobs=repository,
        settings=AppSettings(input_directory="input"),
        app_root=tmp_path,
        pipeline=None,
        pipeline_factory=pipeline_factory,
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    instance.start()
    deadline = time.monotonic() + 2
    while not repository.list() and time.monotonic() < deadline:
        time.sleep(0.01)
    while repository.list()[0].state is not JobState.COMPLETED and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.shutdown()

    assert len(repository.list()) == 1
    assert repository.list()[0].script_name == "fresh lesson"
    assert repository.list()[0].state is JobState.COMPLETED


def test_each_new_start_recomposes_pipeline_for_current_selected_preset(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "first.txt").write_text("first", encoding="utf-8")
    repository = MemoryJobs()
    scheduler = PersistentPollScheduler(repository)
    presets = PresetRepository(tmp_path / "system" / "presets.json")
    selected = presets.create("Selected preset", "preset body A")
    factory_bodies: list[str] = []

    def pipeline_factory():
        factory_bodies.append(presets.require_selected().prompt_text)
        return Pipeline(repository)

    instance = GuiPipelineController(
        jobs=repository,
        settings=AppSettings(input_directory="input"),
        app_root=tmp_path,
        pipeline=None,
        pipeline_factory=pipeline_factory,
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )

    instance.start()
    deadline = time.monotonic() + 2
    while instance.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    presets.update(selected.id, "Selected preset", "preset body B")
    (input_dir / "second.txt").write_text("second", encoding="utf-8")
    instance.start()
    deadline = time.monotonic() + 2
    while instance.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    instance.shutdown()

    assert factory_bodies == ["preset body A", "preset body B"]
    assert all(job.state is JobState.COMPLETED for job in repository.list())


def test_browser_lifecycle_status_is_logged_without_credentials(tmp_path: Path) -> None:
    instance, _repository, _pipeline, _scheduler = controller(tmp_path)
    records: list[object] = []
    instance.browser_status_provider = lambda: {
        "profile_path": "C:/portable/browser/chrome-profile",
        "auth_process_alive": False,
        "auth_pid": None,
        "automation_connected": True,
        "page_count": 2,
        "gemini_page_count": 1,
        "selected_page_url": "https://notebook.google.com/",
        "navigation_result": "navigated-home",
        "authentication_result": "authenticated",
        "preflight_result": "PRE_FLIGHT_READY",
        "preflight_checks": {"auth_chrome_closed": "PASS"},
    }
    instance.bind(
        jobs=lambda _value: None,
        status=lambda _value: None,
        log=records.append,
        error=lambda _operation, _message: None,
    )
    instance.login()
    message = records[0]["message"]
    assert "PRE_FLIGHT_READY" in message and "authentication_result=authenticated" in message
    assert "Cookie" not in message and "token" not in message


class RawStore:
    def save(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        gate = DownloadSafetyGate(
            **{item.name: True for item in fields(DownloadSafetyGate)}
        )
        return SimpleNamespace(
            media=MediaResult(destination, 1.0, destination.stat().st_size),
            safety_gate=gate,
        )

    verify_existing = save


class Ending:
    def process(self, raw, ending, output, padding_seconds):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw.read_bytes() + ending.read_bytes())
        return MediaResult(output, 2.0, output.stat().st_size, 0.5, 1.0)


class Hls:
    def convert_validate_and_zip(self, video, output_zip):
        directory = output_zip.parent / "hls"
        directory.mkdir(parents=True, exist_ok=True)
        playlist = directory / "playlist.m3u8"
        segment = directory / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        segment.write_bytes(video.read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "w", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(directory, playlist, (segment,), output_zip)


class Validator:
    def validate(self, path):
        return SimpleNamespace(valid=Path(path).is_file())


class SettingsRepository:
    def __init__(self, settings):
        self.settings = settings

    def load(self):
        return self.settings

    def save(self, settings):
        self.settings = settings


def test_fake_notebook_runs_through_gui_bridge_to_completed_zip(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "input" / "授業.txt"
    source.parent.mkdir()
    source.write_text("台本", encoding="utf-8")
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"ending")
    job = Job(str(source))
    jobs = MemoryJobs(job)
    notebook = FakeNotebookAdapter({str(source): fixture})
    scheduler = PersistentPollScheduler(
        jobs, first_poll_seconds=1, subsequent_poll_seconds=1
    )
    settings = AppSettings(ending_video=str(ending))
    pipeline = PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RawStore(),
        ending=Ending(),
        hls=Hls(),
        validator=Validator(),
        paths=PipelinePaths(
            tmp_path / "raw_files", tmp_path / "output", tmp_path / "work", ending
        ),
        scheduler=scheduler,
        generation_preset=Preset(
            "test-preset", "Test preset", "test body", "created", "updated"
        ),
    )
    service = GuiPipelineController(
        jobs=jobs,
        settings=settings,
        app_root=tmp_path,
        pipeline=pipeline,
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    pool = QThreadPool()
    bridge = AsyncControllerBridge(service, thread_pool=pool)
    window = MainWindow(
        app_root=tmp_path,
        settings_repository=SettingsRepository(settings),
        job_repository=jobs,
        controller=bridge,
    )

    window.start_processing()
    deadline = time.monotonic() + 3
    while jobs.get(job.id).state is not JobState.COMPLETED and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert jobs.get(job.id).state is JobState.COMPLETED
    assert notebook.artifact_delete_calls == [job.id]
    assert Path(jobs.get(job.id).zip_path or "").is_file()
    window.close()
    pool.waitForDone(2000)
