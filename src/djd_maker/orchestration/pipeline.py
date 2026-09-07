from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import time
import threading
from pathlib import Path
from typing import Any, Protocol
from zipfile import BadZipFile, ZipFile

from djd_maker.core.interfaces import (
    EndingEngine,
    HlsEngine,
    NotebookEngine,
    require_remote_deletion_gate,
)
from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.repositories import JobStateSaveError
from djd_maker.core.cancellation import RunCancelled, checkpoint
from djd_maker.adapters.notebook_modal import BlockingModalError
from djd_maker.core.runtime_operation import operation_scope, report_operation


class NoOpJobTransitionError(RuntimeError):
    pass


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
    ending_video: Path | None


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
            JobState.DOWNLOAD_PENDING,
        }
    )
    RECOVERY_STATES = frozenset(
        {
            JobState.RESERVED_WAITING_CREDIT_RESET,
            JobState.WAITING_VIDEO,
            JobState.DOWNLOAD_PENDING,
            JobState.RECOVERY_PENDING,
        }
    )
    MEDIA_STATES = frozenset(
        {JobState.RAW_READY, JobState.ENDING, JobState.HLS_ENCODING, JobState.ZIPPING}
    )
    STATE_PROGRESS = {
        JobState.WAITING: 0.0,
        JobState.UPLOADING: 5.0,
        JobState.CREDIT_EXHAUSTED: 10.0,
        JobState.RESERVED_WAITING_CREDIT_RESET: 15.0,
        JobState.RECOVERY_PENDING: 20.0,
        JobState.GENERATING: 10.0,
        JobState.WAITING_VIDEO: 20.0,
        JobState.DOWNLOAD_PENDING: 25.0,
        JobState.DOWNLOADING: 30.0,
        JobState.DOWNLOAD_VERIFY_FAILED: 30.0,
        JobState.RAW_READY: 40.0,
        JobState.ENDING: 55.0,
        JobState.HLS_ENCODING: 75.0,
        JobState.ZIPPING: 90.0,
        JobState.COMPLETED: 100.0,
    }

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
        generation_preset: Preset | None = None,
    ) -> None:
        if ffmpeg_concurrency not in {1, 2}:
            raise ValueError("ffmpeg_concurrency must be 1 or 2")
        if paths.ending_video is not None and not paths.ending_video.is_file():
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
        self.generation_preset = generation_preset
        self.runtime_callback = lambda _record: None
        self._resume_attempted: set[str] = set()
        self._no_op_count = 0
        self._idle_announced: dict[str, tuple] = {}
        self._media_progress_lock = threading.Lock()
        self._media_completed = 0

    def begin_run(self) -> None:
        """Reset local queue bookkeeping only; never opens a Notebook."""
        self._resume_attempted.clear()
        self._no_op_count = 0
        self._idle_announced.clear()

    def _save(self, job: Job) -> None:
        self.jobs.save(job)

    def _transition(self, job: Job, target: JobState) -> None:
        job.transition_to(target)
        if target in self.STATE_PROGRESS:
            job.progress_percent = self.STATE_PROGRESS[target]
        self._save(job)
        report_operation('state.saved', decision=target.value, notebook=job.notebook_url or '－')
        if target is JobState.COMPLETED:
            self.reconcile_completed_txt()
            archived = self.jobs.get(job.id)
            if archived is not None:
                job.txt_move_status = archived.txt_move_status
                job.archived_txt_path = archived.archived_txt_path
                job.source_sha256 = archived.source_sha256

    def reconcile_completed_txt(self) -> int:
        from djd_maker.core.completed_txt import reconcile_completed_txt
        return reconcile_completed_txt(self.jobs, self.paths.raw_directory)

    def resume_failed_jobs(self, job_ids: set[str] | None = None) -> list[str]:
        """Once per human Start; keep identity and original preset snapshot."""
        from djd_maker.core.job_migration import failure_class
        resumed = []
        candidates = self.jobs.list() if job_ids is None else [self.jobs.get(job_id) for job_id in sorted(job_ids)]
        for job in candidates:
            if job is None:
                continue
            checkpoint('resume.dequeue', job.id)
            interrupted = job.state is JobState.RECOVERY_PENDING and (job.resume_checkpoint or '').startswith('STOPPED:')
            if job.state is not JobState.FAILED and not interrupted:
                continue
            if job_ids is not None and job.id not in job_ids:
                continue
            if job.failure_class == 'FATAL_FAILED':
                continue
            job.failure_class = failure_class(job)
            if job.failure_class == 'FATAL_FAILED':
                continue
            target = None
            try:
                if job.raw_path and Path(job.raw_path).is_file():
                    if getattr(self.validator.validate(Path(job.raw_path)), "valid", True):
                        target = JobState.RAW_READY
                        if job.edited_path and Path(job.edited_path).is_file() and getattr(self.validator.validate(Path(job.edited_path)), "valid", True):
                            target = JobState.ZIPPING if job.resume_checkpoint == "ZIPPING" else JobState.HLS_ENCODING
                elif job.notebook_id and job.notebook_url:
                    diagnose = getattr(self.notebook, "diagnose_resume", None)
                    diagnosis = diagnose(job) if callable(diagnose) else {"artifact": self.notebook.inspect_status(job)}
                    status = diagnosis["artifact"]
                    job.source_status = diagnosis.get("source", job.source_status)
                    if job.failure_class != "FATAL_FAILED":
                        if job.source_status in {"ERROR", "MISSING"}:
                            job.failure_class = "SOURCE_UPLOAD_FAILED"
                        elif diagnosis.get("reply") == "QUOTA_EXHAUSTED":
                            job.failure_class = "QUOTA_RECOVERY_PENDING"
                        elif diagnosis.get("preset") == "NOT_SENT":
                            job.failure_class = "PRESET_SEND_FAILED"
                        elif diagnosis.get("reply") == "NO_RESPONSE":
                            job.failure_class = "PRESET_RESPONSE_TIMEOUT"
                    job.artifact_status = status
                    target = {"READY": JobState.DOWNLOAD_PENDING, "GENERATING": JobState.WAITING_VIDEO, "WAITING": JobState.RESERVED_WAITING_CREDIT_RESET}.get(status)
                    if status == "NOT_STARTED" and job.failure_class != "FATAL_FAILED":
                        target = JobState.WAITING_VIDEO if diagnosis.get("reply") == "GENERATION_ACCEPTED" else JobState.WAITING
            except BlockingModalError:
                raise
            except JobStateSaveError:
                raise
            except Exception as exc:
                job.error_message = f"RESUME_DIAGNOSIS_FAILED: {exc}"
            if target is not None:
                job.state = target
                job.resume_checkpoint = target.value
                job.error_code = None
                job.error_message = None
                resumed.append(job.id)
            self._save(job)
        return resumed

    def run_cycle(self) -> None:
        self._reject_output_name_collisions()
        # Preserve the existing bounded FFmpeg lane for RAW already on disk.
        # This is local-only: no Notebook is inspected/opened by these workers.
        local_media = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES]
        if local_media:
            self._media_completed = 0
            with ThreadPoolExecutor(max_workers=self.ffmpeg_concurrency, thread_name_prefix='djd-ffmpeg') as pool:
                futures = []
                for index, job in enumerate(local_media, 1):
                    checkpoint('media.dequeue', job.id)
                    self._resume_attempted.add(job.id)
                    futures.append(pool.submit(copy_context().run, self._local_media_with_progress, job, index, len(local_media)))
                for future in futures:
                    future.result()
        values = self.jobs.list()
        # Local ordering only. No remote pre-scan and no second execution queue.
        values.sort(key=lambda j: 0 if j.state in self.MEDIA_STATES or j.state is JobState.DOWNLOAD_PENDING else 1)
        for index, job in enumerate(values, 1):
            checkpoint('queue.dequeue', job.id)
            idle = self._idle_reason(job)
            signature = (idle, job.state, job.next_poll_at, job.credit_reset_at, job.error_code)
            if idle and self._idle_announced.get(job.id) == signature:
                continue
            if idle:
                self._idle_announced[job.id] = signature
            else:
                self._idle_announced.pop(job.id, None)
            started = time.monotonic()
            record = dict(job=job.script_name, job_id=job.id, notebook=job.notebook_url or '－',
                          phase='逐次処理', decision='－', next_action='状態確認', outcome='－',
                          attempt='－', processed=index-1, total=len(values), started=started)
            def update(stage, fields):
                record.update(fields)
                record.update(stage=stage, elapsed=time.monotonic()-started)
                self.runtime_callback(dict(record))
            with operation_scope(update):
                report_operation('job.start')
                try:
                    if idle:
                        self._finish_job(job, idle)
                    else:
                        self._check_act_job(job)
                except RunCancelled:
                    job.runtime_outcome = 'STOPPED'
                    job.runtime_reason = 'STOP_REQUESTED'
                    self._save(job)
                    report_operation('stop.complete', outcome='STOPPED')
                    raise
                except BlockingModalError:
                    job.runtime_outcome = 'STOPPED'
                    job.runtime_reason = 'BLOCKING_MODAL'
                    self._save(job)
                    report_operation('modal.blocked', outcome='STOPPED')
                    raise
                report_operation('job.next', processed=index, next_action='次のジョブ')

    def _local_media_with_progress(self, job: Job, index: int, total: int) -> None:
        started = time.monotonic()
        record = dict(job=job.script_name, job_id=job.id, notebook=job.notebook_url or '－',
                      phase='保存済みRAWの変換', processed=self._media_completed, total=total, started=started)
        def update(stage, fields):
            record.update(fields)
            record.update(stage=stage, elapsed=time.monotonic()-started)
            self.runtime_callback(dict(record))
        with operation_scope(update):
            try:
                self._run_media_job(job)
            except RunCancelled:
                job.runtime_outcome = 'STOPPED'
                job.runtime_reason = 'STOP_REQUESTED'
                self._save(job)
                report_operation('stop.complete', outcome='STOPPED')
                raise
            job.runtime_outcome = job.state.value
            job.runtime_reason = job.error_code or job.state.value
            self._save(job)
            # Media state is already durably saved; queue bookkeeping stays on
            # the Notebook owner thread and is not shared across FFmpeg workers.
            with self._media_progress_lock:
                self._media_completed += 1
                report_operation('job.result', outcome=job.state.value, processed=self._media_completed)

    def _idle_reason(self, job: Job) -> str | None:
        if job.state is JobState.COMPLETED:
            return 'COMPLETED_SKIP'
        if job.state is JobState.DOWNLOAD_VERIFY_FAILED:
            return 'DOWNLOAD_RETRY_REQUIRED'
        if job.state is JobState.FAILED:
            from djd_maker.core.job_migration import failure_class
            if job.failure_class == 'FATAL_FAILED' or failure_class(job) == 'FATAL_FAILED' or job.error_code == 'OUTPUT_NAME_COLLISION':
                return 'FATAL_FAILED'
            if job.id in self._resume_attempted:
                return 'RETRY_REQUIRES_START'
        if job.state is JobState.WAITING_VIDEO and self.scheduler is not None:
            self.scheduler.ensure_scheduled(job)
            if not self.scheduler.is_due(job):
                return 'WAITING_FOR_NEXT_CHECK'
        if job.state in {JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING} and not (job.resume_checkpoint or '').startswith('STOPPED:'):
            now = datetime.now(UTC)
            deadlines = [self._parse_utc(v) for v in (job.next_poll_at, job.credit_reset_at)]
            if any(deadline and now < deadline for deadline in deadlines):
                return 'RESERVED_WAITING_RESET'
        return None

    def _finish_job(self, job: Job, reason: str, *, no_op: bool = False) -> None:
        if reason == 'COMPLETED_SKIP':
            report_operation('job.result', decision=reason, outcome='COMPLETED')
            self._no_op_count = 0
            return
        previous = (job.runtime_outcome, job.runtime_reason)
        if no_op:
            job.error_code = 'NO_OP_JOB_TRANSITION'
            job.error_message = reason
            if job.state is not JobState.FAILED:
                self._transition(job, JobState.FAILED)
        job.runtime_outcome = 'FAILED_WITH_REASON' if job.state is JobState.FAILED else job.state.value
        job.runtime_reason = reason
        # Completed records must remain immutable (TXT reconciliation is local).
        if previous != (job.runtime_outcome, job.runtime_reason):
            self._save(job)
        report_operation('job.result', decision=reason, outcome=job.runtime_outcome,
                         notebook=job.notebook_url or '－')
        self._no_op_count = self._no_op_count + 1 if no_op else 0
        if self._no_op_count >= 3:
            raise NoOpJobTransitionError('NO_OP_JOB_TRANSITION: Notebookを開きましたが処理工程を開始できない状態が3件続いています')

    def _check_act_job(self, job: Job) -> None:
        if job.state is JobState.COMPLETED:
            self._finish_job(job, 'COMPLETED_SKIP')
            return
        interrupted = job.state is JobState.RECOVERY_PENDING and (job.resume_checkpoint or '').startswith('STOPPED:')
        if job.state is JobState.FAILED or interrupted:
            from djd_maker.core.job_migration import failure_class
            if failure_class(job) == 'FATAL_FAILED':
                self._finish_job(job, 'FATAL_FAILED')
                return
            if job.id in self._resume_attempted:
                self._finish_job(job, 'RETRY_REQUIRES_START')
                return
            self._resume_attempted.add(job.id)
            report_operation('resume.check', phase='再開処理')
            self.resume_failed_jobs({job.id})
            resumed = self.jobs.get(job.id)
            assert resumed is not None
            # Keep the current object so cancellation persists this same job.
            for field in job.__dataclass_fields__:
                setattr(job, field, getattr(resumed, field))
            report_operation('resume.decision', decision=job.state.value, next_action='必要工程を即実行')
            if job.state is JobState.FAILED:
                self._finish_job(job, 'REMOTE_STATE_UNKNOWN', no_op=True)
                return
            if job.state is JobState.WAITING_VIDEO:
                if self.scheduler is not None:
                    self.scheduler.schedule_generation(job, force=True)
                self._finish_job(job, 'GENERATION_ALREADY_STARTED')
                return

        if job.state is JobState.WAITING_VIDEO and self.scheduler is not None:
            self.scheduler.ensure_scheduled(job)
            if not self.scheduler.claim_next_poll(job):
                self._finish_job(job, 'WAITING_FOR_NEXT_CHECK')
                return

        if job.state in {JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING}:
            now = datetime.now(UTC)
            deadline = self._parse_utc(job.next_poll_at)
            reset = self._parse_utc(job.credit_reset_at)
            if (deadline and now < deadline) or (reset and now < reset):
                self._finish_job(job, 'RESERVED_WAITING_RESET')
                return
            self._recover_remote_job(job, checked_at=now)
            if job.state in self.RECOVERY_STATES:
                job.next_poll_at = (now + timedelta(seconds=getattr(self.scheduler, 'subsequent_poll_seconds', 120))).isoformat()
                self._save(job)
        elif job.state in self.NOTEBOOK_STATES:
            if job.state is JobState.DOWNLOAD_VERIFY_FAILED:
                self._finish_job(job, 'DOWNLOAD_RETRY_REQUIRED')
                return
            before = job.to_dict()
            self._resume_attempted.add(job.id)
            self._run_notebook_job(job)
            if job.to_dict() == before and job.state is not JobState.WAITING_VIDEO:
                self._finish_job(job, 'NO_OP_JOB_TRANSITION', no_op=True)
                return
        if job.state in self.MEDIA_STATES:
            self._run_media_job(job)
        self._finish_job(job, job.error_code or ('GENERATION_ALREADY_STARTED' if job.state is JobState.WAITING_VIDEO else job.state.value))

    def run_recovery_cycle(self, *, now: datetime | None = None) -> list[str]:
        """Advance only persisted remote/recovery jobs; never submit new work."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        processed: list[str] = []
        values = self.jobs.list()
        for index, job in enumerate(values, 1):
            checkpoint('recovery.dequeue', job.id)
            if job.state not in self.RECOVERY_STATES:
                continue
            deadline = self._parse_utc(job.next_poll_at)
            if deadline is not None and current < deadline:
                continue
            if job.state is JobState.RESERVED_WAITING_CREDIT_RESET:
                reset_at = self._parse_utc(job.credit_reset_at)
                if reset_at is not None and current < reset_at:
                    continue
            processed.append(job.id)
            started = time.monotonic()
            record = dict(job=job.script_name, job_id=job.id, notebook=job.notebook_url or '－',
                          phase='回収処理', processed=index-1, total=len(values), started=started)
            def update(stage, fields):
                record.update(fields)
                record.update(stage=stage, elapsed=time.monotonic()-started)
                self.runtime_callback(dict(record))
            with operation_scope(update):
                report_operation('job.start')
                self._recover_remote_job(job, checked_at=current)
                if job.state in self.MEDIA_STATES:
                    self._run_media_job(job)
                self._finish_job(job, job.error_code or job.state.value)
                report_operation('job.next', processed=index)

        media_jobs = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES]
        with ThreadPoolExecutor(
            max_workers=self.ffmpeg_concurrency, thread_name_prefix="djd-ffmpeg"
        ) as pool:
            futures = []
            for job in media_jobs:
                checkpoint('media.dequeue')
                futures.append(pool.submit(copy_context().run, self._run_media_job, job))
            for future in futures:
                future.result()
        return processed

    @staticmethod
    def _parse_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("credit_reset_at must be timezone-aware")
        return parsed.astimezone(UTC)

    def _recover_remote_job(self, job: Job, *, checked_at: datetime) -> None:
        """Read the existing Notebook and continue at download only when ready."""
        if not job.notebook_id or not job.notebook_url:
            job.error_code = "NOTEBOOK_RESUME_METADATA_MISSING"
            job.error_message = "回収対象Notebookの識別情報がありません"
            if job.state is not JobState.FAILED:
                self._transition(job, JobState.FAILED)
            return
        try:
            status = self.notebook.inspect_status(job)
            job.last_checked_at = checked_at.isoformat()
            job.artifact_status = status
            if status == "READY":
                report_operation('artifact.ready', decision='READY', next_action='Download開始')
                if job.state is not JobState.DOWNLOAD_PENDING:
                    self._transition(job, JobState.DOWNLOAD_PENDING)
                self._run_notebook_job(job)
                return
            if status == "FAILED":
                raise RuntimeError("remote video generation failed")
            if status == 'GENERATING' and job.state in {JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING}:
                self._transition(job, JobState.WAITING_VIDEO)
            job.next_poll_at = (checked_at + timedelta(seconds=getattr(self.scheduler, 'subsequent_poll_seconds', 120))).isoformat()
            self._save(job)
        except BlockingModalError:
            # An ambiguous dialog stops the run, not just this recovery item.
            raise
        except JobStateSaveError:
            # The repository already exhausted its bounded Windows-sharing
            # retries. Preserve remote/local artifacts and surface one terminal
            # operation error to the GUI; do not rewrite the job as FAILED.
            raise
        except Exception as exc:
            job.recovery_retry_count += 1
            job.error_code = "RECOVERY_CHECK_FAILED"
            job.error_message = str(exc)
            if job.state not in {
                JobState.RESERVED_WAITING_CREDIT_RESET,
                JobState.RECOVERY_PENDING,
            }:
                self._transition(job, JobState.RECOVERY_PENDING)
            else:
                self._save(job)

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
                checkpoint('notebook.submit')
                if self.generation_preset is None:
                    raise RuntimeError("PRESET_NOT_SELECTED")
                if job.preset_body_snapshot is None:
                    job.snapshot_preset(self.generation_preset)
                self._transition(job, JobState.UPLOADING)
                submission = self.notebook.submit(job)
                if hasattr(submission, "notebook_id"):
                    notebook_id = submission.notebook_id
                    notebook_url = submission.notebook_url
                else:
                    notebook_id, notebook_url = submission
                job.notebook_id = notebook_id
                job.notebook_url = notebook_url
                if bool(getattr(submission, "reserved", False)):
                    snapshot = getattr(submission, "credit", None)
                    job.credit_state = "CREDIT_EXHAUSTED"
                    job.credit_percent = getattr(snapshot, "percent", None)
                    reset_at = getattr(snapshot, "reset_at", None)
                    job.credit_reset_at = reset_at.isoformat() if reset_at else None
                    job.reservation_created_at = getattr(
                        submission, "reservation_created_at", None
                    )
                    job.expected_generation_after = job.credit_reset_at
                    job.artifact_status = "SCHEDULED_REMOTE"
                    self._transition(job, JobState.CREDIT_EXHAUSTED)
                    self._transition(job, JobState.RESERVED_WAITING_CREDIT_RESET)
                    return
                self._transition(job, JobState.GENERATING)
                if getattr(submission, 'remote_status', None) == 'READY':
                    self._transition(job, JobState.DOWNLOADING)
                if self.scheduler is not None:
                    if job.state is JobState.GENERATING:
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
                checkpoint('artifact.poll')
                status = self.notebook.inspect_status(job)
                job.artifact_status = status
                job.last_checked_at = datetime.now(UTC).isoformat()
                self._save(job)
                if status != "READY":
                    if status == "FAILED":
                        raise RuntimeError("remote video generation failed")
                    return
                report_operation('artifact.ready', decision='READY', next_action='Download開始')
                self._transition(job, JobState.DOWNLOAD_PENDING)

            if job.state is JobState.DOWNLOAD_PENDING:
                job.download_status = "PENDING"
                self._transition(job, JobState.DOWNLOADING)

            if job.state is JobState.DOWNLOADING:
                checkpoint('download.start')
                download = (
                    self.paths.work_directory
                    / job.id
                    / "download"
                    / f"{job.script_name}.mp4"
                )
                if not download.exists():
                    self.notebook.download_artifact(job, download)
                job.download_status = "DOWNLOADED"
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
                job.raw_status = "READY"
                self._transition(job, JobState.RAW_READY)
                report_operation('raw.saved', decision='RAW_READY', next_action='artifact削除安全Gateを確認')
                try:
                    require_remote_deletion_gate(gate)
                    self.notebook.delete_video_artifact(job, gate)
                except BlockingModalError:
                    raise
                except Exception as exc:
                    # RAW is already durable and verified. Remote cleanup can be
                    # retried independently and must not destroy local progress.
                    job.error_code = "REMOTE_ARTIFACT_DELETE_FAILED"
                    job.error_message = str(exc)
                    self._save(job)
        except (RunCancelled, BlockingModalError):
            job.resume_checkpoint = 'STOPPED:' + job.state.value
            if job.state is JobState.UPLOADING:
                job.state = JobState.RECOVERY_PENDING
            self._save(job)
            raise
        except JobStateSaveError:
            raise
        except Exception as exc:
            job.error_message = str(exc)
            job.resume_checkpoint = job.state.value
            if job.state is JobState.DOWNLOADING:
                job.error_code = "DOWNLOAD_VERIFY_FAILED"
                self._transition(job, JobState.DOWNLOAD_VERIFY_FAILED)
            elif job.state not in {JobState.FAILED, JobState.COMPLETED}:
                failure_code = str(exc).split(":", 1)[0]
                if failure_code in {
                    "PRESET_APPLY_MISMATCH", "PRESET_RESPONSE_TIMEOUT",
                    "WRONG_INPUT_TARGET", "CHAT_HISTORY_CHANGED",
                    "SOURCE_UPLOAD_FAILED", "SOURCE_IDENTITY_CHANGED",
                    "SOURCE_IDENTITY_AMBIGUOUS", "GENERATION_STATE_UNCERTAIN",
                }:
                    job.error_code = failure_code
                else:
                    job.error_code = job.error_code or "NOTEBOOK_STAGE_FAILED"
                self._transition(job, JobState.FAILED)

    def _run_media_job(self, job: Job) -> None:
        try:
            checkpoint('media.start', job.id)
            raw = Path(job.raw_path or "")
            edited = (
                self.paths.work_directory / job.id / "ending" / f"{job.script_name}.mp4"
            )
            output_zip = self.paths.output_directory / f"{job.script_name}.zip"

            if job.state is JobState.RAW_READY:
                self._transition(job, JobState.ENDING)

            if job.state is JobState.ENDING:
                if self.paths.ending_video is None:
                    validated = self.validator.validate(raw)
                    if not getattr(validated, 'valid', True):
                        raise ValueError('Endingスキップ元RAWの検証に失敗しました')
                    job.edited_path = str(raw)
                    job.ending_result = 'SKIPPED (not configured)'
                    report_operation('ending.skip', decision='RAW_READY', next_action='HLS変換')
                    self._transition(job, JobState.HLS_ENCODING)

            if job.state is JobState.ENDING:
                existing_is_valid = False
                if edited.exists():
                    try:
                        existing = self.validator.validate(edited)
                        existing_is_valid = getattr(existing, "valid", True)
                    except Exception:
                        existing_is_valid = False
                if not existing_is_valid:
                    report_operation('ending.start', next_action='Ending結合後HLS変換')
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
                resuming_zip_publish = job.state is JobState.ZIPPING
                if job.state is JobState.HLS_ENCODING:
                    self._transition(job, JobState.ZIPPING)
                if output_zip.exists():
                    if not resuming_zip_publish:
                        raise FileExistsError(
                            f"既存ZIPを別工程の成果物として採用しません: {output_zip}"
                        )
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
        except RunCancelled:
            job.resume_checkpoint = 'STOPPED:' + job.state.value
            self._save(job)
            raise
        except JobStateSaveError:
            raise
        except Exception as exc:
            job.error_code = "MEDIA_STAGE_FAILED"
            if isinstance(exc, FileExistsError):
                job.failure_class = 'FATAL_FAILED'
            job.resume_checkpoint = job.state.value
            job.error_message = str(exc)
            if job.state not in {JobState.FAILED, JobState.COMPLETED}:
                self._transition(job, JobState.FAILED)

    def _reject_output_name_collisions(self) -> None:
        """Fail duplicate active stems before either job can claim shared paths."""

        by_stem: dict[str, list[Job]] = {}
        for job in self.jobs.list():
            if job.state is not JobState.FAILED:
                by_stem.setdefault(job.script_name.casefold(), []).append(job)
        for duplicates in by_stem.values():
            if len(duplicates) < 2:
                continue
            completed = [job for job in duplicates if job.state is JobState.COMPLETED]
            owner = min(
                completed or duplicates,
                key=lambda item: (item.created_at, item.id),
            )
            for job in duplicates:
                if job.id == owner.id or job.state in {JobState.COMPLETED, JobState.FAILED}:
                    continue
                job.error_code = "OUTPUT_NAME_COLLISION"
                job.error_message = (
                    f"output stem {job.script_name!r} is already owned by job {owner.id}"
                )
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
        require_remote_deletion_gate(job.safety_gate)
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
        self.resume_failed_jobs({previous.id})
        retried = self.jobs.get(previous.id)
        assert retried is not None
        if retried.state is JobState.FAILED:
            raise ValueError("再開checkpointを確認できません。既存jobと成果物は保持しました")
        retried.attempt_by_stage[retried.state.value] = retried.attempt_by_stage.get(retried.state.value, 0) + 1
        self._save(retried)
        return retried
