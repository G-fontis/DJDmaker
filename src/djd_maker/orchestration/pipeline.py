from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from zipfile import BadZipFile, ZipFile

from djd_maker.core.interfaces import EndingEngine, HlsEngine, NotebookEngine
from djd_maker.core.models import Job, JobState


class JobRepositoryPort(Protocol):
    def save(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(self) -> list[Job]: ...


class RawStorePort(Protocol):
    def save(self, source: Path, destination: Path) -> Any: ...

    def verify_existing(self, source: Path, destination: Path) -> Any: ...


class ValidatorPort(Protocol):
    def validate(self, path: Path) -> Any: ...


class PollSchedulerPort(Protocol):
    def schedule_generation(self, job: Job, *, force: bool = False) -> Job: ...

    def ensure_scheduled(self, job: Job) -> Job: ...

    def poll_due(self, poll: Any) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class PipelinePaths:
    raw_directory: Path
    output_directory: Path
    work_directory: Path
    ending_video: Path


class PipelineCoordinator:
    """Notebook laneとbounded FFmpeg laneを1 cycleずつ前進させる。"""

    NOTEBOOK_STATES = frozenset(
        {
            JobState.WAITING,
            JobState.UPLOADING,
            JobState.GENERATING,
            JobState.WAITING_VIDEO,
            JobState.DOWNLOADING,
            JobState.DOWNLOAD_VERIFY_FAILED,
        }
    )
    MEDIA_STATES = frozenset(
        {JobState.RAW_READY, JobState.ENDING, JobState.HLS_ENCODING, JobState.ZIPPING}
    )

    def __init__(
        self,
        *,
        jobs: JobRepositoryPort,
        notebook: NotebookEngine,
        raw_store: RawStorePort,
        ending: EndingEngine,
        hls: HlsEngine,
        validator: ValidatorPort,
        paths: PipelinePaths,
        ffmpeg_concurrency: int = 1,
        scheduler: PollSchedulerPort | None = None,
    ) -> None:
        if ffmpeg_concurrency not in {1, 2}:
            raise ValueError("ffmpeg_concurrency must be 1 or 2")
        if not paths.ending_video.is_file():
            raise FileNotFoundError(f"Ending動画が未設定または存在しません: {paths.ending_video}")
        self.jobs = jobs
        self.notebook = notebook
        self.raw_store = raw_store
        self.ending = ending
        self.hls = hls
        self.validator = validator
        self.paths = paths
        self.ffmpeg_concurrency = ffmpeg_concurrency
        self.scheduler = scheduler

    def _save(self, job: Job) -> None:
        self.jobs.save(job)

    def _transition(self, job: Job, target: JobState) -> None:
        job.transition_to(target)
        self._save(job)

    def run_cycle(self) -> None:
        # Browser automation remains serialized. Waiting in this lane never
        # prevents already downloaded jobs from entering the media pool below.
        for job in self.jobs.list():
            if job.state in self.NOTEBOOK_STATES and not (
                self.scheduler is not None and job.state is JobState.WAITING_VIDEO
            ):
                self._run_notebook_job(job)

        if self.scheduler is not None:
            self.scheduler.poll_due(self._run_notebook_job)

        media_jobs = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES]
        with ThreadPoolExecutor(
            max_workers=self.ffmpeg_concurrency, thread_name_prefix="djd-ffmpeg"
        ) as pool:
            futures = [pool.submit(self._run_media_job, job) for job in media_jobs]
            for future in futures:
                future.result()

    def recover_after_restart(self) -> list[Job]:
        loader = getattr(self.jobs, "load_recoverable", None)
        if loader is None:
            return [
                job
                for job in self.jobs.list()
                if job.state not in {JobState.COMPLETED, JobState.FAILED}
            ]
        return loader()

    def _run_notebook_job(self, job: Job) -> None:
        if job.state is JobState.DOWNLOAD_VERIFY_FAILED:
            return
        try:
            if job.state is JobState.WAITING:
                self._transition(job, JobState.UPLOADING)
                notebook_id, notebook_url = self.notebook.submit(job)
                job.notebook_id = notebook_id
                job.notebook_url = notebook_url
                self._transition(job, JobState.GENERATING)
                if self.scheduler is not None:
                    self.scheduler.schedule_generation(job)

            if job.state is JobState.UPLOADING:
                # A crash without persisted remote identity cannot safely retry:
                # doing so could create a duplicate Notebook.
                job.error_code = "SUBMISSION_STATE_UNCERTAIN"
                job.error_message = "Notebook identity was not persisted"
                self._transition(job, JobState.FAILED)
                return

            if job.state is JobState.GENERATING:
                if not job.notebook_id or not job.notebook_url:
                    raise RuntimeError("Notebook resume metadata is missing")
                self._transition(job, JobState.WAITING_VIDEO)
                if self.scheduler is not None:
                    self.scheduler.ensure_scheduled(job)
                    return

            if job.state is JobState.WAITING_VIDEO:
                status = self.notebook.inspect_status(job)
                if status != "READY":
                    if status == "FAILED":
                        raise RuntimeError("remote video generation failed")
                    return
                self._transition(job, JobState.DOWNLOADING)

            if job.state is JobState.DOWNLOADING:
                download = (
                    self.paths.work_directory
                    / job.id
                    / "download"
                    / f"{job.script_name}.mp4"
                )
                if not download.exists():
                    self.notebook.download_artifact(job, download)
                raw_path = self.paths.raw_directory / f"{job.script_name}.mp4"
                if raw_path.exists():
                    stored = self.raw_store.verify_existing(download, raw_path)
                else:
                    stored = self.raw_store.save(download, raw_path)
                media = getattr(stored, "media", stored)
                gate = getattr(stored, "safety_gate", None)
                if gate is None:
                    raise RuntimeError("RAW store did not return a deletion safety gate")
                job.raw_path = str(getattr(media, "path", raw_path))
                job.raw_size_bytes = getattr(media, "size_bytes", None)
                job.duration_seconds = getattr(media, "duration_seconds", None)
                raw_validation = getattr(stored, "raw_validation", None)
                metadata = getattr(raw_validation, "metadata", None)
                job.video_codec = getattr(metadata, "video_codec", None)
                job.audio_codec = getattr(metadata, "audio_codec", None)
                job.safety_gate = gate
                self._transition(job, JobState.RAW_READY)
                try:
                    self.notebook.delete_video_artifact(job, gate)
                except Exception as exc:
                    # RAW is already durable and verified. Remote cleanup can be
                    # retried independently and must not destroy local progress.
                    job.error_code = "REMOTE_ARTIFACT_DELETE_FAILED"
                    job.error_message = str(exc)
                    self._save(job)
        except Exception as exc:
            job.error_message = str(exc)
            if job.state is JobState.DOWNLOADING:
                job.error_code = "DOWNLOAD_VERIFY_FAILED"
                self._transition(job, JobState.DOWNLOAD_VERIFY_FAILED)
            elif job.state not in {JobState.FAILED, JobState.COMPLETED}:
                job.error_code = job.error_code or "NOTEBOOK_STAGE_FAILED"
                self._transition(job, JobState.FAILED)

    def _run_media_job(self, job: Job) -> None:
        try:
            raw = Path(job.raw_path or "")
            edited = (
                self.paths.work_directory / job.id / "ending" / f"{job.script_name}.mp4"
            )
            output_zip = self.paths.output_directory / f"{job.script_name}.zip"

            if job.state is JobState.RAW_READY:
                self._transition(job, JobState.ENDING)

            if job.state is JobState.ENDING:
                existing_is_valid = False
                if edited.exists():
                    try:
                        existing = self.validator.validate(edited)
                        existing_is_valid = getattr(existing, "valid", True)
                    except Exception:
                        existing_is_valid = False
                if not existing_is_valid:
                    if edited.exists():
                        edited.unlink()
                    result = self.ending.process(
                        raw, self.paths.ending_video, edited, padding_seconds=0.5
                    )
                    job.edited_path = str(result.path)
                    job.last_audio_position_seconds = getattr(
                        result, "last_audio_end_seconds", None
                    )
                    job.cut_position_seconds = getattr(result, "cut_at_seconds", None)
                    job.ending_result = "PASS"
                else:
                    job.edited_path = str(edited)
                    job.ending_result = "PASS (checkpoint)"
                self._transition(job, JobState.HLS_ENCODING)

            if job.state in {JobState.HLS_ENCODING, JobState.ZIPPING}:
                if job.state is JobState.HLS_ENCODING:
                    self._transition(job, JobState.ZIPPING)
                if output_zip.exists():
                    if not self._valid_zip(output_zip):
                        raise FileExistsError(f"不正な既存ZIPを上書きしません: {output_zip}")
                    job.zip_path = str(output_zip)
                else:
                    result = self.hls.convert_validate_and_zip(
                        Path(job.edited_path or edited), output_zip
                    )
                    job.zip_path = str(result.zip_path)
                job.hls_result = "PASS"
                self._transition(job, JobState.COMPLETED)
        except Exception as exc:
            job.error_code = "MEDIA_STAGE_FAILED"
            job.error_message = str(exc)
            if job.state not in {JobState.FAILED, JobState.COMPLETED}:
                self._transition(job, JobState.FAILED)

    @staticmethod
    def _valid_zip(path: Path) -> bool:
        try:
            with ZipFile(path) as archive:
                return bool(archive.namelist()) and archive.testzip() is None
        except (OSError, BadZipFile):
            return False

    def retry_download(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.state is not JobState.DOWNLOAD_VERIFY_FAILED:
            raise ValueError("download retry is only allowed from DOWNLOAD_VERIFY_FAILED")
        job.error_code = None
        job.error_message = None
        self._transition(job, JobState.DOWNLOADING)
        return job

    def retry_remote_artifact_delete(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.raw_path or not Path(job.raw_path).is_file():
            raise ValueError("verified RAW is required before remote cleanup retry")
        self.notebook.delete_video_artifact(job, job.safety_gate)
        if job.error_code == "REMOTE_ARTIFACT_DELETE_FAILED":
            job.error_code = None
            job.error_message = None
            self._save(job)
        return job

    def create_retry(self, job_id: str, restart_at: JobState) -> Job:
        previous = self.jobs.get(job_id)
        if previous is None:
            raise KeyError(job_id)
        if previous.state is not JobState.FAILED:
            raise ValueError("failed job is required")
        allowed = {JobState.WAITING, JobState.RAW_READY, JobState.ENDING, JobState.HLS_ENCODING}
        if restart_at not in allowed:
            raise ValueError(f"unsupported retry state: {restart_at}")
        retried = Job(
            source_path=previous.source_path,
            parent_job_id=previous.id,
            state=restart_at,
            notebook_id=previous.notebook_id,
            notebook_url=previous.notebook_url,
            raw_path=previous.raw_path,
            edited_path=previous.edited_path,
            safety_gate=previous.safety_gate,
            attempt_by_stage={
                **previous.attempt_by_stage,
                restart_at.value: previous.attempt_by_stage.get(restart_at.value, 0) + 1,
            },
        )
        self._save(retried)
        return retried
