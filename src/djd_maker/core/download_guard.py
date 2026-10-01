from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4


class DeferredTaskType(StrEnum):
    USER_STOP = "USER_STOP"
    APP_CLOSE = "APP_CLOSE"
    DOWNLOAD_RETRY = "DOWNLOAD_RETRY"
    MANUAL_SAVE_RETRY = "MANUAL_SAVE_RETRY"
    RECOVERY_TASK = "RECOVERY_TASK"
    UNRECOVERED_SCAN = "UNRECOVERED_SCAN"
    NEXT_NOTEBOOK = "NEXT_NOTEBOOK"
    NEXT_JOB = "NEXT_JOB"
    NORMAL_SCHEDULER_TASK = "NORMAL_SCHEDULER_TASK"
    PAGE_CLOSE = "PAGE_CLOSE"
    POPUP_CLOSE = "POPUP_CLOSE"
    CONTEXT_CLOSE = "CONTEXT_CLOSE"
    BROWSER_CLOSE = "BROWSER_CLOSE"
    PIPELINE_CLEANUP = "PIPELINE_CLEANUP"
    AUTOMATION_CLEANUP = "AUTOMATION_CLEANUP"
    ERROR_CLEANUP = "ERROR_CLEANUP"
    PHASE_TRANSITION = "PHASE_TRANSITION"


_PRIORITY = {
    DeferredTaskType.USER_STOP: 0,
    DeferredTaskType.APP_CLOSE: 1,
    DeferredTaskType.DOWNLOAD_RETRY: 2,
    DeferredTaskType.MANUAL_SAVE_RETRY: 3,
    DeferredTaskType.RECOVERY_TASK: 4,
    DeferredTaskType.UNRECOVERED_SCAN: 4,
    DeferredTaskType.NEXT_NOTEBOOK: 5,
    DeferredTaskType.NEXT_JOB: 6,
    DeferredTaskType.NORMAL_SCHEDULER_TASK: 7,
    DeferredTaskType.PHASE_TRANSITION: 8,
    DeferredTaskType.PAGE_CLOSE: 9,
    DeferredTaskType.POPUP_CLOSE: 9,
    DeferredTaskType.CONTEXT_CLOSE: 9,
    DeferredTaskType.BROWSER_CLOSE: 9,
    DeferredTaskType.PIPELINE_CLEANUP: 9,
    DeferredTaskType.AUTOMATION_CLEANUP: 9,
    DeferredTaskType.ERROR_CLEANUP: 9,
}

_RUNTIME_ONLY = {
    DeferredTaskType.PAGE_CLOSE,
    DeferredTaskType.POPUP_CLOSE,
    DeferredTaskType.CONTEXT_CLOSE,
    DeferredTaskType.BROWSER_CLOSE,
    DeferredTaskType.PIPELINE_CLEANUP,
    DeferredTaskType.AUTOMATION_CLEANUP,
    DeferredTaskType.ERROR_CLEANUP,
    DeferredTaskType.PHASE_TRANSITION,
}


@dataclass(frozen=True)
class DeferredTask:
    task_type: str
    job_id: str | None = None
    target_notebook_id: str | None = None
    command_id: str | None = None
    created_at: str = ""
    task_id: str = ""

    @classmethod
    def make(
        cls,
        task_type: DeferredTaskType | str,
        *,
        job_id: str | None = None,
        target_notebook_id: str | None = None,
        command_id: str | None = None,
    ) -> "DeferredTask":
        return cls(
            str(task_type), job_id, target_notebook_id, command_id,
            datetime.now(UTC).isoformat(), uuid4().hex,
        )

    @property
    def dedup_key(self) -> tuple[str, str | None, str | None, str | None]:
        return self.task_type, self.job_id, self.target_notebook_id, self.command_id


class DownloadLifecycleGuard:
    """One process-wide owner for transfers and lifecycle deferral.

    The guard intentionally does not know Playwright, Qt, or the scheduler.  It
    serializes download ownership and releases queued commands only after the
    last transfer has conclusively completed or failed.
    """

    def __init__(
        self,
        persistence_path: Path | None = None,
        *,
        logger: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.persistence_path = persistence_path.resolve() if persistence_path else None
        self.logger = logger or (lambda _record: None)
        self._lock = threading.RLock()
        self._transfer_lock = threading.Lock()
        self._active: dict[str, str | None] = {}
        self._tasks: list[DeferredTask] = []
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._handlers: dict[str, Callable[[DeferredTask], None]] = {}
        self._load()

    @property
    def active_download_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def state(self) -> str:
        return "RUNNING_DOWNLOAD" if self.active_download_count else "IDLE"

    @property
    def pending(self) -> tuple[DeferredTask, ...]:
        with self._lock:
            return tuple(self._ordered(self._tasks))

    def register_handler(
        self, task_type: DeferredTaskType | str, handler: Callable[[DeferredTask], None]
    ) -> None:
        with self._lock:
            self._handlers[str(task_type)] = handler

    def record(self, event: str, **fields: object) -> None:
        self._log(event, **fields)

    @contextmanager
    def transfer(self, job_id: str | None = None) -> Iterator[None]:
        # Notebook downloads are deliberately serialized.  This also makes the
        # active count an exact lifecycle gate rather than a best-effort flag.
        with self._transfer_lock:
            transfer_id = uuid4().hex
            with self._lock:
                self._active[transfer_id] = job_id
                self._log("DOWNLOAD_STARTED", job_id=job_id)
            try:
                yield
            finally:
                with self._lock:
                    self._active.pop(transfer_id, None)
                    self._log("DOWNLOAD_ENDED", job_id=job_id)
                self.release()

    def defer(
        self,
        task_type: DeferredTaskType | str,
        *,
        job_id: str | None = None,
        target_notebook_id: str | None = None,
        command_id: str | None = None,
        callback: Callable[[], None] | None = None,
    ) -> DeferredTask:
        task = DeferredTask.make(
            task_type, job_id=job_id, target_notebook_id=target_notebook_id,
            command_id=command_id,
        )
        with self._lock:
            existing = next((item for item in self._tasks if item.dedup_key == task.dedup_key), None)
            if existing is not None:
                if callback is not None:
                    self._callbacks.setdefault(existing.task_id, callback)
                return existing
            self._tasks.append(task)
            if callback is not None:
                self._callbacks[task.task_id] = callback
            self._persist()
            self._log("CLOSE_DEFERRED" if "CLOSE" in task.task_type else "TASK_DEFERRED",
                      task_type=task.task_type, job_id=job_id)
        return task

    def run_or_defer(
        self,
        task_type: DeferredTaskType | str,
        callback: Callable[[], None],
        **identity: str | None,
    ) -> bool:
        if self.active_download_count:
            self.defer(task_type, callback=callback, **identity)
            return False
        callback()
        return True

    def release(self) -> list[DeferredTask]:
        """Execute a stable priority snapshot after all transfers finish."""
        completed: list[DeferredTask] = []
        while True:
            with self._lock:
                if self._active or not self._tasks:
                    return completed
                task = self._ordered(self._tasks)[0]
                self._tasks.remove(task)
                callback = self._callbacks.pop(task.task_id, None)
                handler = self._handlers.get(task.task_type)
                self._persist()
            try:
                if callback is not None:
                    callback()
                elif handler is not None:
                    handler(task)
                else:
                    # Durable work without a currently registered consumer must
                    # survive until the corresponding pipeline is constructed.
                    task_type = DeferredTaskType(task.task_type)
                    if task_type not in _RUNTIME_ONLY:
                        with self._lock:
                            self._tasks.append(task)
                            self._persist()
                        return completed
                completed.append(task)
                self._log("TASK_RELEASED", task_type=task.task_type, job_id=task.job_id)
            except Exception as exc:
                with self._lock:
                    self._tasks.append(task)
                    self._persist()
                self._log("TASK_RELEASE_FAILED", task_type=task.task_type,
                          job_id=task.job_id, error=str(exc))
                return completed

    def _ordered(self, values: list[DeferredTask]) -> list[DeferredTask]:
        return sorted(values, key=lambda item: (
            _PRIORITY.get(DeferredTaskType(item.task_type), 99), item.created_at, item.task_id
        ))

    def _load(self) -> None:
        path = self.persistence_path
        if path is None or not path.is_file():
            return
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            for value in values:
                task = DeferredTask(**value)
                if DeferredTaskType(task.task_type) not in _RUNTIME_ONLY:
                    self._tasks.append(task)
        except (OSError, ValueError, TypeError, KeyError):
            self._tasks = []
        self._persist()

    def _persist(self) -> None:
        path = self.persistence_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        durable = [asdict(task) for task in self._tasks
                   if DeferredTaskType(task.task_type) not in _RUNTIME_ONLY]
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(durable, ensure_ascii=False, indent=2)
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt, delay in enumerate((0.0, 0.05, 0.1, 0.2, 0.4)):
            if delay:
                time.sleep(delay)
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise

    def _log(self, event: str, **fields: object) -> None:
        self.logger({
            "event": event,
            "active_download_count": self.active_download_count,
            "timestamp": datetime.now(UTC).isoformat(),
            **fields,
        })
