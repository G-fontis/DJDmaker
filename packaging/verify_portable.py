"""Windows-only smoke checks for an already built DJDmaker onedir tree."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path


EXPECTED_TITLE = "台本から授業動画つくるマシーン v0.1"


def _wait_for_window(process_id: int, timeout: float = 20) -> tuple[int, str]:
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows: list[tuple[int, str]] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(handle, _data):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if pid.value == process_id and user32.IsWindowVisible(handle):
                length = user32.GetWindowTextLengthW(handle)
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(handle, text, length + 1)
                windows.append((int(handle), text.value))
            return True

        user32.EnumWindows(callback, 0)
        if windows:
            return windows[0]
        time.sleep(0.1)
    raise TimeoutError("DJDmaker GUI window did not appear")


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **kwargs,
    )


def verify(root: Path) -> dict[str, object]:
    root = root.resolve()
    executable = root / "DJDmaker.exe"
    ffmpeg = root / "runtime" / "ffmpeg" / "ffmpeg.exe"
    ffprobe = root / "runtime" / "ffmpeg" / "ffprobe.exe"
    required = (
        executable,
        ffmpeg,
        ffprobe,
        root / "config" / "default-settings.json",
        root / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll",
        root / "_internal" / "PySide6" / "plugins" / "multimedia" / "ffmpegmediaplugin.dll",
        root / "_internal" / "playwright" / "driver" / "node.exe",
    )
    missing = [str(item.relative_to(root)) for item in required if not item.is_file()]
    if missing:
        raise RuntimeError("missing portable assets: " + ", ".join(missing))

    media_versions: dict[str, str] = {}
    for name, tool in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        completed = _run([str(tool), "-version"])
        if completed.returncode != 0:
            raise RuntimeError(f"portable {name} failed: {completed.stderr}")
        media_versions[name] = completed.stdout.splitlines()[0]

    environment = os.environ.copy()
    environment["PATH"] = os.path.join(environment.get("SystemRoot", r"C:\Windows"), "System32")
    process = subprocess.Popen(
        [str(executable)],
        cwd=root,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        handle, title = _wait_for_window(process.pid)
        if title != EXPECTED_TITLE:
            raise RuntimeError(f"unexpected GUI title: {title!r}")
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)
        exit_code = process.wait(timeout=15)
        if exit_code != 0:
            raise RuntimeError(f"GUI exited with {exit_code}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    smoke_environment = {**environment, "DJD_PACKAGING_SMOKE": "1"}
    reports: dict[str, object] = {}
    for mode in ("write", "read"):
        report_path = root / "work" / f"settings-{mode}-日本語 report.json"
        completed = _run(
            [str(executable), "--packaging-settings-smoke", mode, str(report_path)],
            cwd=root,
            env=smoke_environment,
        )
        if completed.returncode != 0 or not report_path.is_file():
            raise RuntimeError(f"packaged settings {mode} failed: exit={completed.returncode}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("passed") is not True:
            raise RuntimeError(f"packaged settings {mode} did not pass")
        reports[mode] = report
        report_path.unlink()

    browser_report_path = root / "work" / "portable-browser-report.json"
    completed = _run(
        [str(executable), "--packaging-browser-smoke", str(browser_report_path)],
        cwd=root,
        env=smoke_environment,
    )
    if completed.returncode != 0 or not browser_report_path.is_file():
        raise RuntimeError(f"packaged browser smoke failed: exit={completed.returncode}")
    browser_report = json.loads(browser_report_path.read_text(encoding="utf-8"))
    if browser_report.get("passed") is not True:
        raise RuntimeError("packaged browser smoke did not pass")
    browser_report_path.unlink()

    e2e_report_path = root / "work" / "portable-fake-e2e-report.json"
    completed = _run(
        [str(executable), "--packaging-fake-e2e", str(e2e_report_path)],
        cwd=root,
        env=smoke_environment,
    )
    if completed.returncode != 0 or not e2e_report_path.is_file():
        raise RuntimeError(f"packaged Fake E2E failed: exit={completed.returncode}")
    e2e_report = json.loads(e2e_report_path.read_text(encoding="utf-8"))
    if e2e_report.get("passed") is not True:
        raise RuntimeError("packaged Fake E2E did not pass")

    return {
        "root": str(root),
        "gui_title": EXPECTED_TITLE,
        "gui_exit_code": 0,
        "media_versions": media_versions,
        "settings_restart": reports,
        "browser_smoke": browser_report,
        "portable_fake_e2e": e2e_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.root)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
