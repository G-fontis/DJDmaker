from __future__ import annotations

from pathlib import Path
import threading
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

from djd_maker.core.interfaces import HlsResult, MediaResult
from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths


class ThreadSafeJobs:
    def __init__(self, *jobs: Job):
        self._lock = threading.Lock()
        self._jobs = {job.id: Job.from_dict(job.to_dict()) for job in jobs}

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = Job.from_dict(job.to_dict())

    def get(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            return Job.from_dict(job.to_dict()) if job else None

    def list(self):
        with self._lock:
            return [Job.from_dict(job.to_dict()) for job in self._jobs.values()]


class ConcurrentEnding:
    def __init__(self):
        self._lock = threading.Lock()
        self.release = threading.Event()
        self.both_entered = threading.Event()
        self.active = 0
        self.peak = 0
        self.output_parents: list[Path] = []

    def process(self, raw_video, ending_video, output_path, padding_seconds=0.5):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.output_parents.append(output_path.parent)
            if self.active == 2:
                self.both_entered.set()
                self.release.set()
        assert self.release.wait(timeout=2)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(Path(raw_video).read_bytes() + Path(ending_video).read_bytes())
        with self._lock:
            self.active -= 1
        return MediaResult(output_path, 2.0, output_path.stat().st_size)


class MinimalHls:
    def convert_validate_and_zip(self, video, output_zip):
        hls_dir = output_zip.parent / ".unit4" / output_zip.stem
        hls_dir.mkdir(parents=True, exist_ok=True)
        playlist = hls_dir / "playlist.m3u8"
        segment = hls_dir / "segment00000.ts"
        playlist.write_text("#EXTM3U\nsegment00000.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        segment.write_bytes(Path(video).read_bytes())
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_zip, "w", compression=ZIP_STORED) as archive:
            archive.write(playlist, playlist.name)
            archive.write(segment, segment.name)
        return HlsResult(hls_dir, playlist, (segment,), output_zip)


def test_ffmpeg_lane_really_runs_two_distinct_job_id_workspaces_in_parallel(tmp_path):
    raw_a, raw_b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    raw_a.write_bytes(b"a")
    raw_b.write_bytes(b"b")
    first = Job("A.txt", id="job-a", state=JobState.RAW_READY, raw_path=str(raw_a))
    second = Job("B.txt", id="job-b", state=JobState.RAW_READY, raw_path=str(raw_b))
    jobs = ThreadSafeJobs(first, second)
    ending_file = tmp_path / "ending.mp4"
    ending_file.write_bytes(b"ending")
    ending = ConcurrentEnding()
    pipeline = PipelineCoordinator(
        jobs=jobs,
        notebook=SimpleNamespace(),
        raw_store=SimpleNamespace(),
        ending=ending,
        hls=MinimalHls(),
        validator=SimpleNamespace(validate=lambda path: SimpleNamespace(valid=True)),
        paths=PipelinePaths(
            tmp_path / "raw_files", tmp_path / "output", tmp_path / "work", ending_file
        ),
        ffmpeg_concurrency=2,
    )

    pipeline.run_cycle()

    assert ending.both_entered.is_set()
    assert ending.peak == 2
    assert {path.parent.name for path in ending.output_parents} == {"job-a", "job-b"}
    assert {job.state for job in jobs.list()} == {JobState.COMPLETED}


class SchedulerStub:
    POLLABLE_STATES = frozenset()

    def start(self): pass
    def stop(self): pass
    def pause(self): pass
    def resume(self): pass

    @property
    def mode(self):
        return SimpleNamespace(value="RUNNING")


def test_job_state_log_carries_both_stable_id_and_human_stem(tmp_path):
    job = Job("input/SD001_仕事とは.txt", id="stable-job-id", state=JobState.COMPLETED)
    jobs = ThreadSafeJobs(job)
    records = []
    controller = GuiPipelineController(
        jobs=jobs,
        settings=AppSettings(),
        app_root=tmp_path,
        pipeline=SimpleNamespace(run_cycle=lambda: None, scheduler=None),
        scheduler=SchedulerStub(),
        cycle_interval_seconds=0.01,
    )
    controller.bind(
        jobs=lambda _value: None,
        status=lambda _value: None,
        log=records.append,
        error=lambda _operation, _message: None,
    )

    controller.start()
    worker = controller._worker
    assert worker is not None
    worker.join(timeout=2)

    state_record = next(record for record in records if record.get("job_id"))
    assert state_record["job_id"] == "stable-job-id"
    assert state_record["script_name"] == "SD001_仕事とは"
    assert "[SD001_仕事とは]" in state_record["message"]
