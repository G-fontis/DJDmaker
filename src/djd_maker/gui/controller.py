from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class GuiControllerPort(Protocol):
    """Boundary implemented by the scheduler/application composition root."""

    def reload(self) -> Any: ...

    def start(self) -> Any: ...

    def pause(self) -> Any: ...

    def stop(self) -> Any: ...

    def retry(self, job_id: str, stage: str) -> Any: ...

    def shutdown(self) -> Any: ...


class _TaskSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    done = Signal(str)


class _ControllerTask(QRunnable):
    def __init__(self, operation: str, call: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.call = call
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.call()
        except Exception as exc:  # controller failures belong in the GUI error channel
            self.signals.failed.emit(self.operation, str(exc))
        else:
            self.signals.succeeded.emit(self.operation, result)
        finally:
            self.signals.done.emit(self.operation)


class AsyncControllerBridge(QObject):
    """Runs controller commands away from the UI thread and relays immutable data."""

    jobs_changed = Signal(object)
    status_changed = Signal(object)
    log_received = Signal(object)
    operation_started = Signal(str)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str, str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        controller: GuiControllerPort,
        parent: QObject | None = None,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._active = 0
        self._lock = threading.Lock()
        self._closing = False
        self._tasks: set[_ControllerTask] = set()
        binder = getattr(controller, "bind", None)
        if callable(binder):
            binder(
                jobs=self.publish_jobs,
                status=self.publish_status,
                log=self.publish_log,
                error=self.operation_failed.emit,
            )

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active > 0

    def _invoke(self, operation: str, call: Callable[[], Any]) -> bool:
        with self._lock:
            if self._closing:
                return False
            self._active += 1
        task = _ControllerTask(operation, call)
        self._tasks.add(task)
        task.signals.succeeded.connect(self._on_success)
        task.signals.failed.connect(self.operation_failed)
        task.signals.done.connect(lambda name, worker=task: self._on_done(name, worker))
        self.operation_started.emit(operation)
        self.busy_changed.emit(True)
        self.thread_pool.start(task)
        return True

    @Slot(str, object)
    def _on_success(self, operation: str, result: object) -> None:
        if operation == "reload" and result is not None:
            self.jobs_changed.emit(result)
        self.operation_finished.emit(operation, result)

    @Slot(str)
    def _on_done(self, _operation: str, task: _ControllerTask) -> None:
        self._tasks.discard(task)
        with self._lock:
            self._active = max(0, self._active - 1)
            busy = self._active > 0
        self.busy_changed.emit(busy)

    def reload(self) -> bool:
        return self._invoke("reload", self.controller.reload)

    def start(self) -> bool:
        return self._invoke("start", self.controller.start)

    def pause(self) -> bool:
        return self._invoke("pause", self.controller.pause)

    def stop(self) -> bool:
        return self._invoke("stop", self.controller.stop)

    def retry(self, job_id: str, stage: str) -> bool:
        return self._invoke(f"retry:{stage}", lambda: self.controller.retry(job_id, stage))

    @Slot(object)
    def publish_jobs(self, jobs: object) -> None:
        self.jobs_changed.emit(jobs)

    @Slot(object)
    def publish_status(self, status: object) -> None:
        self.status_changed.emit(status)

    @Slot(object)
    def publish_log(self, record: object) -> None:
        self.log_received.emit(record)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Reject new work, request controller shutdown, then drain owned workers."""
        with self._lock:
            self._closing = True
        try:
            self.controller.shutdown()
        except Exception as exc:
            self.operation_failed.emit("shutdown", str(exc))
            return False
        return self.thread_pool.waitForDone(timeout_ms)
