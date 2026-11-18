"""Read-only, current-process evidence for a dedicated Chrome profile."""
import json
import os
import subprocess
from pathlib import Path


def profile_argument(command):
    import ctypes
    from ctypes import wintypes
    argc = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        raise ValueError('unreadable command line')
    try:
        args = [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(argv)
    for i, arg in enumerate(args):
        if arg.startswith('--user-data-dir='):
            return arg.split('=', 1)[1]
        if arg == '--user-data-dir' and i+1 < len(args):
            return args[i+1]
    return None


def profile_owners(profile):
    """Return matching PIDs, or None when ownership cannot be established.

    Never attach, terminate a process, read credentials, or remove a lock.
    Only Chrome process command lines are queried; they are never logged.
    """
    if os.name != 'nt':
        return None
    command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        run = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        if run.returncode:
            return None
        raw = run.stdout.decode('utf-8-sig', errors='replace').strip()
        rows = json.loads(raw) if raw else []
        rows = rows if isinstance(rows, list) else [rows]
        found = []
        for row in rows:
            text = row.get('CommandLine')
            if not text:
                return None  # Access denied is not evidence of no owner.
            value = profile_argument(text)
            if value and Path(value).resolve() == Path(profile).resolve():
                found.append({'pid': int(row['ProcessId']), 'parent_pid': int(row['ParentProcessId'])})
        return found
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None
