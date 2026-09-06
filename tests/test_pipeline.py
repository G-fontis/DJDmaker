from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

from djd_maker.core.interfaces import HlsResult, MediaResult
from djd_maker.core.models import DownloadSafetyGate, Job, JobState, Preset
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from djd_maker.testing.fake_notebook import FakeNotebookAdapter


class MemoryJobs:
    def __init__(self, *jobs):
        self.data = {job.id: job for job in jobs}

    def save(self, job):
        self.data[job.id] = Job.from_dict(job.to_dict())

    def get(self, job_id):
        job = self.data.get(job_id)
        return Job.from_dict(job.to_dict()) if job else None

    def list(self):
        return [Job.from_dict(job.to_dict()) for job in self.data.values()]


def full_gate():
    return DownloadSafetyGate(**{item.name: True for item in fields(DownloadSafetyGate)})


class RawStore:
    def save(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        media = MediaResult(destination, 1.0, destination.stat().st_size)
        return SimpleNamespace(media=media, safety_gate=full_gate())

    def verify_existing(self, source, destination):
        assert source.read_bytes() == destination.read_bytes()
        media = MediaResult(destination, 1.0, destination.stat().st_size)
        return SimpleNamespace(media=media, safety_gate=full_gate())


class Validator:
    def validate(self, path):
        return SimpleNamespace(valid=path.is_file() and path.stat().st_size > 0)


class RejectingRawStore:
    def save(self, source, destination):
        raise RuntimeError("download validation failed")

    def verify_existing(self, source, destination):
        raise RuntimeError("RAW validation failed")


class Ending:
    def __init__(self, fail_name=None):
        self.calls = []
        self.fail_name = fail_name

    def process(self, raw_video, ending_video, output_path, padding_seconds):
        self.calls.append(raw_video.name)
        if raw_video.name == self.fail_name:
            raise RuntimeError("ending failed")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(raw_video.read_bytes() + ending_video.read_bytes())
        return MediaResult(output_path, 2.0, output_path.stat().st_size)


class Hls:
    def convert_validate_and_zip(self, video, output_zip):
        hls_dir = output_zip.parent / ".hls-test" / output_zip.stem
        hls_dir.mkdir(parents=True, exist_ok=True)
        playlist = hls_dir / "playlist.m3u8"
        segment = hls_dir / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        segment.write_bytes(video.read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "w", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(hls_dir, playlist, (segment,), output_zip)


def coordinator(tmp_path, jobs, notebook, ending=None, preset=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ending_file = tmp_path / "ending.mp4"
    ending_file.write_bytes(b"ending")
    return PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RawStore(),
        ending=ending or Ending(),
        hls=Hls(),
        validator=Validator(),
        paths=PipelinePaths(
            tmp_path / "raw_files",
            tmp_path / "output",
            tmp_path / "work",
            ending_file,
        ),
        ffmpeg_concurrency=2,
        generation_preset=preset,
    )


class SchedulerClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 6, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class InspectTrackingNotebook(FakeNotebookAdapter):
    def __init__(self, fixture_by_source):
        super().__init__(fixture_by_source)
        self.inspect_calls = []

    def inspect_status(self, job):
        self.inspect_calls.append(job.id)
        return super().inspect_status(job)


def test_pipeline_uses_persisted_scheduler_deadline(tmp_path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    job = Job("scheduled.txt")
    jobs = MemoryJobs(job)
    clock = SchedulerClock()
    scheduler = PersistentPollScheduler(jobs, clock=clock)
    notebook = InspectTrackingNotebook({job.source_path: fixture})
    instance = coordinator(tmp_path, jobs, notebook)
    instance.scheduler = scheduler

    instance.run_cycle()
    waiting = jobs.get(job.id)
    assert waiting.state is JobState.WAITING_VIDEO
    assert waiting.generation_started_at == "2026-09-06T00:00:00+00:00"
    assert scheduler.remaining_seconds(waiting) == 600
    assert notebook.inspect_calls == []

    clock.advance(599)
    instance.run_cycle()
    assert notebook.inspect_calls == []
    clock.advance(1)
    instance.run_cycle()
    assert notebook.inspect_calls == [job.id]
    assert jobs.get(job.id).state is JobState.COMPLETED


def test_fake_notebook_pipeline_completes_headlessly(tmp_path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    job = Job("SD001_仕事とは.txt")
    jobs = MemoryJobs(job)
    notebook = FakeNotebookAdapter({job.source_path: fixture})
    coordinator(tmp_path, jobs, notebook).run_cycle()
    result = jobs.get(job.id)
    assert result.state is JobState.COMPLETED
    assert Path(result.raw_path).name == "SD001_仕事とは.mp4"
    assert Path(result.zip_path).name == "SD001_仕事とは.zip"
    assert notebook.artifact_delete_calls == [job.id]
    assert result.progress_percent == 100.0


def test_selected_preset_is_snapshotted_and_switch_changes_generation_text(tmp_path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    prompts = []
    for key, body in (("a", "プリセットA本文"), ("b", "プリセットB本文")):
        job = Job(f"lesson-{key}.txt")
        jobs = MemoryJobs(job)
        notebook = FakeNotebookAdapter({job.source_path: fixture})
        preset = Preset(key, f"Preset {key}", body, "created", "updated")
        coordinator(tmp_path / key, jobs, notebook, preset=preset).run_cycle()
        saved = jobs.get(job.id)
        assert saved.preset_id == key
        assert saved.preset_name == f"Preset {key}"
        assert saved.generation_prompt == body
        prompts.extend(notebook.submitted_prompts)
    assert prompts == ["プリセットA本文", "プリセットB本文"]


def test_waiting_notebook_does_not_block_raw_ready_job(tmp_path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    waiting = Job(
        "A.txt",
        state=JobState.WAITING_VIDEO,
        notebook_id="a",
        notebook_url="https://notebook.google.com/notebook/a",
    )
    raw = tmp_path / "raw-b.mp4"
    raw.write_bytes(b"raw")
    ready = Job("B.txt", state=JobState.RAW_READY, raw_path=str(raw))
    jobs = MemoryJobs(waiting, ready)
    notebook = FakeNotebookAdapter({}, status_by_job={waiting.id: "WAITING"})
    coordinator(tmp_path, jobs, notebook).run_cycle()
    assert jobs.get(waiting.id).state is JobState.WAITING_VIDEO
    assert jobs.get(ready.id).state is JobState.COMPLETED


def test_one_media_failure_does_not_stop_other_job(tmp_path):
    bad_raw = tmp_path / "bad.mp4"
    good_raw = tmp_path / "good.mp4"
    bad_raw.write_bytes(b"bad")
    good_raw.write_bytes(b"good")
    bad = Job("bad.txt", state=JobState.RAW_READY, raw_path=str(bad_raw))
    good = Job("good.txt", state=JobState.RAW_READY, raw_path=str(good_raw))
    jobs = MemoryJobs(bad, good)
    coordinator(
        tmp_path, jobs, FakeNotebookAdapter({}), Ending(fail_name="bad.mp4")
    ).run_cycle()
    assert jobs.get(bad.id).state is JobState.FAILED
    assert jobs.get(good.id).state is JobState.COMPLETED


def test_remote_delete_failure_after_raw_does_not_lose_pipeline_progress(tmp_path):
    fixture = tmp_path / "fixture.mp4"
    fixture.write_bytes(b"video")
    job = Job("delete-failure.txt")
    jobs = MemoryJobs(job)
    notebook = FakeNotebookAdapter({job.source_path: fixture}, fail_delete=True)
    coordinator(tmp_path, jobs, notebook).run_cycle()
    result = jobs.get(job.id)
    assert result.state is JobState.COMPLETED
    assert result.error_code == "REMOTE_ARTIFACT_DELETE_FAILED"
    assert result.raw_path and Path(result.raw_path).exists()


def test_completed_job_is_not_reprocessed(tmp_path):
    job = Job("done.txt", state=JobState.COMPLETED)
    jobs = MemoryJobs(job)
    ending = Ending()
    coordinator(tmp_path, jobs, FakeNotebookAdapter({}), ending).run_cycle()
    assert ending.calls == []


def test_download_restart_reuses_verified_existing_raw(tmp_path):
    source = tmp_path / "work" / "job" / "download" / "resume.mp4"
    raw = tmp_path / "raw_files" / "resume.mp4"
    source.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    source.write_bytes(b"same")
    raw.write_bytes(b"same")
    job = Job(
        "resume.txt",
        id="job",
        state=JobState.DOWNLOADING,
        notebook_id="remote",
        notebook_url="https://notebook.google.com/notebook/remote",
    )
    jobs = MemoryJobs(job)
    notebook = FakeNotebookAdapter({})
    coordinator(tmp_path, jobs, notebook).run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert notebook.download_calls == []
    assert notebook.artifact_delete_calls == [job.id]


def test_ending_restart_uses_valid_checkpoint_without_reencoding(tmp_path):
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"raw")
    job = Job("resume-ending.txt", state=JobState.ENDING, raw_path=str(raw))
    edited = tmp_path / "work" / job.id / "ending" / "resume-ending.mp4"
    edited.parent.mkdir(parents=True)
    edited.write_bytes(b"already-valid")
    jobs = MemoryJobs(job)
    ending = Ending()
    coordinator(tmp_path, jobs, FakeNotebookAdapter({}), ending).run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert ending.calls == []


def test_invalid_download_never_calls_remote_delete(tmp_path):
    fixture = tmp_path / "zero-byte.mp4"
    fixture.touch()
    job = Job("invalid.txt")
    jobs = MemoryJobs(job)
    notebook = FakeNotebookAdapter({job.source_path: fixture})
    instance = coordinator(tmp_path, jobs, notebook)
    instance.raw_store = RejectingRawStore()
    instance.run_cycle()
    assert jobs.get(job.id).state is JobState.DOWNLOAD_VERIFY_FAILED
    assert notebook.artifact_delete_calls == []


def test_missing_ending_prevents_pipeline_start(tmp_path):
    paths = PipelinePaths(
        tmp_path / "raw", tmp_path / "output", tmp_path / "work", tmp_path / "missing.mp4"
    )
    import pytest

    with pytest.raises(FileNotFoundError):
        PipelineCoordinator(
            jobs=MemoryJobs(),
            notebook=FakeNotebookAdapter({}),
            raw_store=RawStore(),
            ending=Ending(),
            hls=Hls(),
            validator=Validator(),
            paths=paths,
        )
