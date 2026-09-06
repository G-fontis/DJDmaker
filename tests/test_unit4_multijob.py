from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

from djd_maker.core.interfaces import HlsResult, MediaResult
from djd_maker.core.models import DownloadSafetyGate, Job, JobState, Preset
from djd_maker.core.repositories import JobRepository
from djd_maker.core.settings import AppSettings
from djd_maker.gui.app import _resolved
from djd_maker.gui.viewmodels import state_display, summarize_jobs
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler, SchedulerMode


def full_gate() -> DownloadSafetyGate:
    return DownloadSafetyGate(
        **{item.name: True for item in fields(DownloadSafetyGate)}
    )


class MemoryJobs:
    def __init__(self, *jobs: Job) -> None:
        self.data = {job.id: Job.from_dict(job.to_dict()) for job in jobs}

    def save(self, job: Job) -> None:
        self.data[job.id] = Job.from_dict(job.to_dict())

    def get(self, job_id: str) -> Job | None:
        value = self.data.get(job_id)
        return Job.from_dict(value.to_dict()) if value else None

    def list(self) -> list[Job]:
        return [Job.from_dict(job.to_dict()) for job in self.data.values()]


class IdentityNotebook:
    def __init__(self, fixture_by_source: dict[str, Path]) -> None:
        self.fixture_by_source = fixture_by_source
        self.events: list[tuple[str, str]] = []
        self.status_by_job: dict[str, str] = {}

    def submit(self, job: Job) -> tuple[str, str]:
        self.events.append(("submit", job.id))
        self.status_by_job[job.id] = "READY"
        return job.script_name, f"https://notebook.google.com/notebook/{job.id}"

    def inspect_status(self, job: Job) -> str:
        self.events.append(("poll", job.id))
        return self.status_by_job.get(job.id, "READY")

    def download_artifact(self, job: Job, destination: Path) -> Path:
        self.events.append(("download", job.id))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.fixture_by_source[job.source_path].read_bytes())
        return destination

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None:
        assert gate.remote_deletion_allowed
        self.events.append(("delete", job.id))


class RawStore:
    def save(self, source: Path, destination: Path):
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return SimpleNamespace(
            media=MediaResult(destination, 1.0, destination.stat().st_size),
            safety_gate=full_gate(),
        )

    def verify_existing(self, source: Path, destination: Path):
        assert source.read_bytes() == destination.read_bytes()
        return SimpleNamespace(
            media=MediaResult(destination, 1.0, destination.stat().st_size),
            safety_gate=full_gate(),
        )


class ImmutableEnding:
    def __init__(self, fail_stem: str | None = None) -> None:
        self.fail_stem = fail_stem
        self.raw_observations: dict[str, tuple[str, int, int]] = {}

    def process(
        self, raw: Path, ending: Path, output: Path, padding_seconds: float
    ) -> MediaResult:
        self.raw_observations[raw.stem] = fingerprint(raw)
        if raw.stem == self.fail_stem:
            raise RuntimeError("controlled ending failure")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw.read_bytes() + ending.read_bytes())
        return MediaResult(output, 2.0, output.stat().st_size, 0.5, 1.0)


class MappingHls:
    def __init__(self) -> None:
        self.input_by_zip: dict[str, bytes] = {}

    def convert_validate_and_zip(self, video: Path, output_zip: Path) -> HlsResult:
        self.input_by_zip[output_zip.stem] = video.read_bytes()
        hls = output_zip.parent / ".hls" / output_zip.stem
        hls.mkdir(parents=True, exist_ok=True)
        playlist = hls / "playlist.m3u8"
        segment = hls / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n", encoding="utf-8")
        segment.write_bytes(video.read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "x", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(hls, playlist, (segment,), output_zip)


class Validator:
    def validate(self, path: Path):
        return SimpleNamespace(valid=path.is_file() and path.stat().st_size > 0)


def fingerprint(path: Path) -> tuple[str, int, int]:
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        path.stat().st_size,
        path.stat().st_mtime_ns,
    )


def make_pipeline(
    tmp_path: Path,
    jobs,
    notebook,
    *,
    ending: ImmutableEnding | None = None,
    hls: MappingHls | None = None,
    scheduler: PersistentPollScheduler | None = None,
) -> tuple[PipelineCoordinator, ImmutableEnding, MappingHls]:
    ending_file = tmp_path / "ending.mp4"
    ending_file.write_bytes(b"-ending")
    ending_engine = ending or ImmutableEnding()
    hls_engine = hls or MappingHls()
    return (
        PipelineCoordinator(
            jobs=jobs,
            notebook=notebook,
            raw_store=RawStore(),
            ending=ending_engine,
            hls=hls_engine,
            validator=Validator(),
            paths=PipelinePaths(
                tmp_path / "raw_files",
                tmp_path / "output",
                tmp_path / "work",
                ending_file,
            ),
            ffmpeg_concurrency=2,
            scheduler=scheduler,
            generation_preset=Preset(
                "test-preset", "Test preset", "test body", "created", "updated"
            ),
        ),
        ending_engine,
        hls_engine,
    )


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_three_jobs_keep_identity_raw_immutable_and_zip_mapping(tmp_path: Path) -> None:
    jobs_list: list[Job] = []
    fixtures: dict[str, Path] = {}
    for index in range(1, 4):
        source = tmp_path / "input" / f"DJD_MULTI_{index:03}.txt"
        source.parent.mkdir(exist_ok=True)
        source.write_text(f"lesson {index}", encoding="utf-8")
        fixture = tmp_path / f"fixture-{index}.mp4"
        fixture.write_bytes(f"video-{index}".encode())
        job = Job(str(source), id=f"job-{index}")
        jobs_list.append(job)
        fixtures[job.source_path] = fixture
    jobs = MemoryJobs(*jobs_list)
    notebook = IdentityNotebook(fixtures)
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(jobs, clock=clock)
    instance, ending, hls = make_pipeline(
        tmp_path, jobs, notebook, scheduler=scheduler
    )

    instance.run_cycle()
    assert all(job.state is JobState.WAITING_VIDEO for job in jobs.list())
    clock.value += timedelta(seconds=600)
    instance.run_cycle()

    completed = jobs.list()
    assert all(job.state is JobState.COMPLETED for job in completed)
    assert len(list((tmp_path / "raw_files").glob("*.mp4"))) == 3
    assert len(list((tmp_path / "output").glob("*.zip"))) == 3
    assert {job.notebook_id for job in completed} == {
        "DJD_MULTI_001",
        "DJD_MULTI_002",
        "DJD_MULTI_003",
    }
    assert all(job.notebook_url and job.id in job.notebook_url for job in completed)
    assert [item for item in notebook.events if item[0] == "delete"] == [
        ("delete", "job-1"),
        ("delete", "job-2"),
        ("delete", "job-3"),
    ]
    for index, job in enumerate(completed, 1):
        stem = f"DJD_MULTI_{index:03}"
        raw = Path(job.raw_path or "")
        output_zip = Path(job.zip_path or "")
        assert raw.name == f"{stem}.mp4"
        assert raw.read_bytes() == f"video-{index}".encode()
        assert fingerprint(raw) == ending.raw_observations[stem]
        assert output_zip.name == f"{stem}.zip"
        assert hls.input_by_zip[stem] == f"video-{index}".encode() + b"-ending"
        with ZipFile(output_zip) as archive:
            assert archive.testzip() is None


def test_same_stem_jobs_fail_second_before_wrong_notebook_or_output_mapping(
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "a" / "Lesson.txt"
    second_source = tmp_path / "b" / "lesson.txt"
    first_source.parent.mkdir()
    second_source.parent.mkdir()
    first_source.write_text("A", encoding="utf-8")
    second_source.write_text("B", encoding="utf-8")
    first_fixture = tmp_path / "first.mp4"
    second_fixture = tmp_path / "second.mp4"
    first_fixture.write_bytes(b"first-video")
    second_fixture.write_bytes(b"second-video")
    first = Job(str(first_source), id="a", created_at="2026-09-06T00:00:00+00:00")
    second = Job(str(second_source), id="b", created_at="2026-09-06T00:00:01+00:00")
    jobs = MemoryJobs(first, second)
    notebook = IdentityNotebook(
        {first.source_path: first_fixture, second.source_path: second_fixture}
    )
    instance, _ending, _hls = make_pipeline(tmp_path, jobs, notebook)

    instance.run_cycle()

    assert jobs.get(first.id).state is JobState.COMPLETED
    rejected = jobs.get(second.id)
    assert rejected is not None and rejected.state is JobState.FAILED
    assert rejected.error_code == "OUTPUT_NAME_COLLISION"
    assert rejected.raw_path is None and rejected.zip_path is None
    assert not any(job_id == second.id for _operation, job_id in notebook.events)
    assert (tmp_path / "raw_files" / "Lesson.mp4").read_bytes() == b"first-video"
    assert len(list((tmp_path / "output").glob("*.zip"))) == 1


def test_existing_zip_is_collision_not_another_jobs_mapping(tmp_path: Path) -> None:
    raw = tmp_path / "raw.mp4"
    edited = tmp_path / "work" / "job" / "ending" / "lesson.mp4"
    raw.write_bytes(b"raw")
    edited.parent.mkdir(parents=True)
    edited.write_bytes(b"edited")
    existing = tmp_path / "output" / "lesson.zip"
    existing.parent.mkdir()
    with ZipFile(existing, "w", compression=ZIP_STORED) as archive:
        archive.writestr("playlist.m3u8", "foreign")
    before = existing.read_bytes()
    job = Job(
        "lesson.txt",
        id="job",
        state=JobState.HLS_ENCODING,
        raw_path=str(raw),
        edited_path=str(edited),
    )
    jobs = MemoryJobs(job)
    instance, _ending, _hls = make_pipeline(
        tmp_path, jobs, IdentityNotebook({})
    )

    instance.run_cycle()

    failed = jobs.get(job.id)
    assert failed is not None and failed.state is JobState.FAILED
    assert failed.error_code == "MEDIA_STAGE_FAILED"
    assert existing.read_bytes() == before
    assert failed.zip_path is None


def test_one_failure_does_not_stop_other_two_jobs(tmp_path: Path) -> None:
    raws = []
    jobs_list = []
    for stem in ("A", "B", "C"):
        raw = tmp_path / f"{stem}.mp4"
        raw.write_bytes(stem.encode())
        raws.append(raw)
        jobs_list.append(Job(f"{stem}.txt", id=stem, state=JobState.RAW_READY, raw_path=str(raw)))
    jobs = MemoryJobs(*jobs_list)
    instance, _ending, _hls = make_pipeline(
        tmp_path,
        jobs,
        IdentityNotebook({}),
        ending=ImmutableEnding(fail_stem="B"),
    )

    instance.run_cycle()

    assert jobs.get("B").state is JobState.FAILED
    assert jobs.get("A").state is JobState.COMPLETED
    assert jobs.get("C").state is JobState.COMPLETED
    assert [raw.read_bytes() for raw in raws] == [b"A", b"B", b"C"]


def test_restart_recovers_three_stages_without_touching_completed_job(
    tmp_path: Path,
) -> None:
    repository = JobRepository(tmp_path / "system" / "jobs")
    fixture = tmp_path / "waiting.mp4"
    fixture.write_bytes(b"waiting")
    waiting = Job(
        "waiting.txt",
        id="waiting",
        state=JobState.WAITING_VIDEO,
        notebook_id="waiting",
        notebook_url="https://notebook.google.com/notebook/waiting",
    )
    raw_ready_file = tmp_path / "raw-ready.mp4"
    raw_ready_file.write_bytes(b"raw-ready")
    raw_ready = Job(
        "raw-ready.txt", id="raw-ready", state=JobState.RAW_READY, raw_path=str(raw_ready_file)
    )
    hls_raw = tmp_path / "hls.mp4"
    hls_raw.write_bytes(b"hls-raw")
    hls_job = Job(
        "hls.txt", id="hls", state=JobState.HLS_ENCODING, raw_path=str(hls_raw)
    )
    hls_edited = tmp_path / "work" / "hls" / "ending" / "hls.mp4"
    hls_edited.parent.mkdir(parents=True)
    hls_edited.write_bytes(b"hls-edited")
    hls_job.edited_path = str(hls_edited)
    completed_zip = tmp_path / "output" / "done.zip"
    completed_zip.parent.mkdir()
    completed_zip.write_bytes(b"keep-completed")
    done = Job("done.txt", id="done", state=JobState.COMPLETED, zip_path=str(completed_zip))
    for job in (waiting, raw_ready, hls_job, done):
        repository.save(job)
    notebook = IdentityNotebook({waiting.source_path: fixture})
    instance, _ending, _hls = make_pipeline(tmp_path, repository, notebook)

    recoverable = instance.recover_after_restart()
    assert {job.id for job in recoverable} == {"waiting", "raw-ready", "hls"}
    instance.run_cycle()

    assert all(repository.require(job_id).state is JobState.COMPLETED for job_id in ("waiting", "raw-ready", "hls", "done"))
    assert completed_zip.read_bytes() == b"keep-completed"
    assert not any(job_id == "done" for _operation, job_id in notebook.events)
    assert raw_ready_file.read_bytes() == b"raw-ready"
    assert hls_raw.read_bytes() == b"hls-raw"


def test_scheduler_keeps_independent_deadlines_and_stops_polling_completed_jobs() -> None:
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    first = Job("first.txt", id="first", state=JobState.WAITING_VIDEO)
    second = Job("second.txt", id="second", state=JobState.WAITING_VIDEO)
    jobs = MemoryJobs(first, second)
    scheduler = PersistentPollScheduler(jobs, clock=clock)
    scheduler.schedule_generation(first)
    clock.value += timedelta(seconds=60)
    scheduler.schedule_generation(second)

    clock.value += timedelta(seconds=540)
    calls: list[str] = []
    scheduler.poll_due(lambda job: calls.append(job.id))
    assert calls == ["first"]
    jobs_first = jobs.get("first")
    assert jobs_first is not None
    jobs_first.state = JobState.COMPLETED
    jobs.save(jobs_first)
    clock.value += timedelta(seconds=60)
    scheduler.poll_due(lambda job: calls.append(job.id))
    assert calls == ["first", "second"]
    clock.value += timedelta(seconds=120)
    scheduler.poll_due(lambda job: calls.append(job.id))
    assert calls == ["first", "second", "second"]


class CompletingPipeline:
    def __init__(self, jobs: MemoryJobs) -> None:
        self.jobs = jobs
        self.scheduler = None

    def run_cycle(self) -> None:
        for job in self.jobs.list():
            job.state = JobState.COMPLETED
            job.progress_percent = 100
            self.jobs.save(job)


class PausableMultiJobPipeline:
    def __init__(self, jobs: MemoryJobs) -> None:
        self.jobs = jobs
        self.scheduler = None
        self.cycles = 0
        self.submit_count: dict[str, int] = {}
        self.download_count: dict[str, int] = {}
        self.first_cycle_entered = threading.Event()
        self.release_first_cycle = threading.Event()

    def run_cycle(self) -> None:
        self.cycles += 1
        for job in self.jobs.list():
            if job.state is JobState.WAITING:
                self.submit_count[job.id] = self.submit_count.get(job.id, 0) + 1
                job.state = JobState.WAITING_VIDEO
                job.notebook_id = f"notebook-{job.id}"
                job.notebook_url = f"https://notebook.google.com/notebook/{job.id}"
                job.generation_started_at = "2026-09-06T00:00:00+00:00"
                job.next_poll_at = "2026-09-06T00:10:00+00:00"
                self.jobs.save(job)
        if self.cycles == 1:
            self.first_cycle_entered.set()
            assert self.release_first_cycle.wait(timeout=2)
            return
        for job in self.jobs.list():
            if job.state is JobState.WAITING_VIDEO:
                self.download_count[job.id] = self.download_count.get(job.id, 0) + 1
                job.state = JobState.COMPLETED
                self.jobs.save(job)


def test_pause_resume_three_jobs_preserves_deadlines_without_duplicate_work(
    tmp_path: Path,
) -> None:
    jobs = MemoryJobs(
        Job("A.txt", id="A"), Job("B.txt", id="B"), Job("C.txt", id="C")
    )
    pipeline = PausableMultiJobPipeline(jobs)
    scheduler = PersistentPollScheduler(jobs)
    controller = GuiPipelineController(
        jobs=jobs,
        settings=AppSettings(),
        app_root=tmp_path,
        pipeline=pipeline,
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    controller.start()
    assert pipeline.first_cycle_entered.wait(timeout=2)
    controller.pause()
    persisted_deadlines = {job.id: job.next_poll_at for job in jobs.list()}
    pipeline.release_first_cycle.set()
    time.sleep(0.05)
    assert pipeline.cycles == 1
    assert all(count == 1 for count in pipeline.submit_count.values())

    controller.start()
    deadline = time.monotonic() + 2
    while any(job.state is not JobState.COMPLETED for job in jobs.list()) and time.monotonic() < deadline:
        time.sleep(0.01)
    controller.stop()

    assert all(job.state is JobState.COMPLETED for job in jobs.list())
    assert {job.id: job.next_poll_at for job in jobs.list()} == persisted_deadlines
    assert pipeline.submit_count == {"A": 1, "B": 1, "C": 1}
    assert pipeline.download_count == {"A": 1, "B": 1, "C": 1}


def test_controller_natural_completion_stops_scheduler_and_gui_aggregate(
    tmp_path: Path,
) -> None:
    aggregate_jobs = []
    for stem in ("A", "B", "C"):
        raw = tmp_path / "raw_files" / f"{stem}.mp4"
        output_zip = tmp_path / "output" / f"{stem}.zip"
        raw.parent.mkdir(exist_ok=True)
        output_zip.parent.mkdir(exist_ok=True)
        raw.write_bytes(stem.encode())
        output_zip.write_bytes(b"zip-" + stem.encode())
        aggregate_jobs.append(
            Job(
                f"{stem}.txt",
                id=stem,
                raw_path=str(raw),
                zip_path=str(output_zip),
            )
        )
    jobs = MemoryJobs(*aggregate_jobs)
    scheduler = PersistentPollScheduler(jobs)
    controller = GuiPipelineController(
        jobs=jobs,
        settings=AppSettings(),
        app_root=tmp_path,
        pipeline=CompletingPipeline(jobs),
        scheduler=scheduler,
        cycle_interval_seconds=0.01,
    )
    controller.start()
    deadline = time.monotonic() + 2
    while controller.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert scheduler.mode is SchedulerMode.STOPPED
    summary = summarize_jobs(jobs.list())
    assert (
        summary.total,
        summary.active,
        summary.notebook_complete,
        summary.zip_complete,
        summary.errors,
    ) == (3, 0, 3, 3, 0)
    assert len(list((tmp_path / "raw_files").glob("*.mp4"))) == summary.notebook_complete
    assert len(list((tmp_path / "output").glob("*.zip"))) == summary.zip_complete
    assert all(state_display(job) == "○ 完成" for job in jobs.list())


def test_portable_relative_paths_resolve_under_root_with_unicode_and_spaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "日本語 portable root"
    root.mkdir()
    assert _resolved(root, "raw_files") == (root / "raw_files").resolve()
    assert _resolved(root, "output/授業 ZIP") == (root / "output" / "授業 ZIP").resolve()
    absolute = (tmp_path / "external" / "Ending.mp4").resolve()
    assert _resolved(root, str(absolute)) == absolute
