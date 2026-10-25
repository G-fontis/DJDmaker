"""Short, reentrant, owner-aware sequence locking. No age-based deletion."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

RETRY_DELAYS = (.05, .1, .2, .4, .8, 1.6)
_guard = threading.Lock()
_locks = {}
_held = threading.local()


class SequenceUnavailable(RuntimeError):
    code = 'WAITING_FOR_JOBNO_LOCK'
    def __init__(self, path, reason, owner=None):
        self.path, self.reason, self.owner = Path(path), reason, owner
        super().__init__(f'{self.code}: {path}: {reason}; owner={owner}')


def process_identity(pid):
    """Return alive/start epoch. Unknown permission is never proof of death."""
    if os.name != 'nt':
        try:os.kill(pid, 0);return True, None
        except ProcessLookupError:return False, None
        except PermissionError:return None, None
    from ctypes import wintypes as w
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE]
    k.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    k.GetProcessTimes.argtypes=[w.HANDLE,*[ctypes.POINTER(w.FILETIME)]*4]
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return (False if ctypes.get_last_error()==87 else None), None
    try:
        code=w.DWORD()
        if not k.GetExitCodeProcess(h,ctypes.byref(code)):return None,None
        values=[w.FILETIME() for _ in range(4)]
        start=None
        if k.GetProcessTimes(h,*[ctypes.byref(x) for x in values]):
            start=((values[0].dwHighDateTime<<32)+values[0].dwLowDateTime)/10000000-11644473600
        return code.value==259,start
    finally:k.CloseHandle(h)


def _os_lock(stream, unlock=False):
    stream.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_UN if unlock else fcntl.LOCK_EX|fcntl.LOCK_NB)


def _legacy_owner(data):
    match=re.fullmatch(rb'pid=(\d+) time=([\d.]+)\s*',data)
    if not match:return None
    try:pid=int(match[1]);stamp=float(match[2])
    except ValueError:return None
    if pid<=0:return None
    alive,start=process_identity(pid)
    # Legacy records lack a process-start identity. Clock adjustment or PID
    # reuse is ambiguous: never take a legacy marker from a live PID.
    if alive and start is not None and start>stamp+.1:alive=None
    return {'pid':pid,'created_at':stamp,'alive':alive,'process_start':start}


@contextmanager
def sequence_lock(path, *, delays=RETRY_DELAYS, sleeper=time.sleep):
    path=Path(path);key=os.path.normcase(str(path.resolve()))
    held=getattr(_held,'paths',None)
    if held is None:held={};_held.paths=held
    if key in held:
        yield
        return
    with _guard:local=_locks.setdefault(key,threading.RLock())
    marker=path.with_name('.'+path.name+'.lock')
    reason='same-process contention';owner=None;stream=None
    for attempt in range(len(delays)+1):
        if local.acquire(timeout=.05):
            locked=False
            try:
                marker.parent.mkdir(parents=True,exist_ok=True)
                stream=os.fdopen(os.open(marker,os.O_RDWR|os.O_CREAT,0o600),'r+b',buffering=0)
                _os_lock(stream);locked=True
                stream.seek(0);data=stream.read()
                if data and not data.startswith(b'\0'):
                    owner=_legacy_owner(data)
                    if owner is None or owner['alive'] is not False:
                        raise SequenceUnavailable(path,'legacy owner live or unknown',owner)
                metadata={'pid':os.getpid(),'created_at':time.time(),'host':socket.gethostname(),
                          'process_start':process_identity(os.getpid())[1],'released':False,'kind':'os_byte_lock'}
                stream.seek(0);stream.write(b'\0'+json.dumps(metadata).encode());stream.truncate();stream.flush()
                held[key]=stream
                break
            except BaseException as exc:
                reason=str(exc)
                if stream is not None:
                    if not locked:
                        try:
                            stream.seek(1);owner=json.loads(stream.read().decode('utf8'))
                        except (OSError,ValueError,UnicodeError):pass
                    try:
                        if locked:_os_lock(stream,True)
                    finally:
                        try:stream.close()
                        finally:stream=None;local.release()
                else:local.release()
                if not isinstance(exc,(OSError,SequenceUnavailable)):raise
        if attempt<len(delays):sleeper(delays[attempt])
    else:raise SequenceUnavailable(path,reason,owner)
    try:
        yield
    finally:
        held.pop(key,None)
        try:
            metadata['released']=True
            stream.seek(1);stream.write(json.dumps(metadata).encode());stream.truncate();stream.flush()
        finally:
            try:_os_lock(stream,True)
            finally:
                try:stream.close()
                finally:local.release()
