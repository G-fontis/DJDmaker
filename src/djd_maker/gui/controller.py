from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, QTimer
from djd_maker.core.commands import EventId, PresentationEvent


class GuiControllerPort(Protocol):
    """Boundary implemented by the scheduler/application composition root."""

    def reload(self) -> Any: ...

    def start(self) -> Any: ...

    def pause(self) -> Any: ...

    def stop(self) -> Any: ...

    def login(self) -> Any: ...

    def recover_pending(self) -> Any: ...

    def refresh_credit(self) -> Any: ...

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
    job_changed = Signal(object)
    status_changed = Signal(object)
    log_received = Signal(object)
    operation_started = Signal(str)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str, str)
    busy_changed = Signal(bool)
    presentation_event = Signal(object)

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
        self._limit_active = False
        self._last_phase = None
        self._pause_acknowledged = False
        self._pause_timer = QTimer(self)
        self._pause_timer.timeout.connect(self._observe_pause)
        self._pause_timer.start(200)
        self.operation_failed.connect(lambda name, message: self.presentation_event.emit(
            PresentationEvent(EventId.ERROR, {'operation': name, 'message': message})))
        self.jobs_changed.connect(lambda p: self.presentation_event.emit(PresentationEvent(EventId.JOBS_UPDATED, p)))
        self.job_changed.connect(lambda p: self.presentation_event.emit(PresentationEvent(EventId.JOB_UPDATED, p)))
        self.status_changed.connect(self._status_event)
        bind_job = getattr(controller, 'bind_job', None)
        if callable(bind_job):
            bind_job(self.publish_job)
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
        request = getattr(self.controller, 'request_pause', None)
        if callable(request):
            request()
        return self._invoke("pause", self.controller.pause)

    def resume(self) -> bool:
        return self._invoke("resume", self.controller.resume)

    def stop(self) -> bool:
        request = getattr(self.controller, 'request_stop', None)
        if callable(request):
            request()
        return self._invoke("stop", self.controller.stop)

    def login(self) -> bool:
        return self._invoke("login", self.controller.login)

    def recover_pending(self) -> bool:
        return self._invoke("recover", self.controller.recover_pending)

    def refresh_credit(self) -> bool:
        return self._invoke("credit", self.controller.refresh_credit)

    def delete_completed(self, job_ids: list[str]) -> bool:
        return self._invoke("delete_completed", lambda: self.controller.delete_completed(job_ids))

    def retry(self, job_id: str, stage: str) -> bool:
        return self._invoke(f"retry:{stage}", lambda: self.controller.retry(job_id, stage))

    @Slot(object)
    def publish_job(self, job: object) -> None:
        if not self._closing:
            self.job_changed.emit(job)

    @Slot(object)
    def publish_jobs(self, jobs: object) -> None:
        self.jobs_changed.emit(jobs)

    @Slot(object)
    def publish_status(self, status: object) -> None:
        self.status_changed.emit(status)

    def _status_event(self, status):
        self.presentation_event.emit(PresentationEvent(EventId.RUNTIME_STATUS, status))
        if not isinstance(status, dict):
            return
        phase = status.get('runtime', {}).get('phase', status.get('phase'))
        if phase != self._last_phase:
            self._last_phase = phase
            self.presentation_event.emit(PresentationEvent(EventId.PHASE_CHANGED, {'phase': phase}))
        active = bool(status.get('cloud_limit', {}).get('active'))
        if active:
            event = EventId.LIMIT_WAITING if self._limit_active else EventId.LIMIT_DETECTED
            self.presentation_event.emit(PresentationEvent(event, status['cloud_limit']))
        elif self._limit_active:
            self.presentation_event.emit(PresentationEvent(EventId.LIMIT_RELEASED, {}))
        self._limit_active = active
        if status.get('pause_state') == 'PAUSED':
            self.presentation_event.emit(PresentationEvent(EventId.PAUSED, status))
        if status.get('phase') == 'STOPPED':
            self.presentation_event.emit(PresentationEvent(EventId.STOPPED, status))

    def _observe_pause(self):
        # Read-only acknowledgment on the Qt thread; never access Playwright.
        token = getattr(self.controller, 'cancellation', None)
        acknowledged = bool(token and token.paused.is_set())
        if acknowledged and not self._pause_acknowledged and not self._closing:
            self.publish_status(self.controller.status())
        self._pause_acknowledged = acknowledged

    @Slot(object)
    def publish_log(self, record: object) -> None:
        self.log_received.emit(record)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Reject new work, request controller shutdown, then drain owned workers."""
        self._pause_timer.stop()
        with self._lock:
            self._closing = True
        try:
            self.controller.shutdown()
        except Exception as exc:
            self.operation_failed.emit("shutdown", str(exc))
            return False
        return self.thread_pool.waitForDone(timeout_ms)
