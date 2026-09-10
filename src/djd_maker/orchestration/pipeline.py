from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
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
from djd_maker.core.cloud_limit import CloudLimitGate, CloudLimitReached
from djd_maker.adapters.notebook_modal import BlockingModalError
from djd_maker.adapters.notebook import SourceRetryExhausted, GenerationRetryExhausted
from djd_maker.core.runtime_operation import operation_scope, report_operation, local_task_scope, LocalTaskComplete


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


from .deferred_recovery import DeferredRecovery
from .task_discovery import Capabilities, RESCAN_SECONDS, MAX_ERROR_ATTEMPTS, due, terminal, remote_reconcile_candidate
from djd_maker.core.deferred_state import SaveDeferred


class PipelineCoordinator(DeferredRecovery):
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
        cloud_limit: CloudLimitGate | None = None,
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
        self.cloud_limit = cloud_limit or CloudLimitGate(
            Path(getattr(jobs, 'directory', paths.work_directory / 'jobs')).parent / 'cloud-limit.json')
        self.runtime_callback = lambda _record: None
        self.job_callback = lambda _job: None
        self.phase = 'GENERATION_DISPATCH'
        self._dispatch_only = False
        self._progress_jobs = {}
        self._resume_attempted: set[str] = set()
        self._no_op_count = 0
        self._idle_announced: dict[str, tuple] = {}
        self._media_progress_lock = threading.Lock()
        self._media_completed = 0
        self.capabilities = Capabilities.from_limit(self.cloud_limit.blocked)
        self.wait_seconds = 0
        self.scheduler_view = {}
        self._init_deferred()
        if hasattr(self.notebook, 'persist_identity'):
            self.notebook.persist_identity = self._save

    def begin_run(self) -> None:
        """Reset local queue bookkeeping only; never opens a Notebook."""
        self._resume_attempted.clear()
        self._no_op_count = 0
        self._idle_announced.clear()
        self._deferred_attempts.clear()
        for entry in self.deferred.entries.values():
            self._deferred_notice(entry, 'save.deferred', '保存保留journalを検出しました。再実行前に成果物を照合します。')

    def _save(self, job: Job) -> None:
        if job.id in self.deferred_ids:
            raise SaveDeferred()
        job.presentation_revision += 1
        try:
            self.jobs.save(job)
        except JobStateSaveError as error:
            self._defer(job, error)
            raise SaveDeferred() from error
        self._progress_jobs[job.id] = Job.from_dict(job.to_dict())
        self.job_callback(Job.from_dict(job.to_dict()))

    def _counts(self) -> str:
        jobs = list(self._progress_jobs.values())
        if self._dispatch_only:
            generated = sum(j.state in {JobState.GENERATING,JobState.WAITING_VIDEO,JobState.DOWNLOAD_PENDING,JobState.DOWNLOADING,JobState.COMPLETED} or bool(j.raw_path) for j in jobs)
            return f'生成開始済: {generated}/{len(jobs)} / 旧予約: {sum(j.state is JobState.RESERVED_WAITING_CREDIT_RESET for j in jobs)} / 残り: {sum(self._needs_dispatch(j) for j in jobs)}'
        return f'回収済: {sum(bool(j.raw_path) for j in jobs)}/{len(jobs)} / HLS完了: {sum(j.hls_result == "PASS" for j in jobs)} / ZIP完了: {sum(j.state is JobState.COMPLETED for j in jobs)}'

    def _stage_event(self, job: Job, stage: str) -> None:
        if stage in {'state.saved', 'job.start', 'job.result', 'job.next', 'limit.warning'} or job.state is JobState.COMPLETED:
            return
        if job.presentation_stage != stage or job.presentation_phase != self.phase:
            if stage == 'zip.start' and job.state is JobState.HLS_ENCODING:
                job.transition_to(JobState.ZIPPING)
            if stage == 'hls.complete':
                job.hls_result = 'PASS'
            job.presentation_stage = stage
            job.presentation_phase = self.phase
            self._save(job)

    @staticmethod
    def _local_checkpoint_fields(job, fields):
        for name in ('hls_checkpoint_directory', 'hls_source_sha256', 'zip_checkpoint_path'):
            if fields.get(name):
                setattr(job, name, fields[name])

    def _transition(self, job: Job, target: JobState) -> None:
        job.transition_to(target)
        job.presentation_stage = target.value
        job.presentation_phase = self.phase
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
                self.job_callback(Job.from_dict(archived.to_dict()))

    def reconcile_completed_txt(self) -> int:
        from djd_maker.core.completed_txt import reconcile_completed_txt
        return reconcile_completed_txt(self.jobs, self.paths.raw_directory)

    def resume_failed_jobs(self, job_ids: set[str] | None = None) -> list[str]:
        """Once per human Start; keep identity and original preset snapshot."""
        from djd_maker.core.job_migration import failure_class
        self._reject_output_name_collisions()
        resumed = []
        candidates = self.jobs.list() if job_ids is None else [self.jobs.get(job_id) for job_id in sorted(job_ids)]
        for job in candidates:
            if job is None or job.id in self.deferred_ids or job.failure_class == 'OUTPUT_BLOCKED':
                continue
            checkpoint('resume.dequeue', job.id)
            interrupted = job.state is JobState.RECOVERY_PENDING and (job.resume_checkpoint or '').startswith('STOPPED:')
            if job.state is not JobState.FAILED and not interrupted:
                continue
            if job_ids is not None and job.id not in job_ids:
                continue
            if remote_reconcile_candidate(job):
                with self._job_boundary(job):
                    self._reconcile_failed_remote(job)
                    if job.state is not JobState.FAILED:
                        resumed.append(job.id)
                continue
            if terminal(job) or job.failure_class == 'FATAL_FAILED':
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
            with self._job_boundary(job):
                self._save(job)
                if target is not None:
                    resumed.append(job.id)
        return resumed

    def _needs_dispatch(self, job: Job) -> bool:
        if job.id in self.deferred_ids or terminal(job):
            return False
        if job.state in {JobState.WAITING, JobState.UPLOADING}:
            return True
        if job.state is JobState.FAILED:
            from djd_maker.core.job_migration import failure_class
            if remote_reconcile_candidate(job) and (
                    failure_class(job) not in {'SOURCE_UPLOAD_FAILED', 'PRESET_SEND_FAILED',
                                              'PRESET_RESPONSE_TIMEOUT', 'QUOTA_RECOVERY_PENDING'}
                    or job.raw_path or job.edited_path or job.hls_checkpoint_directory
                    or job.artifact_status == 'READY'
                    or any(job.attempt_by_stage.get(k, 0) >= MAX_ERROR_ATTEMPTS
                           for k in ('source.reupload', 'generation.failed_retry'))):
                return False  # Unknown progress: P3. Known retry: P1 check-before-act.
            if job.failure_class == 'OUTPUT_BLOCKED' or job.error_code == 'OUTPUT_NAME_COLLISION':
                return False
            return (not job.raw_path and failure_class(job) != 'FATAL_FAILED'
                    and job.attempt_by_stage.get('scheduler.recovery', 0) < MAX_ERROR_ATTEMPTS
                    and due(job, self.cloud_limit.clock()))
        return job.state is JobState.RECOVERY_PENDING and (job.resume_checkpoint or '').startswith('STOPPED:') and job.id not in self._resume_attempted

    def _phase_event(self, phase: str) -> None:
        self.phase = phase
        jobs = self.jobs.list()
        generated = sum(j.state in {JobState.GENERATING, JobState.WAITING_VIDEO, JobState.DOWNLOAD_PENDING, JobState.DOWNLOADING} or j.raw_path is not None or j.state is JobState.COMPLETED for j in jobs)
        reserved = sum(j.state is JobState.RESERVED_WAITING_CREDIT_RESET for j in jobs)
        remaining = sum(self._needs_dispatch(j) for j in jobs)
        summary = (f'生成開始済: {generated}/{len(jobs)} / 旧予約: {reserved} / 残り: {remaining}' if phase == 'GENERATION_DISPATCH'
                   else f'回収済: {sum(bool(j.raw_path) for j in jobs)}/{len(jobs)} / HLS完了: {sum(j.hls_result == "PASS" for j in jobs)} / ZIP完了: {sum(j.state is JobState.COMPLETED for j in jobs)}')
        summary += f' / 状態保存保留: {len(self.deferred_ids)}'
        decision = ('保存保留jobは未解決のまま隔離し、安全な他jobの回収を続行します' if self.deferred_ids
                    else '全jobの生成投入判定が完了しました')
        self.runtime_callback(dict(phase='動画生成開始フェーズ' if phase == 'GENERATION_DISPATCH' else '動画回収・変換フェーズ',
                                   stage='phase.a' if phase == 'GENERATION_DISPATCH' else 'phase.b', phase_counts=summary,
                                   next_action=summary, decision=decision if phase == 'COLLECT_LOCAL' else '未生成jobを先に処理'))

    def run_cycle(self) -> None:
        checkpoint('pipeline.cycle')
        for job in self.jobs.list():
            if terminal(job) or job.id in self.deferred_ids or not job.next_poll_at:
                continue
            try:
                self._parse_utc(job.next_poll_at)
            except (ValueError, TypeError, AttributeError):
                with self._job_boundary(job):
                    job.resume_checkpoint = job.state.value
                    job.state = JobState.FAILED
                    job.error_code = 'INVALID_CHECK_DEADLINE'
                    job.error_message = '保存済み再確認日時が不正です。既存Notebookを再診断します。'
                    job.next_poll_at = None
                    self._save(job)
        self._refresh_capabilities()
        self._reject_output_name_collisions()
        self._progress_jobs = {j.id:j for j in self.jobs.list()}
        if not self.cloud_limit.blocked and any(self._needs_dispatch(j) for j in self.jobs.list()):
            self.wait_seconds = 0
            self._scheduler_status(1, '生成投入')
            self._phase_event('GENERATION_DISPATCH')
            self._dispatch_only = True
            try:
                self._run_cycle_lane()
            finally:
                self._dispatch_only = False
            checkpoint('phase.dispatch.complete')
            self._retry_deferred()
            if self._generation_preempts():
                return
        if self.phase != 'COLLECT_LOCAL':
            if self.deferred_ids:
                self._retry_deferred()
            self._phase_event('COLLECT_LOCAL')
        self._discover_remaining_tasks()

    def _refresh_capabilities(self):
        """Recheck on the browser owner thread, never inside an FFmpeg worker."""
        checkpoint('capability.refresh')
        if self.cloud_limit.blocked:
            if self.cloud_limit.due:
                recheck = getattr(self.notebook, 'recheck_cloud_limit', None)
                if callable(recheck):
                    try:
                        if recheck(self.cloud_limit.state.get('notebook_url')):
                            self.cloud_limit.release()
                        else:
                            self.cloud_limit.defer_recheck()
                    except CloudLimitReached as exc:
                        self.cloud_limit.block(exc.observation)
                    except BlockingModalError:
                        raise
                    except Exception as exc:
                        self.cloud_limit.defer_recheck()
                        self.runtime_callback(dict(stage='limit.recheck.failed', level='WARNING',
                            message=f'上限解除を確認できません。待機を維持して再確認します: {type(exc).__name__}'))
        self.capabilities = Capabilities.from_limit(self.cloud_limit.blocked)

    def _generation_preempts(self):
        self._refresh_capabilities()
        return self.capabilities.can_generate and any(self._needs_dispatch(j) for j in self.jobs.list())

    def all_tasks_completed(self):
        return self.final_discovery()['complete']

    def final_discovery(self):
        """Fresh durable scan immediately before automatic completion."""
        from .task_discovery import final_discovery
        result = final_discovery(self.jobs.list(), self.cloud_limit.clock(),
                                 self.cloud_limit.blocked, self.deferred_ids)
        errors = getattr(self.jobs, 'list_with_errors', None)
        if callable(errors):
            _, unreadable = errors()
            result['unreadable_jobs'] = list(unreadable)
            if unreadable:
                result['complete'] = False
        self.final_discovery_result = result
        return result

    def _scheduler_status(self, priority, task):
        from dataclasses import asdict
        now = self.cloud_limit.clock().astimezone(UTC)
        next_scan = None
        if self.wait_seconds:
            previous_scan = self._parse_utc(self.scheduler_view.get('next_scan_at'))
            next_scan = (previous_scan if previous_scan and previous_scan > now else now+timedelta(seconds=self.wait_seconds)).isoformat()
        view = dict(priority=priority, capabilities=asdict(self.capabilities),
            task=task, next_scan_at=next_scan,
            completed=sum(j.state is JobState.COMPLETED for j in self.jobs.list()),
            retry_attempts={j.id: j.attempt_by_stage.get('scheduler.recovery', 0) for j in self.jobs.list()
                            if j.attempt_by_stage.get('scheduler.recovery', 0)},
            terminal_failed=sum(terminal(j) and j.state is not JobState.COMPLETED and not j.duplicate_of_job_id for j in self.jobs.list()))
        if view == self.scheduler_view:
            return
        self.scheduler_view = view
        self.runtime_callback(dict(stage='scheduler.wait' if priority == 5 else 'scheduler.task',
            scheduler=dict(self.scheduler_view), phase=self.phase, cloud_limit=self.cloud_limit.status(),
            message=f'優先度{priority}: {task}'))

    def _discover_remaining_tasks(self):
        self.wait_seconds = 0
        now = self.cloud_limit.clock()
        idle = [j for j in self.jobs.list() if j.state is JobState.COMPLETED or
                j.state is JobState.WAITING_VIDEO and not due(j, now)]
        if idle:
            self._run_cycle_lane(selected_jobs=idle, allow_collection=True)
        # P2: finish existing local media before any remote collection.
        if any(j.state in self.MEDIA_STATES for j in self.jobs.list()):
            self._scheduler_status(2, 'ローカル処理')
        self._drain_limit_local(local_only=True)
        if self._generation_preempts():
            return
        # P3: Chat disabled does not disable artifact access. Check each due job
        # independently; the adapter remains responsible for current DOM readiness.
        now = self.cloud_limit.clock()
        remote = [j for j in self.jobs.list() if j.id not in self.deferred_ids and
            (due(j, now) or j.state in {JobState.DOWNLOAD_PENDING, JobState.DOWNLOADING}
             and j.runtime_reason != 'WAITING_REMOTE_ACCESS')
            and (j.state in {JobState.GENERATING, JobState.WAITING_VIDEO, JobState.DOWNLOAD_PENDING,
                JobState.DOWNLOADING, JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING}
                or remote_reconcile_candidate(j)
                or j.state in self.MEDIA_STATES and j.artifact_status == 'DELETE_PENDING')]
        if remote:
            self._scheduler_status(3, '期限到達動画の確認・Download')
            self._run_cycle_lane(selected_jobs=remote, allow_collection=True)
        # P4 is reached only after higher-priority work has had its turn.
        self._retry_deferred(local_only=self.cloud_limit.blocked)
        for job in self.jobs.list():
            if job.id not in self.deferred_ids and not terminal(job) and job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED} and due(job, now):
                self._scheduler_status(4, f'Error再診断: {job.script_name}')
                self._run_cycle_lane(selected_jobs=[job], allow_collection=True)
                # Re-evaluate generation priority before processing another error.
                if job.state is JobState.WAITING and not self.cloud_limit.blocked:
                    return
        runnable = any(j.id not in self.deferred_ids and not terminal(j) and
            (j.state in self.MEDIA_STATES or
             (self._needs_dispatch(j) and not self.cloud_limit.blocked) or
             (j.state in {JobState.DOWNLOAD_PENDING, JobState.DOWNLOADING} and due(j, now)))
            for j in self.jobs.list())
        if not self.all_tasks_completed() and not runnable:
            self.wait_seconds = RESCAN_SECONDS
            self._scheduler_status(5, '現在実行可能なtaskなし。10分後に再確認します')

    def _recover_error_task(self, job):
        checkpoint('error.recovery', job.id)
        key = 'scheduler.recovery'
        attempts = job.attempt_by_stage.get(key, 0)
        from djd_maker.core.job_migration import failure_class
        if attempts >= MAX_ERROR_ATTEMPTS or failure_class(job) == 'FATAL_FAILED':
            job.failure_class = 'TERMINAL_FAILED' if attempts >= MAX_ERROR_ATTEMPTS else 'FATAL_FAILED'
            with self._job_boundary(job): self._save(job)
            return
        with self._job_boundary(job):
            job.attempt_by_stage[key] = attempts+1
            self._save(job)  # Claim retry durably before any remote operation.
            if job.state is JobState.DOWNLOAD_VERIFY_FAILED:
                job.state = JobState.DOWNLOADING
                self._save(job)
                self._run_notebook_job(job)
            else:
                self.resume_failed_jobs({job.id})
                restored = self.jobs.get(job.id)
                for field in job.__dataclass_fields__:
                    setattr(job, field, getattr(restored, field))
                if job.state is JobState.FAILED and job.failure_class != 'FATAL_FAILED' and not self.cloud_limit.blocked:
                    # submit() checks the existing artifact and ensure_source()
                    # checks existing source identity before any upload/send.
                    job.state = JobState.WAITING
                    job.resume_checkpoint = 'SOURCE_CHECK'
                    self._save(job)
            if job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}:
                job.next_poll_at = (self.cloud_limit.clock()+timedelta(seconds=RESCAN_SECONDS)).isoformat()
                if job.attempt_by_stage.get(key, 0) >= MAX_ERROR_ATTEMPTS:
                    job.failure_class = 'TERMINAL_FAILED'
                self._save(job)

    def _drain_limit_local(self, *, local_only=False):
        self._reject_output_name_collisions()
        self._retry_deferred(local_only=True)
        # Downloaded files still pass the existing RAW safety gate. A known
        # ready artifact may be collected only by an adapter with live evidence.
        for job in self.jobs.list():
            if job.id in self.deferred_ids or job.state not in {JobState.DOWNLOADING, JobState.DOWNLOAD_PENDING}:
                continue
            download = self.paths.work_directory / job.id / 'download' / f'{job.script_name}.mp4'
            if not download.is_file() and (local_only or not getattr(self.notebook, 'download_during_limit_verified', False)):
                continue
            with self._job_boundary(job):
                checkpoint('limit.download.dequeue', job.id)
                def update(stage, fields):
                    self._local_checkpoint_fields(job, fields)
                    self._stage_event(job, stage)
                    self.runtime_callback(dict(fields, stage=stage, job=job.script_name, job_id=job.id,
                        phase='LIMIT_LOCAL_DRAIN', notebook=job.notebook_url or '－'))
                with operation_scope(update):
                    self._run_notebook_job(job)
        local = [j for j in self.jobs.list() if j.state in self.MEDIA_STATES and j.id not in self.deferred_ids]
        self.phase = ('LIMIT_LOCAL_DRAIN' if local else 'LIMIT_WAIT') if self.cloud_limit.blocked else 'COLLECT_LOCAL'
        if local:
            self.runtime_callback(dict(stage='limit.local', phase=self.phase, cloud_limit=self.cloud_limit.status(),
                message='ローカル処理を実行しています。'))
        if local:
            self._run_local_tasks(local)
        checkpoint('completed.txt.reconcile')
        if any(j.state is JobState.COMPLETED and j.zip_path for j in self.jobs.list()):
            self.reconcile_completed_txt()
        self._retry_deferred(local_only=True)

    def _run_local_tasks(self, jobs):
        """Submit only available slots; refresh priority before every next task.

        Already running tasks finish normally. No executor backlog can start
        another local job after generation becomes runnable.
        """
        self._media_completed = 0
        pending = list(jobs)
        with ThreadPoolExecutor(max_workers=self.ffmpeg_concurrency, thread_name_prefix='djd-ffmpeg') as pool:
            active = {}
            preempt = False
            while pending or active:
                preempt = preempt or self._generation_preempts()
                while pending and len(active) < self.ffmpeg_concurrency and not preempt:
                    job = pending.pop(0)
                    checkpoint('media.dequeue', job.id)
                    future = pool.submit(copy_context().run, self._local_media_with_progress,
                                         job, 1, len(jobs), True)
                    active[future] = job.id
                if not active:
                    break
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    future.result()
                    job_id = active.pop(future)
                    latest = self.jobs.get(job_id)
                    if latest and latest.state in self.MEDIA_STATES and job_id not in self.deferred_ids:
                        pending.insert(0, latest)
                preempt = preempt or self._generation_preempts()

    def _run_cycle_lane(self, *, selected_jobs=None, allow_collection=False) -> None:
        self._reject_output_name_collisions()
        # Preserve the existing bounded FFmpeg lane for RAW already on disk.
        # This is local-only: no Notebook is inspected/opened by these workers.
        local_media = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES and not self._dispatch_only and job.id not in self.deferred_ids]
        if local_media:
            self._run_local_tasks(local_media)
            if self._generation_preempts():
                return
        values = self.jobs.list() if selected_jobs is None else selected_jobs
        if self._dispatch_only:
            values = [job for job in values if self._needs_dispatch(job)]
        # Local ordering only. No remote pre-scan and no second execution queue.
        values.sort(key=lambda j: 0 if j.state in self.MEDIA_STATES or j.state is JobState.DOWNLOAD_PENDING else 1)
        for index, job in enumerate(values, 1):
            if not self._dispatch_only and self._generation_preempts():
                return
            if self.cloud_limit.blocked and not allow_collection:
                break
            if job.id in self.deferred_ids:
                continue
            with self._job_boundary(job):
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
                              phase='動画生成開始フェーズ' if self._dispatch_only else '動画回収・変換フェーズ', decision='－', next_action='状態確認', outcome='－',
                              attempt='－', processed=index-1, total=len(values), started=started)
                def update(stage, fields):
                    self._local_checkpoint_fields(job, fields)
                    self._stage_event(job, stage)
                    record.update(fields)
                    record.update(stage=stage, elapsed=time.monotonic()-started, notebook=job.notebook_url or '－')
                    record['phase_counts'] = self._counts()
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

    def _local_media_with_progress(self, job: Job, index: int, total: int, one_task=False) -> None:
        with self._job_boundary(job):
            started = time.monotonic()
            record = dict(job=job.script_name, job_id=job.id, notebook=job.notebook_url or '－',
                          phase='保存済みRAWの変換', processed=self._media_completed, total=total, started=started)
            def update(stage, fields):
                self._local_checkpoint_fields(job, fields)
                self._stage_event(job, stage)
                record.update(fields)
                record.update(stage=stage, elapsed=time.monotonic()-started, notebook=job.notebook_url or '－')
                record['phase_counts'] = self._counts()
                self.runtime_callback(dict(record))
            with operation_scope(update):
                try:
                    self._run_media_job(job, one_task=one_task)
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
                    if job.state not in self.MEDIA_STATES:
                        self._media_completed += 1
                    report_operation('job.result', outcome=job.state.value, processed=self._media_completed)

    def _idle_reason(self, job: Job) -> str | None:
        if job.duplicate_of_job_id:
            return 'DUPLICATE_SOURCE_REFERENCE'
        if job.failure_class == 'OUTPUT_BLOCKED' or job.error_code == 'OUTPUT_NAME_COLLISION':
            return 'OUTPUT_BLOCKED'
        if job.state is JobState.COMPLETED:
            return 'COMPLETED_SKIP'
        if job.state is JobState.DOWNLOAD_VERIFY_FAILED:
            return None if due(job, self.cloud_limit.clock()) else 'WAITING_FOR_NEXT_CHECK'
        if job.state is JobState.FAILED:
            if remote_reconcile_candidate(job):
                return None if due(job, self.cloud_limit.clock()) else 'WAITING_FOR_NEXT_CHECK'
            from djd_maker.core.job_migration import failure_class
            if job.failure_class == 'FATAL_FAILED' or failure_class(job) == 'FATAL_FAILED' or job.error_code == 'OUTPUT_NAME_COLLISION':
                return 'FATAL_FAILED'
            if not due(job, self.cloud_limit.clock()):
                return 'WAITING_FOR_NEXT_CHECK'
        if job.state is JobState.WAITING_VIDEO and not due(job, self.cloud_limit.clock()):
            return 'WAITING_FOR_NEXT_CHECK'
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
        previous_stage = job.presentation_stage
        job.presentation_stage = job.state.value
        if no_op:
            job.error_code = 'NO_OP_JOB_TRANSITION'
            job.error_message = reason
            if job.state is not JobState.FAILED:
                self._transition(job, JobState.FAILED)
        job.runtime_outcome = 'FAILED_WITH_REASON' if job.state is JobState.FAILED else job.state.value
        if reason == 'FATAL_FAILED':
            job.failure_class = 'FATAL_FAILED'
        job.runtime_reason = reason
        # Completed records must remain immutable (TXT reconciliation is local).
        if previous != (job.runtime_outcome, job.runtime_reason) or previous_stage != job.presentation_stage:
            self._save(job)
        report_operation('job.result', decision=reason, outcome=job.runtime_outcome,
                         notebook=job.notebook_url or '－',
                         **({'message':f'ファイル {job.script_name} はエラーになりました。他のjobを継続します。{job.error_message or reason}'}
                            if job.state is JobState.FAILED else {}))
        self._no_op_count = self._no_op_count + 1 if no_op else 0
        # Diagnosis failures belong to the individual persisted retry budget.

    def _check_act_job(self, job: Job) -> None:
        if remote_reconcile_candidate(job):
            if due(job, self.cloud_limit.clock()):
                self._reconcile_failed_remote(job)
            if job.state in {JobState.FAILED, JobState.WAITING_VIDEO} or (
                    job.state is JobState.WAITING and not self._dispatch_only):
                self._finish_job(job, job.remote_checkpoint or 'NOTEBOOK_STATE_UNKNOWN')
                return
        if job.state is JobState.COMPLETED:
            self._finish_job(job, 'COMPLETED_SKIP')
            return
        interrupted = job.state is JobState.RECOVERY_PENDING and (job.resume_checkpoint or '').startswith('STOPPED:')
        if job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED} or interrupted:
            from djd_maker.core.job_migration import failure_class
            if failure_class(job) == 'FATAL_FAILED':
                job.failure_class = 'FATAL_FAILED'
                self._save(job)
                self._finish_job(job, 'FATAL_FAILED')
                return
            if terminal(job) or not due(job, self.cloud_limit.clock()):
                return
            self._resume_attempted.add(job.id)
            report_operation('resume.check')
            if interrupted:
                self.resume_failed_jobs({job.id})
            else:
                self._recover_error_task(job)
            if job.id in self.deferred_ids:
                raise SaveDeferred()
            resumed = self.jobs.get(job.id)
            assert resumed is not None
            # Keep the current object so cancellation persists this same job.
            for field in job.__dataclass_fields__:
                setattr(job, field, getattr(resumed, field))
            report_operation('resume.decision', decision=job.state.value, next_action='必要工程を即実行')
            if job.state is JobState.FAILED:
                self._finish_job(job, 'REMOTE_STATE_UNKNOWN')
                return
            if job.state is JobState.WAITING_VIDEO:
                if self.scheduler is not None:
                    self.scheduler.schedule_generation(job, force=True)
                self._finish_job(job, 'GENERATION_ALREADY_STARTED')
                return

        if self._dispatch_only and job.state not in {JobState.WAITING, JobState.UPLOADING}:
            if job.state is JobState.DOWNLOAD_PENDING:
                job.artifact_status = 'READY'
                self._save(job)
            self._finish_job(job, 'REMOTE_ARTIFACT_READY' if job.state is JobState.DOWNLOAD_PENDING else job.state.value)
            return

        if job.state is JobState.GENERATING and not self._dispatch_only:
            self._transition(job, JobState.WAITING_VIDEO)
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
            if all(job.to_dict()[key] == value for key,value in before.items() if not key.startswith('presentation_')) and job.state is not JobState.WAITING_VIDEO:
                self._finish_job(job, 'NO_OP_JOB_TRANSITION', no_op=True)
                return
        if job.state in self.MEDIA_STATES and not self._dispatch_only:
            while job.state in self.MEDIA_STATES:
                if self._generation_preempts():
                    break
                self._run_media_job(job, one_task=True)
        self._finish_job(job, 'WAITING_REMOTE_ACCESS' if job.runtime_reason == 'WAITING_REMOTE_ACCESS' else
                         job.error_code or ('GENERATION_ALREADY_STARTED' if job.state is JobState.WAITING_VIDEO else job.state.value))
        if job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}:
            attempts = job.attempt_by_stage.get('scheduler.recovery', 0)
            if attempts:
                job.next_poll_at = (self.cloud_limit.clock()+timedelta(seconds=RESCAN_SECONDS)).isoformat()
                if attempts >= MAX_ERROR_ATTEMPTS:
                    job.failure_class = 'TERMINAL_FAILED'
                self._save(job)

    def run_recovery_cycle(self, *, now: datetime | None = None) -> list[str]:
        """Advance only persisted remote/recovery jobs; never submit new work."""
        self._reject_output_name_collisions()
        if self.cloud_limit.blocked:
            self._drain_limit_local(local_only=True)
        self.phase = 'COLLECT_LOCAL'
        current = (now or datetime.now(UTC)).astimezone(UTC)
        processed: list[str] = []
        values = self.jobs.list()
        for index, job in enumerate(values, 1):
            if job.id in self.deferred_ids:
                continue
            with self._job_boundary(job):
                checkpoint('recovery.dequeue', job.id)
                if job.state not in self.RECOVERY_STATES and not remote_reconcile_candidate(job):
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
                    self._local_checkpoint_fields(job, fields)
                    self._stage_event(job, stage)
                    record.update(fields)
                    record.update(stage=stage, elapsed=time.monotonic()-started, notebook=job.notebook_url or '－')
                    self.runtime_callback(dict(record))
                with operation_scope(update):
                    report_operation('job.start')
                    if remote_reconcile_candidate(job):
                        self._reconcile_failed_remote(job)
                        if job.state is JobState.DOWNLOAD_PENDING:
                            self._run_notebook_job(job)
                    else:
                        self._recover_remote_job(job, checked_at=current)
                    if job.state in self.MEDIA_STATES:
                        self._run_media_job(job)
                    self._finish_job(job, job.error_code or job.state.value)
                    report_operation('job.next', processed=index)

        media_jobs = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES and job.id not in self.deferred_ids]
        # Browser reconciliation stays on its owning thread, never a FFmpeg
        # executor thread (Playwright's sync session is thread-affine).
        for index, job in enumerate(media_jobs, 1):
            if job.artifact_status == 'DELETE_PENDING':
                checkpoint('media.dequeue', job.id)
                self._local_media_with_progress(job, index, len(media_jobs))
        media_jobs = [job for job in self.jobs.list() if job.state in self.MEDIA_STATES
                      and job.id not in self.deferred_ids and job.artifact_status != 'DELETE_PENDING']
        with ThreadPoolExecutor(
            max_workers=self.ffmpeg_concurrency, thread_name_prefix="djd-ffmpeg"
        ) as pool:
            futures = []
            for index, job in enumerate(media_jobs, 1):
                checkpoint('media.dequeue')
                futures.append(pool.submit(copy_context().run, self._local_media_with_progress, job, index, len(media_jobs)))
            for future in futures:
                future.result()
        self._retry_deferred()
        return processed

    @staticmethod
    def _parse_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("credit_reset_at must be timezone-aware")
        return parsed.astimezone(UTC)

    def _reconcile_failed_remote(self, job: Job) -> None:
        """Rebuild a failed checkpoint from evidence, before any retry/send."""
        checkpoint('resume.remote.check', job.id)
        now = self.cloud_limit.clock()
        try:
            if not Path(job.source_path).is_file():
                job.local_error_code = 'LOCAL_SOURCE_FILE_MISSING'
            if job.raw_path and Path(job.raw_path).is_file():
                from djd_maker.core.output_ownership import path_identity
                if any(other.id != job.id and other.raw_path
                       and path_identity(other.raw_path) == path_identity(job.raw_path)
                       for other in self.jobs.list()):
                    job.failure_class = 'OUTPUT_BLOCKED'
                    job.error_code = 'OUTPUT_EXISTING_UNVERIFIED'
                    job.error_message = 'RAWが他jobにも紐付いています。無断採用しません'
                    self._save(job)
                    return
                validated = self.validator.validate(Path(job.raw_path))
                if getattr(validated, 'valid', True):
                    stored = self.raw_store.verify_existing(Path(job.raw_path), Path(job.raw_path))
                    if stored.safety_gate.failed_checks:
                        raise ValueError('RAW safety gate failed')
                    job.safety_gate = stored.safety_gate
                    job.state = JobState.RAW_READY
                    job.remote_checkpoint = 'LOCAL_RAW_VALIDATED'
                    job.error_code = job.error_message = job.failure_class = None
                    self._save(job)
                    return
            if not job.notebook_id and not job.notebook_url:
                from djd_maker.core.notebook_identity import recover_unique_identity
                candidates = self.jobs.list()
                history = getattr(self.jobs, 'completed_checkpoint_history', None)
                if callable(history):
                    candidates.extend(history())
                if not recover_unique_identity(job, candidates):
                    raise ValueError('NOTEBOOK_IDENTITY_UNRESOLVED: 対応を一意に確認できません。Notebookは新規作成しません')
                self._save(job)
            diagnose = getattr(self.notebook, 'diagnose_resume', None)
            result = diagnose(job) if callable(diagnose) else {'artifact': self.notebook.inspect_status(job)}
            status = result['artifact']
            job.artifact_status = status
            job.source_status = result.get('source', job.source_status)
            job.last_checked_at = now.isoformat()
            job.remote_checkpoint = {
                'READY': 'ARTIFACT_READY', 'GENERATING': 'GENERATION_STARTED',
                'FAILED': 'ARTIFACT_FAILED', 'NOT_STARTED': 'ARTIFACT_ABSENT',
            }.get(status, 'ARTIFACT_UNKNOWN')
            target = None
            if status == 'READY':
                target = JobState.DOWNLOAD_PENDING
                report_operation('artifact.ready', decision='ARTIFACT_READY', next_action='Download開始')
            elif status == 'GENERATING' or (status == 'NOT_STARTED' and result.get('reply') == 'GENERATION_ACCEPTED'):
                target = JobState.WAITING_VIDEO
                job.remote_checkpoint = 'GENERATION_STARTED'
            elif status == 'FAILED':
                # The subsequent submit path checks current quota and retries
                # using the persisted snapshot, never the Studio Retry button.
                target = JobState.WAITING
                job.resume_checkpoint = 'FAILED_ARTIFACT_RETRY'
            elif status == 'WAITING':
                target = JobState.RESERVED_WAITING_CREDIT_RESET
            elif status == 'NOT_STARTED':
                target = JobState.WAITING
                job.remote_checkpoint = 'SOURCE_READY' if result.get('source') == 'READY' else 'ARTIFACT_ABSENT'
            if target is None:
                raise ValueError('NOTEBOOK_STATE_UNKNOWN: remote checkpointを確定できません')
            job.state = target
            job.error_code = job.error_message = job.failure_class = None
            if status == 'NOT_STARTED' and target is JobState.WAITING:
                # Historical diagnosis describes the resume cause, not current
                # capability. Only the new send/recheck may block Cloud.
                if result.get('source') in {'ERROR', 'MISSING', 'FAILED', 'ABSENT'}:
                    job.failure_class = 'SOURCE_UPLOAD_FAILED'
                elif result.get('reply') == 'QUOTA_EXHAUSTED':
                    job.failure_class = 'QUOTA_RECOVERY_PENDING'
                elif result.get('preset') == 'NOT_SENT':
                    job.failure_class = 'PRESET_SEND_FAILED'
                elif result.get('reply') == 'NO_RESPONSE':
                    job.failure_class = 'PRESET_RESPONSE_TIMEOUT'
            job.next_poll_at = ((now + timedelta(seconds=RESCAN_SECONDS)).isoformat()
                                if target is JobState.WAITING_VIDEO else None)
            self._save(job)
        except (RunCancelled, BlockingModalError, JobStateSaveError):
            raise
        except Exception as exc:
            key = 'scheduler.recovery'
            job.attempt_by_stage[key] = job.attempt_by_stage.get(key, 0) + 1
            job.error_code = 'NOTEBOOK_STATE_UNKNOWN'
            job.error_message = str(exc)
            job.remote_checkpoint = 'ARTIFACT_UNKNOWN'
            job.next_poll_at = (now + timedelta(seconds=RESCAN_SECONDS)).isoformat()
            if job.attempt_by_stage[key] >= MAX_ERROR_ATTEMPTS:
                job.failure_class = 'TERMINAL_FAILED'
            self._save(job)

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
            # Preserve artifacts; the outer job boundary isolates this save
            # failure without rewriting the job as FAILED or stopping others.
            raise
        except Exception as exc:
            job.recovery_retry_count += 1
            job.attempt_by_stage['scheduler.recovery'] = job.attempt_by_stage.get('scheduler.recovery', 0)+1
            job.error_code = "RECOVERY_CHECK_FAILED"
            job.error_message = str(exc)
            job.next_poll_at = (checked_at+timedelta(seconds=RESCAN_SECONDS)).isoformat()
            if job.attempt_by_stage['scheduler.recovery'] >= MAX_ERROR_ATTEMPTS:
                job.failure_class = 'TERMINAL_FAILED'
                job.state = JobState.FAILED
                self._save(job)
                return
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
        if self.cloud_limit.blocked and job.state in {JobState.WAITING, JobState.UPLOADING}:
            return
        status = None
        raw_validation_started = False
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
                    # Newly reserved jobs must not be re-polled immediately
                    # when Phase B begins, even if Google omits reset time.
                    now = datetime.now(UTC)
                    reset = self._parse_utc(job.credit_reset_at)
                    job.next_poll_at = max(now + timedelta(seconds=getattr(self.scheduler, 'subsequent_poll_seconds', 120)), reset or now).isoformat()
                    self._transition(job, JobState.CREDIT_EXHAUSTED)
                    self._transition(job, JobState.RESERVED_WAITING_CREDIT_RESET)
                    return
                self._transition(job, JobState.GENERATING)
                if getattr(submission, 'remote_status', None) == 'READY':
                    job.artifact_status = 'READY'
                    self._transition(job, JobState.DOWNLOAD_PENDING)
                    report_operation('artifact.ready', decision='READY', next_action='Phase BでDownload開始')
                if self.scheduler is not None:
                    if job.state is JobState.GENERATING:
                        self.scheduler.schedule_generation(job)
                        self._save(job)

            if self._dispatch_only and job.state not in {JobState.UPLOADING, JobState.GENERATING}:
                return

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
                if self._dispatch_only:
                    return

            if job.state is JobState.WAITING_VIDEO:
                checkpoint('artifact.poll')
                status = self.notebook.inspect_status(job)
                if job.error_code == 'REMOTE_ACCESS_UNAVAILABLE':
                    job.error_code = None
                    job.error_message = None
                    job.runtime_reason = None
                job.artifact_status = status
                job.last_checked_at = datetime.now(UTC).isoformat()
                self._save(job)
                if status != "READY":
                    if status == "FAILED":
                        # Keep identity and snapshot. Dispatch will inspect the
                        # present state again before a bounded central-chat retry.
                        job.state = JobState.WAITING
                        job.resume_checkpoint = 'FAILED_ARTIFACT_RETRY'
                        job.runtime_reason = 'GENERATION_RETRY_PENDING'
                        job.next_poll_at = None
                        self._save(job)
                        report_operation('generation.retry.pending', decision='FAILED_ARTIFACT',
                                         next_action='上限解除後Preset再送' if self.cloud_limit.blocked else 'Priority 1でPreset再送')
                        return
                    if self.scheduler is None:
                        job.next_poll_at = (self.cloud_limit.clock()+timedelta(seconds=RESCAN_SECONDS)).isoformat()
                        self._save(job)
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
                raw_validation_started = True
                report_operation('download.complete')
                report_operation('raw.validate')
                raw_path = self.paths.raw_directory / f"{job.script_name}.mp4"
                if raw_path.exists():
                    stored = self.raw_store.verify_existing(download, raw_path)
                else:
                    stored = self.raw_store.save(download, raw_path)
                media = getattr(stored, "media", stored)
                gate = getattr(stored, "safety_gate", None)
                if gate is None:
                    raise RuntimeError("RAW store did not return a deletion safety gate")
                if gate.failed_checks:
                    raise RuntimeError('RAW safety gate failed: ' + ', '.join(gate.failed_checks))
                job.raw_path = str(getattr(media, "path", raw_path))
                job.raw_size_bytes = getattr(media, "size_bytes", None)
                job.duration_seconds = getattr(media, "duration_seconds", None)
                raw_validation = getattr(stored, "raw_validation", None)
                metadata = getattr(raw_validation, "metadata", None)
                job.video_codec = getattr(metadata, "video_codec", None)
                job.audio_codec = getattr(metadata, "audio_codec", None)
                job.safety_gate = gate
                job.raw_status = "READY"
                job.artifact_status = 'RETAINED'
                self._transition(job, JobState.RAW_READY)
                report_operation('raw.saved', decision='RAW_READY', next_action='動画をNotebookに保持してローカル処理')
        except (SourceRetryExhausted, GenerationRetryExhausted) as exc:
            job.error_code = str(exc).split(':', 1)[0]
            job.error_message = str(exc)
            # The adapter explicitly certifies exhaustion. Persist that fact
            # before discovery so migration cannot demote it to generic fatal.
            stage = 'source.reupload' if isinstance(exc, SourceRetryExhausted) else 'generation.failed_retry'
            job.attempt_by_stage[stage] = max(MAX_ERROR_ATTEMPTS, job.attempt_by_stage.get(stage, 0))
            job.failure_class = 'TERMINAL_FAILED'
            job.state = JobState.FAILED
            self._save(job)
        except CloudLimitReached as exc:
            self.cloud_limit.block(exc.observation, notebook_url=job.notebook_url)
            # A Chat limit must never erase an existing generation/download
            # checkpoint or turn collection into another submission.
            if job.state in {JobState.WAITING, JobState.UPLOADING}:
                job.state = JobState.WAITING
            else:
                job.next_poll_at = (self.cloud_limit.clock()+timedelta(seconds=RESCAN_SECONDS)).isoformat()
            job.credit_state = 'CLOUD_BLOCKED_UNTIL'
            job.error_code = None
            job.runtime_reason = 'CLOUD_BLOCKED_UNTIL' if job.state is JobState.WAITING else 'WAITING_REMOTE_ACCESS'
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
            if ((job.state is JobState.WAITING_VIDEO and status != 'FAILED') or
                (job.state is JobState.DOWNLOADING and not raw_validation_started)):
                # Current artifact access can fail independently of Chat.
                # Preserve identity, back off only this job, and continue peers.
                key = 'scheduler.recovery'
                job.attempt_by_stage[key] = job.attempt_by_stage.get(key, 0)+1
                job.next_poll_at = (self.cloud_limit.clock()+timedelta(seconds=RESCAN_SECONDS)).isoformat()
                job.error_code = 'REMOTE_ACCESS_UNAVAILABLE'
                job.error_message = str(exc)
                job.runtime_reason = 'WAITING_REMOTE_ACCESS'
                if job.attempt_by_stage[key] >= MAX_ERROR_ATTEMPTS:
                    job.state = JobState.FAILED
                    job.failure_class = 'TERMINAL_FAILED'
                self._save(job)
                report_operation('remote.wait', decision='WAITING_REMOTE_ACCESS',
                    next_action='他jobへ。次回期限後に再確認', attempt=job.attempt_by_stage[key],
                    can_check_artifact=False, can_download=False)
                return
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
                    "LOCAL_SOURCE_FILE_MISSING",
                    "PRESET_NOT_SELECTED",
                }:
                    job.error_code = failure_code
                    if failure_code == 'LOCAL_SOURCE_FILE_MISSING':
                        job.local_error_code = failure_code
                else:
                    job.error_code = job.error_code or "NOTEBOOK_STAGE_FAILED"
                self._transition(job, JobState.FAILED)

    def _run_media_job(self, job: Job, *, one_task=False) -> None:
        try:
            checkpoint('media.start', job.id)
            if job.artifact_status == 'DELETE_PENDING':
                # Migrate the old pending-cleanup checkpoint without touching
                # the remote Notebook, including during quota/local-only work.
                job.artifact_status = 'RETAINED'
                if job.error_code == 'REMOTE_ARTIFACT_DELETE_FAILED':
                    job.error_code = None
                    job.error_message = None
                self._save(job)
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
                    if one_task:
                        return

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
                report_operation('ending.complete')
                self._transition(job, JobState.HLS_ENCODING)
                if one_task:
                    return

            if job.state in {JobState.HLS_ENCODING, JobState.ZIPPING}:
                resuming_zip_publish = job.state is JobState.ZIPPING
                if output_zip.exists():
                    if not resuming_zip_publish:
                        raise FileExistsError(
                            f"既存ZIPを別工程の成果物として採用しません: {output_zip}"
                        )
                    from dataclasses import replace
                    from djd_maker.core.artifact_ownership import owned_zip
                    if not owned_zip(replace(job, zip_path=str(output_zip)), output_zip, self.jobs):
                        raise FileExistsError(f"不正な既存ZIPを上書きしません: {output_zip}")
                    job.zip_path = str(output_zip)
                else:
                    with local_task_scope(one_task):
                        if job.hls_checkpoint_directory:
                            result = self.hls.resume_validated(Path(job.edited_path or edited), output_zip,
                                Path(job.hls_checkpoint_directory), job.hls_source_sha256,
                                Path(job.zip_checkpoint_path) if job.zip_checkpoint_path else None)
                        else:
                            result = self.hls.convert_validate_and_zip(
                                Path(job.edited_path or edited), output_zip
                            )
                    job.zip_path = str(result.zip_path)
                from djd_maker.core.artifact_ownership import _digest
                job.output_zip_sha256 = _digest(output_zip)
                job.hls_result = "PASS"
                if job.state is JobState.HLS_ENCODING:
                    self._transition(job, JobState.ZIPPING)
                self._transition(job, JobState.COMPLETED)
        except LocalTaskComplete:
            # hls.complete persisted the validated directory and input digest.
            # The owner thread refreshes capability before scheduling ZIP.
            return
        except RunCancelled:
            job.resume_checkpoint = 'STOPPED:' + job.state.value
            self._save(job)
            raise
        except JobStateSaveError:
            raise
        except BlockingModalError:
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
        from djd_maker.core.output_ownership import reconcile_output_ownership
        def persist(job):
            if job.id not in self.deferred_ids:
                with self._job_boundary(job):
                    self._save(job)
        self.ownership_report = reconcile_output_ownership(self.jobs, self.paths.output_directory, persist)

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
        if retried.id in self.deferred_ids:
            return retried
        if retried.state is JobState.FAILED:
            raise ValueError("再開checkpointを確認できません。既存jobと成果物は保持しました")
        retried.attempt_by_stage[retried.state.value] = retried.attempt_by_stage.get(retried.state.value, 0) + 1
        self._save(retried)
        return retried
