"""One cooperative stop signal shared by a run and its owned child processes."""
from contextlib import contextmanager
from contextvars import ContextVar
import subprocess
import threading
import time


class RunCancelled(BaseException):
    """Control flow, deliberately not a retryable job failure."""


class CancellationToken:
    def __init__(self):
        self.event = threading.Event()
        self.requested_at = None
        self.operation = None
        self.navigation_count = 0
        self.navigation_count_at_stop = None
        self.current_jobs = {}
        self.children = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self.pause_requested = False
        self.paused = threading.Event()
        self._pause_started = None
        self._pause_duration = 0.0

    def request_pause(self):
        with self._condition:
            if not self.pause_requested:
                self._pause_started = time.monotonic()
            self.pause_requested = True

    def resume(self):
        with self._condition:
            if self._pause_started is not None:
                self._pause_duration += time.monotonic() - self._pause_started
                self._pause_started = None
            self.pause_requested = False
            self.paused.clear()
            self._condition.notify_all()

    def request(self):
        with self._lock:
            if not self.event.is_set():
                self.requested_at = time.monotonic()
                self.navigation_count_at_stop = self.navigation_count
                self.event.set()
                self._condition.notify_all()

    def reset(self):
        with self._lock:
            if any(child.poll() is None for child in self.children.values()):
                raise RuntimeError('Cannot restart while owned child processes are active')
            self.children.clear()
            self.current_jobs.clear()
            self.event.clear()
            self.requested_at = None
            self.navigation_count_at_stop = None
            self.operation = None
            self.pause_requested = False
            self.paused.clear()
            self._pause_started = None
            self._pause_duration = 0.0
            self._condition.notify_all()

    def check(self, operation=None):
        with self._condition:
            # An already running child may finish; no new child/task can start.
            while self.pause_requested and operation != 'subprocess.wait' and not self.event.is_set():
                self.paused.set()
                self._condition.wait()
            if self.event.is_set():
                raise RunCancelled("STOP_REQUESTED")
            if operation:
                self.operation = operation

    def navigation(self):
        with self._lock:
            self.check('page.goto')
            self.navigation_count += 1

    def wait(self, seconds):
        self.check()
        if self.event.wait(max(0, seconds)):
            raise RunCancelled('STOP_REQUESTED')
        self.check()

    def diagnostic(self):
        return dict(stop_requested_at=self.requested_at, current_operation=self.operation,
                    navigation_count_before=self.navigation_count_at_stop,
                    navigation_count_after=self.navigation_count,
                    current_jobs=dict(self.current_jobs), active_child_pids=list(self.children))


_current = ContextVar('djd_run_cancellation', default=None)


def current_token():
    return _current.get()


def active_monotonic():
    """Observation deadlines exclude time deliberately spent paused."""
    now = time.monotonic()
    token = current_token()
    if token is None:
        return now
    with token._lock:
        return now - token._pause_duration - (now-token._pause_started if token._pause_started is not None else 0)


def checkpoint(operation=None, job_id=None):
    token = current_token()
    if token:
        if job_id is not None:
            token.current_jobs[threading.get_ident()] = job_id
        token.check(operation)
    if operation:
        from .runtime_operation import report_operation
        report_operation(operation)


@contextmanager
def cancellation_scope(token):
    marker = _current.set(token)
    try:
        yield
    finally:
        _current.reset(marker)


def interruptible_sleep(seconds):
    token = current_token()
    if token:
        token.wait(seconds)
    else:
        time.sleep(seconds)


def run_process(command, **kwargs):
    """Keep subprocess.run compatibility outside a cancellable pipeline run."""
    token = current_token()
    if token is None:
        return subprocess.run(command, **kwargs)
    token.check('subprocess.start')
    timeout = kwargs.pop('timeout', None)
    check = kwargs.pop('check', False)
    if kwargs.pop('capture_output', False):
        kwargs['stdout'] = subprocess.PIPE
        kwargs['stderr'] = subprocess.PIPE
    process = subprocess.Popen(command, **kwargs)
    token.children[process.pid] = process
    started = time.monotonic()
    try:
        while True:
            token.check('subprocess.wait')
            if timeout is not None and time.monotonic()-started >= timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=.2)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                if check:
                    result.check_returncode()
                token.check()
                return result
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)
        token.children.pop(process.pid, None)
