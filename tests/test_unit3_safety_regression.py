from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

import pytest

from djd_maker.core.interfaces import HlsResult, MediaResult, RemoteDeletionDenied
from djd_maker.core.models import DownloadSafetyGate, Job, JobState
from djd_maker.core.repositories import JobRepository
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler, SchedulerMode


def complete_gate() -> DownloadSafetyGate:
    return DownloadSafetyGate(
        **{item.name: True for item in fields(DownloadSafetyGate)}
    )


class MemoryJobs:
    def __init__(self, *jobs: Job) -> None:
        self.data = {job.id: Job.from_dict(job.to_dict()) for job in jobs}

    def save(self, job: Job) -> None:
        self.data[job.id] = Job.from_dict(job.to_dict())

    def get(self, job_id: str) -> Job | None:
        job = self.data.get(job_id)
        return Job.from_dict(job.to_dict()) if job else None

    def list(self) -> list[Job]:
        return [Job.from_dict(job.to_dict()) for job in self.data.values()]


class SpyNotebook:
    def __init__(self, *, fail_delete: bool = False) -> None:
        self.delete_calls: list[str] = []
        self.fail_delete = fail_delete

    def submit(self, job: Job):
        return f"remote-{job.id}", f"https://notebook.google.com/notebook/{job.id}"

    def inspect_status(self, _job: Job) -> str:
        return "READY"

    def download_artifact(self, _job: Job, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"download")
        return destination

    def delete_video_artifact(self, job: Job, _gate: DownloadSafetyGate) -> None:
        # Deliberately does not enforce the gate: Pipeline must protect this boundary.
        self.delete_calls.append(job.id)
        if self.fail_delete:
            raise RuntimeError("remote delete unavailable")


class RawStore:
    def __init__(self, gate: DownloadSafetyGate) -> None:
        self.gate = gate

    def save(self, source: Path, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return SimpleNamespace(
            media=MediaResult(destination, 1.0, destination.stat().st_size),
            safety_gate=self.gate,
        )

    def verify_existing(self, source: Path, destination: Path):
        assert source.read_bytes() == destination.read_bytes()
        return SimpleNamespace(
            media=MediaResult(destination, 1.0, destination.stat().st_size),
            safety_gate=self.gate,
        )


class Ending:
    def process(
        self, raw: Path, ending: Path, output: Path, padding_seconds: float
    ) -> MediaResult:
        assert padding_seconds == 0.5
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw.read_bytes() + ending.read_bytes())
        return MediaResult(output, 2.0, output.stat().st_size, 0.5, 1.0)


class Hls:
    def convert_validate_and_zip(self, video: Path, output_zip: Path) -> HlsResult:
        hls = output_zip.parent / ".hls" / output_zip.stem
        hls.mkdir(parents=True, exist_ok=True)
        playlist = hls / "playlist.m3u8"
        segment = hls / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n", encoding="utf-8")
        segment.write_bytes(video.read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "w", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(hls, playlist, (segment,), output_zip)


class Validator:
    def validate(self, path: Path):
        return SimpleNamespace(valid=path.is_file() and path.stat().st_size > 0)


def pipeline(
    tmp_path: Path,
    jobs: MemoryJobs,
    notebook: SpyNotebook,
    gate: DownloadSafetyGate,
) -> PipelineCoordinator:
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"ending")
    return PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RawStore(gate),
        ending=Ending(),
        hls=Hls(),
        validator=Validator(),
        paths=PipelinePaths(
            tmp_path / "raw_files",
            tmp_path / "output",
            tmp_path / "work",
            ending,
        ),
    )


@pytest.mark.parametrize("failed_check", [item.name for item in fields(DownloadSafetyGate)])
def test_pipeline_never_reaches_remote_delete_until_all_twelve_checks_pass(
    tmp_path: Path, failed_check: str
) -> None:
    gate_values = {item.name: True for item in fields(DownloadSafetyGate)}
    gate_values[failed_check] = False
    gate = DownloadSafetyGate(**gate_values)
    job = Job("lesson.txt")
    jobs = MemoryJobs(job)
    notebook = SpyNotebook()

    pipeline(tmp_path, jobs, notebook, gate).run_cycle()

    persisted = jobs.get(job.id)
    assert persisted is not None
    assert persisted.state is JobState.COMPLETED
    assert persisted.error_code == "REMOTE_ARTIFACT_DELETE_FAILED"
    assert failed_check in (persisted.error_message or "")
    assert notebook.delete_calls == []
    assert Path(persisted.raw_path or "").read_bytes() == b"download"


def test_delete_failure_keeps_raw_unchanged_continues_and_persists_retry_metadata(
    tmp_path: Path,
) -> None:
    job = Job("lesson.txt")
    jobs = MemoryJobs(job)
    notebook = SpyNotebook(fail_delete=True)
    instance = pipeline(tmp_path, jobs, notebook, complete_gate())

    instance.run_cycle()

    failed_cleanup = jobs.get(job.id)
    assert failed_cleanup is not None
    raw = Path(failed_cleanup.raw_path or "")
    before = (raw.read_bytes(), raw.stat().st_size, raw.stat().st_mtime_ns)
    assert failed_cleanup.state is JobState.COMPLETED
    assert failed_cleanup.error_code == "REMOTE_ARTIFACT_DELETE_FAILED"
    assert failed_cleanup.error_message == "remote delete unavailable"
    assert failed_cleanup.zip_path and Path(failed_cleanup.zip_path).is_file()

    notebook.fail_delete = False
    retried = instance.retry_remote_artifact_delete(job.id)
    after = (raw.read_bytes(), raw.stat().st_size, raw.stat().st_mtime_ns)
    assert after == before
    assert retried.state is JobState.COMPLETED
    assert retried.error_code is None
    assert retried.error_message is None
    assert notebook.delete_calls == [job.id, job.id]


def test_remote_delete_retry_rechecks_persisted_gate_before_adapter_call(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw_files" / "lesson.mp4"
    raw.parent.mkdir()
    raw.write_bytes(b"immutable-raw")
    job = Job(
        "lesson.txt",
        state=JobState.COMPLETED,
        raw_path=str(raw),
        error_code="REMOTE_ARTIFACT_DELETE_FAILED",
        safety_gate=DownloadSafetyGate(raw_exists=True),
    )
    jobs = MemoryJobs(job)
    notebook = SpyNotebook()
    instance = pipeline(tmp_path, jobs, notebook, complete_gate())

    with pytest.raises(RemoteDeletionDenied):
        instance.retry_remote_artifact_delete(job.id)
    assert notebook.delete_calls == []
    assert raw.read_bytes() == b"immutable-raw"
    assert jobs.get(job.id).error_code == "REMOTE_ARTIFACT_DELETE_FAILED"


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_live_notebook_metadata_and_deadline_survive_repository_restart(
    tmp_path: Path,
) -> None:
    jobs = JobRepository(tmp_path / "system" / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, 3, 0, tzinfo=UTC))
    scheduler = PersistentPollScheduler(jobs, clock=clock)
    job = Job(
        "lesson.txt",
        state=JobState.WAITING_VIDEO,
        notebook_id="live-id",
        notebook_url="https://notebook.google.com/notebook/live-id",
    )
    scheduler.schedule_generation(job)
    original_deadline = jobs.require(job.id).next_poll_at

    clock.now += timedelta(minutes=15)
    restarted = PersistentPollScheduler(
        JobRepository(tmp_path / "system" / "jobs"), clock=clock
    )
    observed: list[tuple[str | None, str | None]] = []
    assert restarted.poll_due(
        lambda due: observed.append((due.notebook_id, due.notebook_url))
    ) == [job.id]

    persisted = jobs.require(job.id)
    assert original_deadline == "2026-09-06T03:10:00+00:00"
    assert observed == [
        ("live-id", "https://notebook.google.com/notebook/live-id")
    ]
    assert persisted.generation_started_at == "2026-09-06T03:00:00+00:00"
    assert persisted.last_polled_at == "2026-09-06T03:15:00+00:00"
    assert persisted.next_poll_at == "2026-09-06T03:17:00+00:00"


def test_repository_crash_recovery_keeps_live_scheduler_metadata(tmp_path: Path) -> None:
    jobs = JobRepository(tmp_path / "jobs")
    job = Job(
        "lesson.txt",
        state=JobState.GENERATING,
        notebook_id="live-id",
        notebook_url="https://notebook.google.com/notebook/live-id",
        generation_started_at="2026-09-06T03:00:00+00:00",
        next_poll_at="2026-09-06T03:10:00+00:00",
        last_polled_at=None,
    )
    jobs.save(job)

    recovered = jobs.recover_interrupted()[0]
    assert recovered.state is JobState.WAITING_VIDEO
    assert recovered.notebook_id == "live-id"
    assert recovered.notebook_url.endswith("/live-id")
    assert recovered.generation_started_at == "2026-09-06T03:00:00+00:00"
    assert recovered.next_poll_at == "2026-09-06T03:10:00+00:00"
    assert recovered.last_polled_at is None


class IdlePipeline:
    scheduler = None

    def run_cycle(self) -> None:
        return None


def test_gui_controller_publishes_pause_resume_stop_state_transitions(
    tmp_path: Path,
) -> None:
    jobs = MemoryJobs()
    scheduler = PersistentPollScheduler(jobs)
    statuses: list[dict[str, object]] = []
    controller = GuiPipelineController(
        jobs=jobs,
        settings=SimpleNamespace(input_directory="input"),
        app_root=tmp_path,
        pipeline=IdlePipeline(),
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    controller.bind(
        jobs=lambda _value: None,
        status=lambda value: statuses.append(value),
        log=lambda _value: None,
        error=lambda _operation, _message: None,
    )

    controller.start()
    controller.pause()
    assert statuses[-1]["paused"] is True
    assert statuses[-1]["scheduler_mode"] == SchedulerMode.PAUSED.value
    controller.start()
    assert controller.status()["paused"] is False
    assert controller.status()["scheduler_mode"] == SchedulerMode.RUNNING.value
    controller.stop()
    assert statuses[-1]["running"] is False
    assert statuses[-1]["paused"] is False
    assert statuses[-1]["scheduler_mode"] == SchedulerMode.STOPPED.value
