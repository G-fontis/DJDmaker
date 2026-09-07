"""Per-job save isolation; journal failure never discards the in-process evidence."""
from dataclasses import dataclass, field
from pathlib import Path
import threading
from urllib.parse import quote, unquote
from contextlib import contextmanager

from .models import Job, utc_now


def store_for(jobs):
    store = getattr(jobs, '_deferred_state', None)
    if store is None:
        directory = getattr(jobs, 'directory', None)
        store = DeferredStateStore(Path(directory).parent / 'recovery' / 'deferred' if directory else None)
        jobs._deferred_state = store
    return store


@contextmanager
def isolate_auxiliary_save(jobs, job):
    from .repositories import JobStateSaveError
    try:
        yield
    except JobStateSaveError as error:
        store_for(jobs).record(job, type(error).__name__)


class SaveDeferred(BaseException):
    """Job-boundary control flow, not a retryable browser/media exception."""


@dataclass
class DeferredEntry:
    job_id: str
    script_name: str
    failed_save_at: str
    intended_state: str
    current_stage: str
    remote_operation_may_have_completed: bool
    retry_count: int
    reason: str
    last_known_notebook_id: str | None
    last_known_notebook_url: str | None
    next_safe_action: str
    snapshot: dict = field(default_factory=dict)
    journal_saved: bool = False
    unresolved: bool = False
    allow_create_retry: bool = False

    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)


class DeferredStateStore:
    """Shared across pipeline re-composition; bounded retry is owned by the run."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory
        self.entries: dict[str, DeferredEntry] = {}
        self._lock = threading.RLock()
        if directory and directory.exists():
            for path in directory.glob('*.json'):
                job_id = unquote(path.stem)
                try:
                    document = self._document(job_id).load()
                    entry = DeferredEntry(**document['entry'])
                    if entry.job_id != job_id:
                        raise ValueError('JOURNAL_IDENTITY_MISMATCH')
                    entry.journal_saved = True
                except Exception:
                    # Corruption is not evidence that an irreversible operation
                    # did not happen. Isolate that identity, never delete it.
                    entry = DeferredEntry(job_id, job_id, utc_now(), 'UNKNOWN',
                        'UNKNOWN', True, 0, 'RECOVERY_JOURNAL_UNREADABLE', None, None,
                        'manual identity reconciliation', unresolved=True)
                self.entries[job_id] = entry

    def _document(self, job_id):
        from .repositories import _VersionedDocument, JOB_REPLACE_RETRY_DELAYS
        assert self.directory is not None
        return _VersionedDocument(self.directory / (quote(job_id, safe='') + '.json'),
            'deferred-job-save', use_file_lock=False,
            replace_retry_delays=JOB_REPLACE_RETRY_DELAYS)

    def record(self, job: Job, reason: str) -> DeferredEntry:
        with self._lock:
            previous = self.entries.get(job.id)
            entry = DeferredEntry(job.id, job.script_name,
                previous.failed_save_at if previous else utc_now(), job.state.value,
                job.presentation_stage, bool(job.notebook_id or job.state.value != 'WAITING'),
                previous.retry_count if previous else 0, reason,
                job.notebook_id, job.notebook_url, 'reconcile remote/local before action',
                snapshot=job.to_dict(), allow_create_retry=(job.state.value == 'WAITING' or
                    job.presentation_stage in {'notebook.submit', 'UPLOADING', 'notebook.create'}))
            self.entries[job.id] = entry
            self.persist(entry)
            return entry

    def persist(self, entry: DeferredEntry) -> bool:
        with self._lock:
            if self.directory is None:
                return False
            try:
                self._document(entry.job_id).save({'schema_version': 1,
                    'kind': 'deferred-job-save', 'entry': entry.to_dict()})
                entry.journal_saved = True
            except Exception:
                entry.journal_saved = False
            return entry.journal_saved

    def resolve(self, job_id: str) -> None:
        with self._lock:
            # A stale journal may safely survive a failed delete: restart still
            # reconciles before acting and never blindly applies old snapshots.
            if self.directory is not None:
                document = self._document(job_id)
                try:
                    document.path.unlink(missing_ok=True)
                    document.backup_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.entries.pop(job_id, None)
