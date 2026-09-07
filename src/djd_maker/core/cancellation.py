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
        self._lock = threading.Lock()

    def request(self):
        with self._lock:
            if not self.event.is_set():
                self.requested_at = time.monotonic()
                self.navigation_count_at_stop = self.navigation_count
                self.event.set()

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

    def check(self, operation=None):
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

    def diagnostic(self):
        return dict(stop_requested_at=self.requested_at, current_operation=self.operation,
                    navigation_count_before=self.navigation_count_at_stop,
                    navigation_count_after=self.navigation_count,
                    current_jobs=dict(self.current_jobs), active_child_pids=list(self.children))


_current = ContextVar('djd_run_cancellation', default=None)


def current_token():
    return _current.get()


def checkpoint(operation=None, job_id=None):
    token = current_token()
    if token:
        if job_id is not None:
            token.current_jobs[threading.get_ident()] = job_id
        token.check(operation)


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
