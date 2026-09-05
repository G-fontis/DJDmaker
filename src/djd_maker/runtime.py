from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RuntimeReport:
    checks: tuple[RuntimeCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "checks": [asdict(item) for item in self.checks]}


def _tool_check(name: str, runner: Callable[..., subprocess.CompletedProcess[str]]) -> RuntimeCheck:
    executable = shutil.which(name)
    if executable is None:
        return RuntimeCheck(name, False, "not found on PATH")
    try:
        completed = runner(
            [executable, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RuntimeCheck(name, False, str(exc))
    first_line = (completed.stdout or completed.stderr).splitlines()
    detail = first_line[0] if first_line else f"exit={completed.returncode}"
    return RuntimeCheck(name, completed.returncode == 0, detail)


def inspect_runtime(
    probe_root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RuntimeReport:
    """Inspect the dedicated GUI runtime without opening windows or media files."""

    checks: list[RuntimeCheck] = [
        RuntimeCheck(
            "python",
            sys.version_info >= (3, 11),
            f"{platform.python_implementation()} {platform.python_version()}",
        )
    ]

    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import QLibraryInfo
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        plugin_path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        checks.extend(
            (
                RuntimeCheck("pyside6", True, str(pyside_version)),
                RuntimeCheck("qt_plugins", plugin_path.is_dir(), str(plugin_path)),
                RuntimeCheck(
                    "qt_multimedia",
                    QMediaPlayer is not None and QAudioOutput is not None,
                    "QMediaPlayer and QAudioOutput available",
                ),
            )
        )
    except Exception as exc:  # import/runtime backend failures are report data
        checks.extend(
            (
                RuntimeCheck("pyside6", False, str(exc)),
                RuntimeCheck("qt_plugins", False, "PySide6 unavailable"),
                RuntimeCheck("qt_multimedia", False, "PySide6 Multimedia unavailable"),
            )
        )

    checks.append(_tool_check("ffmpeg", runner))
    checks.append(_tool_check("ffprobe", runner))
    checks.append(
        RuntimeCheck(
            "windows_shell_open",
            os.name == "nt" and hasattr(os, "startfile"),
            "os.startfile available" if hasattr(os, "startfile") else "os.startfile unavailable",
        )
    )

    unicode_dir = probe_root.resolve() / "日本語パス確認"
    unicode_file = unicode_dir / "授業.txt"
    try:
        unicode_dir.mkdir(parents=True, exist_ok=True)
        unicode_file.write_text("確認", encoding="utf-8")
        passed = unicode_file.read_text(encoding="utf-8") == "確認"
        detail = str(unicode_file)
    except OSError as exc:
        passed = False
        detail = str(exc)
    finally:
        try:
            unicode_file.unlink(missing_ok=True)
            unicode_dir.rmdir()
        except OSError:
            pass
    checks.append(RuntimeCheck("unicode_path", passed, detail))
    return RuntimeReport(tuple(checks))
