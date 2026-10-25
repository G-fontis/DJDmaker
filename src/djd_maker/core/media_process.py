"""Media subprocess diagnostics and bounded, job-persisted command retries."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading

BACKOFF = (2, 5, 10)
_retry = ContextVar('media_retry', default=None)
_spawn_lock = threading.RLock()
_audit_lock = threading.Lock()
_audit = {'spawned': 0, 'active': 0, 'peak': 0, 'finished': 0}


class MediaProcessError(RuntimeError):
    def __init__(self, code, detail='', *, retryable=False, windows_error=None, returncode=None, stderr=''):
        self.code, self.retryable = code, retryable
        self.windows_error, self.returncode, self.stderr = windows_error, returncode, stderr
        super().__init__(f'{code}: {detail} windows_error={windows_error} returncode={returncode} stderr={stderr[-1000:]}')


def process_error(*, result=None, error=None):
    rc = getattr(result, 'returncode', None)
    status = (rc & 0xffffffff) if rc is not None else None
    win = getattr(error, 'winerror', None)
    stderr = str(getattr(result, 'stderr', '') or '')
    if status == 0xc0000142 or win == 1114:
        return MediaProcessError('FFMPEG_PROCESS_START_FAILED', retryable=True,
                                 windows_error='0xc0000142', returncode=rc, stderr=stderr)
    if isinstance(error, (FileNotFoundError, PermissionError)) and win not in {32, 33} or win in {126, 127, 193} or status in {0xc0000135, 0xc000007b}:
        return MediaProcessError('FFMPEG_FATAL_GLOBAL', str(error or ''), windows_error=hex(status) if status else win, returncode=rc, stderr=stderr)
    if isinstance(error, subprocess.TimeoutExpired):
        return MediaProcessError('FFMPEG_TIMEOUT', str(error), retryable=True, stderr=str(error.stderr or ''))
    if error is not None or status is not None and status >= 0x80000000:
        return MediaProcessError('FFMPEG_RETRYABLE', str(error or ''), retryable=True,
                                 windows_error=hex(status) if status else win, returncode=rc, stderr=stderr)
    return None


def classify_media_failure(result):
    failure = process_error(result=result)
    if failure is not None or not result.returncode:
        return failure
    text = str(result.stderr or '').casefold()
    if any(s in text for s in ('invalid data', 'no such file', 'stream map', 'does not contain any stream', 'invalid argument')):
        code, retryable = 'FFMPEG_INPUT_INVALID', False
    elif any(s in text for s in ('permission denied', 'no space left', 'error opening output', 'i/o error', 'sharing violation')):
        code, retryable = 'FFMPEG_OUTPUT_IO_ERROR', True
    else:
        code, retryable = 'FFMPEG_FATAL_JOB', False
    return MediaProcessError(code, returncode=result.returncode, stderr=str(result.stderr or ''), retryable=retryable)


@contextmanager
def media_retry_scope(job, save):
    marker = _retry.set((job, save))
    try:
        yield
    finally:
        _retry.reset(marker)


def run_media_process(command, *, runner, purpose, **kwargs):
    from .cancellation import interruptible_sleep, checkpoint
    from .runtime_operation import report_operation
    scope = _retry.get()
    job, save = scope if scope else (None, None)
    key = f'ffmpeg.{job.state.value}.{purpose}' if job else None
    failures = job.attempt_by_stage.get(key, 0) if job else 0
    while True:
        if failures >= 4:
            raise MediaProcessError('FFMPEG_RETRY_EXHAUSTED', purpose)
        checkpoint('ffmpeg.attempt', job.id if job else None)
        if failures:
            interruptible_sleep(BACKOFF[failures - 1])
        try:
            result = runner(command, **kwargs)
            failure = classify_media_failure(result) if scope else process_error(result=result)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failure = process_error(error=exc)
        if failure is None:
            if job and str(job.error_code).startswith('FFMPEG_'):
                job.error_code = job.error_message = None
                save(job)
            return result
        logging.getLogger(__name__).warning('%s attempt=%s/4 stage=%s', failure, failures + 1, purpose)
        if not scope or not failure.retryable:
            raise failure
        failures += 1
        job.attempt_by_stage[key] = failures
        job.error_code = failure.code
        job.error_message = str(failure)
        save(job)  # Never retry an effect after its budget failed to persist.
        # Only UUID-named attempt outputs are ours; never unlink a canonical
        # input, existing completed MP4, ZIP, or a validated checkpoint.
        output = Path(str(command[-1]))
        if output.name.startswith('.') and output.name.endswith(('.staging.mp4', '.trimmed.mp4')):
            output.unlink(missing_ok=True)
        if failures >= 4:
            raise MediaProcessError('FFMPEG_RETRY_EXHAUSTED', str(failure), windows_error=failure.windows_error,
                                    returncode=failure.returncode, stderr=failure.stderr)
        report_operation('ffmpeg.retry', retry=failures, max_retries=3,
                         decision=f'FFmpegを起動できませんでした。自動再試行します。{failures}/3',
                         windows_error=failure.windows_error, attempt=failures+1)


@contextmanager
def media_spawn_options(command, kwargs):
    """Only hold the process-wide inherited error mode during CreateProcess."""
    media = Path(str(command[0])).stem.casefold() in {'ffmpeg', 'ffprobe'}
    if not media or os.name != 'nt':
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    with _spawn_lock:
        previous = kernel.GetErrorMode()
        kernel.SetErrorMode(previous | 0x0001 | 0x0002 | 0x8000)
        dll_directory = None
        if getattr(sys, 'frozen', False):
            buffer = ctypes.create_unicode_buffer(32768)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            dll_directory = buffer.value
            kernel.SetDllDirectoryW(None)
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
        try:
            yield
        finally:
            if dll_directory is not None:
                kernel.SetDllDirectoryW(dll_directory or None)
            kernel.SetErrorMode(previous)


def process_audit(delta):
    with _audit_lock:
        _audit['active'] += delta
        _audit['spawned' if delta > 0 else 'finished'] += 1
        _audit['peak'] = max(_audit['peak'], _audit['active'])


def process_audit_snapshot():
    with _audit_lock:
        return dict(_audit)
