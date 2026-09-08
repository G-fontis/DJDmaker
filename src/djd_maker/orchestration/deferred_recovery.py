"""Read-before-retry reconciliation for isolated persistence failures."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

from djd_maker.core.cancellation import checkpoint
from djd_maker.core.deferred_state import DeferredStateStore, SaveDeferred
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobStateSaveError
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.adapters.notebook_modal import BlockingModalError


class DeferredRecovery:
    MAX_DEFERRED_ATTEMPTS = 2

    @contextmanager
    def _job_boundary(self, job):
        try:
            yield
        except SaveDeferred:
            pass
        except JobStateSaveError as error:
            self._defer(job, error)

    def _init_deferred(self):
        shared = getattr(self.jobs, '_deferred_state', None)
        if shared is None:
            directory = getattr(self.jobs, 'directory', None)
            shared = DeferredStateStore(Path(directory).parent / 'recovery' / 'deferred' if directory else None)
            self.jobs._deferred_state = shared
        self.deferred = shared
        self._deferred_attempts = {}
        for job in self.jobs.list():
            if job.id not in self.deferred_ids and job.state is JobState.UPLOADING:
                # If both primary save and journal failed in a previous process,
                # the durable pre-action checkpoint still forbids blind resend.
                entry = self.deferred.record(job, 'RESTART_UNCERTAIN_SUBMISSION')
                entry.allow_create_retry = False
                self.deferred.persist(entry)

    @property
    def deferred_ids(self):
        return set(self.deferred.entries)

    def _deferred_notice(self, entry, stage, message):
        self.runtime_callback(dict(job_id=entry.job_id, job=entry.script_name,
            notebook=entry.last_known_notebook_url or '－',
            stage=stage, phase='状態保存の復旧', level='WARNING' if stage != 'save.recovered' else 'INFO',
            decision=message, message=message, outcome='SAVE_DEFERRED' if stage != 'save.recovered' else 'RECOVERED',
            next_action='他jobを続行・後ほど照合' if stage != 'save.recovered' else '安全checkpointから続行',
            attempt=entry.retry_count, deferred_count=len(self.deferred.entries),
            journal_saved=entry.journal_saved))

    def _defer(self, job, error):
        # Do not recursively call _save/report_operation here: its operation
        # observer is precisely where a persistence failure can originate.
        entry = self.deferred.record(job, type(error).__name__)
        self._deferred_notice(entry, 'save.deferred', '状態ファイルを保存できませんでした。このjobを保留し、他のjobを続行します。')

    def _retry_deferred(self, *, local_only=False):
        for job_id in list(self.deferred.entries):
            checkpoint('deferred.dequeue')
            attempts = self._deferred_attempts.get(job_id, 0)
            if attempts >= self.MAX_DEFERRED_ATTEMPTS:
                continue
            entry = self.deferred.entries[job_id]
            if local_only and entry.snapshot:
                snapshot = Job.from_dict(entry.snapshot)
                downloaded = self.paths.work_directory / snapshot.id / 'download' / f'{snapshot.script_name}.mp4'
                raw = Path(snapshot.raw_path) if snapshot.raw_path else self.paths.raw_directory / f'{snapshot.script_name}.mp4'
                if snapshot.notebook_url and not (raw.is_file() or downloaded.is_file() or snapshot.state is JobState.COMPLETED):
                    continue
            self._deferred_attempts[job_id] = attempts + 1
            entry.retry_count += 1
            self._deferred_notice(entry, 'save.retry', 'remote/local成果物を照合し、状態保存を再試行します。')
            try:
                # Reconciliation must not recursively save via an old job's
                # runtime observer, nor publish a success row before the save.
                with operation_scope(lambda *_args: None):
                    job = self._reconcile_deferred(entry, local_only=local_only)
                    if job is None:
                        raise ValueError('RECONCILIATION_UNCERTAIN')
                    checkpoint('deferred.publish')
                    job.presentation_revision += 1
                    self.jobs.save(job)
                self._progress_jobs[job.id] = Job.from_dict(job.to_dict())
                self.deferred.resolve(job.id)
                self.job_callback(Job.from_dict(job.to_dict()))
                self._deferred_notice(entry, 'save.recovered', '状態保存を復旧しました。')
            except BlockingModalError:
                raise
            except Exception as exc:
                entry.reason = type(exc).__name__ + ': reconciliation/publish pending'
                entry.unresolved = self._deferred_attempts[job_id] >= self.MAX_DEFERRED_ATTEMPTS
                self.deferred.persist(entry)
                self._deferred_notice(entry, 'save.unresolved', '状態保存を復旧できませんでした。他のjob処理を続行します。')

    def _reconcile_deferred(self, entry, *, local_only=False):
        if not entry.snapshot:
            return None
        job = Job.from_dict(entry.snapshot)
        durable = self.jobs.get(job.id)
        if durable is None:
            # A discovery save failed before any remote action. Keep its stable
            # identity, do not let every reload create another pending job.
            if entry.allow_create_retry and job.state is JobState.WAITING and not job.notebook_id and not job.notebook_url:
                return job
            return None
        if durable.source_path != job.source_path:
            return None
        if durable.state is JobState.COMPLETED:
            # A stale journal must not roll an already-completed job backwards.
            return durable
        if durable.presentation_revision > job.presentation_revision:
            job = durable
        if job.error_code == 'OUTPUT_NAME_COLLISION':
            job.state = JobState.FAILED
            return job
        output = self.paths.output_directory / f'{job.script_name}.zip'
        if job.presentation_stage in {'zip.complete', 'COMPLETED'} and output.is_file():
            if not self._valid_zip(output):
                return None
            job.zip_path = str(output)
            job.hls_result = 'PASS'
            job.state = JobState.COMPLETED
            job.progress_percent = 100
            return job
        download = self.paths.work_directory / job.id / 'download' / f'{job.script_name}.mp4'
        raw = Path(job.raw_path) if job.raw_path else self.paths.raw_directory / f'{job.script_name}.mp4'
        if raw.is_file() and (job.raw_path or job.download_status == 'DOWNLOADED'):
            checked = self.validator.validate(raw)
            if not getattr(checked, 'valid', True):
                return None
            # Reuse the original RAW safety validator; never infer the 12 gates
            # from existence alone. No new remote download is invoked here.
            stored = self.raw_store.verify_existing(download if download.is_file() else raw, raw)
            job.raw_path = str(raw)
            job.safety_gate = stored.safety_gate
            job.raw_status = 'READY'
            if job.artifact_status != 'DELETED':
                job.artifact_status = 'DELETE_PENDING'
            job.state = JobState.RAW_READY
            if job.edited_path and Path(job.edited_path).is_file():
                if getattr(self.validator.validate(Path(job.edited_path)), 'valid', True):
                    job.state = JobState.HLS_ENCODING
            if job.presentation_stage in {'zip.start', 'zip.complete', 'ZIPPING', 'COMPLETED'}:
                job.state = JobState.ZIPPING
            return job
        if download.is_file():
            if not getattr(self.validator.validate(download), 'valid', True):
                return None
            job.state = JobState.DOWNLOADING
            return job
        if job.notebook_id and job.notebook_url:
            if local_only:
                return None
            checkpoint('deferred.remote.inspect')
            status = self.notebook.inspect_status(job)
            target = {'READY': JobState.DOWNLOAD_PENDING, 'GENERATING': JobState.WAITING_VIDEO,
                      'WAITING': JobState.RESERVED_WAITING_CREDIT_RESET,
                      'SCHEDULED_REMOTE': JobState.RESERVED_WAITING_CREDIT_RESET}.get(status)
            if status == 'FAILED' and job.generation_retry_turn_count is not None:
                # Preserve the claimed chat boundary. The retry adapter must
                # reconcile it before sending; never erase it on save recovery.
                target = JobState.WAITING
            if target is None:
                # Only a pre-chat checkpoint can be safely retried in place.
                # NOT_STARTED after a possible send is NOT proof of no send.
                if status != 'NOT_STARTED' or not job.presentation_stage.startswith(('source.', 'notebook.')):
                    return None
                target = JobState.WAITING
            job.state = target
            job.artifact_status = status
            if target in {JobState.WAITING_VIDEO, JobState.RESERVED_WAITING_CREDIT_RESET}:
                job.next_poll_at = (datetime.now(UTC) + timedelta(seconds=getattr(self.scheduler, 'subsequent_poll_seconds', 120))).isoformat()
            return job
        if entry.allow_create_retry and (job.presentation_stage in {'notebook.submit', 'UPLOADING', 'notebook.create'} or job.state is JobState.WAITING):
            job.state = JobState.WAITING
            return job
        return None

    def deferred_summary(self):
        if self.deferred.entries:
            completed = sum(j.state is JobState.COMPLETED and j.id not in self.deferred_ids for j in self.jobs.list())
            memory_only = sum(not entry.journal_saved for entry in self.deferred.entries.values())
            self.runtime_callback(dict(stage='save.summary', level='WARNING',
                deferred_count=len(self.deferred.entries),
                phase='処理結果（保存保留あり）', outcome='UNRESOLVED_STATE_SAVE',
                decision=f'成功 {completed}件 / 状態保存保留 {len(self.deferred.entries)}件',
                next_action='次回Start時にremote/local照合から再開',
                processed=completed, total=len(self.jobs.list()),
                completed_count=completed, memory_only_count=memory_only,
                message=f'処理終了：成功 {completed}件 / 状態保存保留 {len(self.deferred.entries)}件 / journal未保存 {memory_only}件。完全成功ではありません。次回は成果物照合から再開します。'))
