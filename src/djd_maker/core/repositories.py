from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .models import Job, JobState, utc_now
from .settings import AppSettings
from .storage import JsonStore


SCHEMA_VERSION = 1


class RepositoryError(RuntimeError):
    """Base class for persistence failures visible to the application."""


class MalformedJsonError(RepositoryError):
    """Neither the primary JSON document nor a recovery copy was usable."""


class UnsupportedSchemaError(RepositoryError):
    """A document belongs to a newer or otherwise unsupported schema."""


class RepositoryLockTimeout(RepositoryError):
    """Another process kept a repository document locked for too long."""


_thread_locks_guard = threading.Lock()
_thread_locks: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.RLock())


@contextmanager
def _document_lock(
    path: Path, *, timeout: float = 5.0, stale_after: float = 60.0
) -> Iterator[None]:
    """Serialize threads and cooperating processes without third-party packages."""

    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    local_lock = _thread_lock(path)
    deadline = time.monotonic() + timeout
    with local_lock:
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(
                        descriptor,
                        f"pid={os.getpid()} time={time.time()}\n".encode("ascii"),
                    )
                finally:
                    os.close(descriptor)
                break
            except FileExistsError:
                try:
                    is_stale = time.time() - lock_path.stat().st_mtime > stale_after
                except FileNotFoundError:
                    continue
                if is_stale:
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise RepositoryLockTimeout(f"timed out locking {path}")
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


class _VersionedDocument:
    """Atomic, recoverable storage for one versioned JSON document."""

    def __init__(self, path: str | Path, kind: str) -> None:
        self.path = Path(path)
        self.kind = kind
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")

    def _validate(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise MalformedJsonError(f"{self.path} must contain a JSON object")
        version = value.get("schema_version")
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"{self.path} schema {version!r} is not supported; expected {SCHEMA_VERSION}"
            )
        if value.get("kind") != self.kind:
            raise MalformedJsonError(
                f"{self.path} contains {value.get('kind')!r}, expected {self.kind!r}"
            )
        return value

    @staticmethod
    def _read(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def _quarantine(self, path: Path) -> None:
        if path.exists():
            quarantine = path.with_name(
                f"{path.name}.corrupt-{time.time_ns()}-{uuid4().hex[:8]}"
            )
            os.replace(path, quarantine)

    def _temporary_candidates(self) -> list[Path]:
        return sorted(
            self.path.parent.glob(f".{self.path.name}.*.tmp"),
            key=lambda candidate: candidate.stat().st_mtime_ns,
            reverse=True,
        )

    def _load_locked(self, default: dict[str, Any] | None) -> dict[str, Any]:
        primary_error: Exception | None = None
        if self.path.exists():
            try:
                return self._validate(self._read(self.path))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError, RepositoryError) as error:
                primary_error = error
                # A newer schema is intentional data, not crash corruption. Never
                # replace it with an older backup behind the caller's back.
                if isinstance(error, UnsupportedSchemaError):
                    raise

        # A valid replace-temporary is newer than the backup and can be left by a
        # process killed between fsync and os.replace.
        candidates = self._temporary_candidates()
        if self.backup_path.exists():
            candidates.append(self.backup_path)
        for candidate in candidates:
            try:
                recovered = self._validate(self._read(candidate))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError, RepositoryError):
                continue
            if self.path.exists():
                self._quarantine(self.path)
            JsonStore(self.path).save(recovered)
            for temporary in self._temporary_candidates():
                temporary.unlink(missing_ok=True)
            return recovered

        if primary_error is not None:
            if isinstance(primary_error, UnsupportedSchemaError):
                raise primary_error
            raise MalformedJsonError(f"cannot recover {self.path}: {primary_error}") from primary_error
        if default is None:
            raise FileNotFoundError(self.path)
        return default

    def load(self, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with _document_lock(self.path):
            return self._load_locked(default)

    def save(self, value: Mapping[str, Any]) -> None:
        document = self._validate(dict(value))
        with _document_lock(self.path):
            if self.path.exists():
                try:
                    previous = self._validate(self._read(self.path))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError, RepositoryError):
                    previous = None
                if previous is not None:
                    JsonStore(self.backup_path).save(previous)
            JsonStore(self.path).save(document)

    def update(self, default: dict[str, Any], change) -> dict[str, Any]:
        with _document_lock(self.path):
            current = self._load_locked(default)
            updated = change(current)
            document = self._validate(updated)
            if self.path.exists():
                try:
                    previous = self._validate(self._read(self.path))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError, RepositoryError):
                    previous = None
                if previous is not None:
                    JsonStore(self.backup_path).save(previous)
            JsonStore(self.path).save(document)
            return document


class SettingsRepository:
    def __init__(self, path: str | Path) -> None:
        self._document = _VersionedDocument(path, "settings")

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "settings",
            "settings": AppSettings().to_dict(),
        }

    def load(self) -> AppSettings:
        document = self._document.load(self._default())
        try:
            settings = AppSettings(**document["settings"])
            settings.validate()
            return settings
        except (KeyError, TypeError, ValueError) as error:
            raise MalformedJsonError(f"invalid settings in {self._document.path}: {error}") from error

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self._document.save(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "settings",
                "settings": settings.to_dict(),
            }
        )


class JobRepository:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _document(self, job_id: str) -> _VersionedDocument:
        if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
            raise ValueError("job_id must be a plain file name")
        return _VersionedDocument(self.directory / f"{job_id}.json", "job")

    @staticmethod
    def _envelope(job: Job) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": "job", "job": job.to_dict()}

    @staticmethod
    def _decode(document: Mapping[str, Any], path: Path) -> Job:
        try:
            return Job.from_dict(document["job"])
        except (KeyError, TypeError, ValueError) as error:
            raise MalformedJsonError(f"invalid job in {path}: {error}") from error

    def save(self, job: Job) -> None:
        self._document(job.id).save(self._envelope(job))

    def get(self, job_id: str) -> Job | None:
        document = self._document(job_id)
        try:
            value = document.load()
        except FileNotFoundError:
            return None
        return self._decode(value, document.path)

    load = get

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list(self) -> list[Job]:
        jobs, _errors = self.list_with_errors()
        return jobs

    def list_with_errors(self) -> tuple[list[Job], dict[str, str]]:
        """Load healthy jobs while isolating unrecoverable per-job documents."""

        if not self.directory.exists():
            return [], {}
        jobs: list[Job] = []
        errors: dict[str, str] = {}
        for path in sorted(self.directory.glob("*.json")):
            document = _VersionedDocument(path, "job")
            try:
                jobs.append(self._decode(document.load(), path))
            except (RepositoryError, OSError, UnicodeError, json.JSONDecodeError) as error:
                errors[path.stem] = str(error)
        return jobs, errors

    list_all = list

    def transition(self, job_id: str, target: JobState) -> Job:
        document = self._document(job_id)

        def apply(value: dict[str, Any]) -> dict[str, Any]:
            job = self._decode(value, document.path)
            job.transition_to(target)
            return self._envelope(job)

        try:
            updated = document.update({}, apply)
        except (FileNotFoundError, KeyError):
            raise KeyError(job_id) from None
        return self._decode(updated, document.path)

    def recover_interrupted(self) -> list[Job]:
        """Move interrupted local stages to the last durable checkpoint.

        Remote waiting and verified checkpoints are already resumable and remain
        untouched. COMPLETED and FAILED jobs are never automatically rerun.
        """

        checkpoints = {
            # UPLOADING is intentionally handled below: the process may have
            # created a remote Notebook just before crashing, so resubmission
            # could duplicate it.
            JobState.UPLOADING: JobState.FAILED,
            # submit() persists the remote identity before entering GENERATING;
            # resume monitoring instead of submitting the TXT again.
            JobState.GENERATING: JobState.WAITING_VIDEO,
            JobState.DOWNLOADING: JobState.WAITING_VIDEO,
            JobState.ENDING: JobState.RAW_READY,
            JobState.HLS_ENCODING: JobState.ENDING,
            JobState.ZIPPING: JobState.HLS_ENCODING,
        }
        recovered: list[Job] = []
        for snapshot in self.list():
            target = checkpoints.get(snapshot.state)
            if target is None:
                continue
            document = self._document(snapshot.id)

            def apply(value: dict[str, Any], expected=snapshot.state, new=target):
                job = self._decode(value, document.path)
                if job.state == expected:
                    # Crash recovery is deliberately not a normal forward transition.
                    job.state = new
                    if expected is JobState.UPLOADING:
                        job.error_code = "SUBMISSION_STATE_UNCERTAIN"
                        job.error_message = (
                            "Crash occurred while submitting; verify the remote Notebook "
                            "before creating a retry"
                        )
                    elif expected is JobState.GENERATING and (
                        not job.notebook_id or not job.notebook_url
                    ):
                        job.state = JobState.FAILED
                        job.error_code = "NOTEBOOK_RESUME_METADATA_MISSING"
                        job.error_message = "Notebook identity is missing after submission"
                    job.updated_at = utc_now()
                    return self._envelope(job)
                return value

            recovered.append(self._decode(document.update({}, apply), document.path))
        return recovered

    def load_recoverable(self) -> list[Job]:
        """Recover crash-interrupted stages and return jobs safe to resume now."""

        self.recover_interrupted()
        return [
            job
            for job in self.list()
            if job.state not in {JobState.COMPLETED, JobState.FAILED}
        ]


class QueueRepository:
    def __init__(self, path: str | Path) -> None:
        self._document = _VersionedDocument(path, "queue")

    @staticmethod
    def _default() -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": "queue", "job_ids": []}

    def load(self) -> list[str]:
        document = self._document.load(self._default())
        values = document.get("job_ids")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise MalformedJsonError("queue.job_ids must be a list of strings")
        return list(values)

    def save(self, job_ids: list[str]) -> None:
        if not all(isinstance(item, str) for item in job_ids):
            raise TypeError("job_ids must contain only strings")
        self._document.save(
            {"schema_version": SCHEMA_VERSION, "kind": "queue", "job_ids": list(job_ids)}
        )

    def enqueue(self, job_id: str) -> bool:
        added = False

        def apply(value: dict[str, Any]) -> dict[str, Any]:
            nonlocal added
            items = value.get("job_ids")
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise MalformedJsonError("queue.job_ids must be a list of strings")
            if job_id not in items:
                items = [*items, job_id]
                added = True
            return {**value, "job_ids": items}

        self._document.update(self._default(), apply)
        return added

    def remove(self, job_id: str) -> bool:
        removed = False

        def apply(value: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            items = value.get("job_ids")
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise MalformedJsonError("queue.job_ids must be a list of strings")
            filtered = [item for item in items if item != job_id]
            removed = len(filtered) != len(items)
            return {**value, "job_ids": filtered}

        self._document.update(self._default(), apply)
        return removed

    dequeue = remove


@dataclass(slots=True)
class RuntimeState:
    clean_shutdown: bool = True
    active_notebook_job_id: str | None = None
    active_ffmpeg_job_ids: tuple[str, ...] = ()


class StateRepository:
    def __init__(self, path: str | Path) -> None:
        self._document = _VersionedDocument(path, "state")

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "state",
            "clean_shutdown": True,
            "active_notebook_job_id": None,
            "active_ffmpeg_job_ids": [],
        }

    def load(self) -> RuntimeState:
        value = self._document.load(self._default())
        try:
            active = value["active_ffmpeg_job_ids"]
            if not isinstance(active, list) or not all(isinstance(item, str) for item in active):
                raise TypeError("active_ffmpeg_job_ids must be a list of strings")
            notebook = value["active_notebook_job_id"]
            if notebook is not None and not isinstance(notebook, str):
                raise TypeError("active_notebook_job_id must be a string or null")
            if not isinstance(value["clean_shutdown"], bool):
                raise TypeError("clean_shutdown must be boolean")
            return RuntimeState(value["clean_shutdown"], notebook, tuple(active))
        except (KeyError, TypeError) as error:
            raise MalformedJsonError(f"invalid runtime state: {error}") from error

    def save(self, state: RuntimeState) -> None:
        self._document.save(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "state",
                "clean_shutdown": state.clean_shutdown,
                "active_notebook_job_id": state.active_notebook_job_id,
                "active_ffmpeg_job_ids": list(state.active_ffmpeg_job_ids),
            }
        )

    def mark_started(self) -> bool:
        """Mark this run active and report whether the previous run was unclean."""

        previous_unclean = False

        def apply(value: dict[str, Any]) -> dict[str, Any]:
            nonlocal previous_unclean
            previous_unclean = value.get("clean_shutdown") is False
            return {**value, "clean_shutdown": False}

        self._document.update(self._default(), apply)
        return previous_unclean

    def mark_clean_shutdown(self) -> None:
        def apply(value: dict[str, Any]) -> dict[str, Any]:
            return {
                **value,
                "clean_shutdown": True,
                "active_notebook_job_id": None,
                "active_ffmpeg_job_ids": [],
            }

        self._document.update(self._default(), apply)


class QueueStateRepository:
    """Convenience facade matching the on-disk queue/state pair."""

    def __init__(self, queue_path: str | Path, state_path: str | Path) -> None:
        self.queue = QueueRepository(queue_path)
        self.state = StateRepository(state_path)
