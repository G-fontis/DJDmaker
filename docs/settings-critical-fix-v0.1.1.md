# Settings critical fix v0.1.1

Instruction: `DJD-CHAPPY-V01-CRITICAL-SETTINGS-SAVE-REDESIGN-001`

## Root cause and reproduction

The former `_document_lock()` created `.settings.json.lock` with
`os.open(..., O_CREAT | O_EXCL | O_WRONLY)`, closed its own descriptor, and
unconditionally called `lock_path.unlink()` in the context manager's `finally`
block. On Windows, another process such as a sync/index scanner can open that
file without delete sharing between those operations. The protected settings
save can therefore finish, but lock cleanup raises WinError 32 and the GUI
reports the whole save as failed.

The pre-fix reproduction captured this exact traceback at the former
`src/djd_maker/core/repositories.py:87`:

```text
Traceback (most recent call last):
  File "C:\xampp\htdocs\PHP\DJDmaker\work\reproduce_settings_winerror32.py", line 13, in <module>
    with _document_lock(settings):
  File "C:\Users\Ichiro\AppData\Local\Python\pythoncore-3.14-64\Lib\contextlib.py", line 148, in __exit__
    next(self.gen)
  File "C:\xampp\htdocs\PHP\DJDmaker\src\djd_maker\core\repositories.py", line 87, in _document_lock
    lock_path.unlink()
  File "C:\Users\Ichiro\AppData\Local\Python\pythoncore-3.14-64\Lib\pathlib\__init__.py", line 1042, in unlink
    os.unlink(self)
PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: '.settings.json.lock'
```

Settings load/save, GUI folder and Ending changes, and worker settings reads all
went through `SettingsRepository`. Startup loads settings; normal shutdown does
not save them. No single-instance mechanism used this lock.

## New design

- Settings operations use a path-keyed process-local `threading.RLock` shared
  by every `SettingsRepository` instance.
- Settings never create, acquire, read, remove, or reuse either legacy
  `.settings.json.lock` or `settings.json.lock`.
- JSON is written to a unique same-directory temporary, flushed, `fsync`ed,
  and atomically published with `os.replace`.
- A valid previous document is atomically retained as `settings.json.bak`.
- The final publish retries transient `PermissionError`/WinError 32 after
  100, 200, 400, and 800 ms; the fifth failure is final. There is no infinite
  retry.
- A fully flushed temporary remains available for recovery when all publish
  attempts fail. Malformed primary data recovers from backup or a valid
  temporary.
- The GUI only displays an error after retries are exhausted and states that
  the existing settings are intact and the operation can be retried.
- Job, queue, and state document locking is unchanged. Settings persistence and
  any future single-instance control are separate concerns.

## Verification evidence

- Full source suite: 204 tests passed.
- Includes single, rapid, 100-save, multi-thread, GUI rapid-change,
  PermissionError, WinError 32, retry success/exhaustion, legacy/read-only lock,
  Dropbox/Japanese/space/deep paths, backup, atomicity, crash temporary,
  malformed recovery, restart, Ending/folder/scheduler settings tests.
- Portable build: `dist/DJDmaker_v0.1.1`, PyInstaller onedir, version 0.1.1.
- Actual Dropbox path:
  `C:\Users\Ichiro\Dropbox\DJDmaker 設定検証 20260906\日本語 path\DJDmaker_v0.1.1`.
- Portable settings values 201 through 220 were each written by one EXE process
  and restored by a newly started EXE process: 20/20 passed, 0 save errors.
- During all 20 cycles both legacy lock names existed; `.settings.json.lock`
  was read-only.
- A separate clean Dropbox/Japanese/space-path portable copy passed GUI title,
  settings restart, FFmpeg/ffprobe, browser smoke, and fake end-to-end checks.
- GitHub push was not performed.
