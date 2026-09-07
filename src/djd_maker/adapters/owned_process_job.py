"""Windows kernel ownership boundary for one automation driver and descendants.

No process-name/PID-tree search and no user Chrome termination. Assign the
newly created Playwright driver before it launches Chromium. Fallback operates
only on the retained anonymous Job Object handle, never on Playwright objects.
"""
import ctypes
from ctypes import wintypes
import os
import threading


class OwnedProcessJob:
    def __init__(self, pid):
        if os.name != 'nt':
            raise OSError('Windows Job Objects are required')
        self._lock = threading.Lock()
        self.pid = pid
        self._api = ctypes.WinDLL('kernel32', use_last_error=True)
        api = self._api
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        try:
            if not process or not api.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            api.CloseHandle(self._handle)
            self._handle = None
            raise
        finally:
            if process:
                api.CloseHandle(process)

    def terminate(self):
        with self._lock:
            if self._handle and not self._api.TerminateJobObject(self._handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self):
        class Accounting(ctypes.Structure):
            _fields_ = [('times', ctypes.c_int64 * 4), ('faults', wintypes.DWORD),
                        ('total', wintypes.DWORD), ('active', wintypes.DWORD),
                        ('terminated', wintypes.DWORD)]
        with self._lock:
            if not self._handle:
                return 0
            info = Accounting()
            if not self._api.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return info.active

    def close(self):
        with self._lock:
            if self._handle:
                self._api.CloseHandle(self._handle)
                self._handle = None
