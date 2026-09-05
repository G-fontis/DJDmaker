from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


WRITABLE_DIRECTORIES = (
    "input",
    "raw_files",
    "output",
    "work",
    "system",
    "system/jobs",
    "logs",
    "browser",
    "browser/chrome-profile",
)
DEFAULT_SETTING_KEYS = frozenset(
    {
        "input_directory",
        "raw_directory",
        "output_directory",
        "ending_video",
        "first_notebook_check_seconds",
        "notebook_poll_seconds",
        "audio_tail_padding_seconds",
        "ffmpeg_concurrency",
    }
)
PRIVATE_PROFILE_NAMES = frozenset(
    {
        "cookies",
        "cookies-journal",
        "history",
        "history-journal",
        "login data",
        "login data-journal",
        "local state",
        "web data",
    }
)


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class PreflightReport:
    root: Path
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.checks if item.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "passed": self.passed,
            "checks": [asdict(item) for item in self.checks],
        }


def application_root() -> Path:
    """Return the portable folder, never PyInstaller's temporary resource path."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def find_media_tool(
    root: Path,
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    environment = os.environ if environ is None else environ
    variable = environment.get(f"DJD_{name.upper()}_PATH", "")
    candidates = [
        Path(variable) if variable else None,
        root / "runtime" / "ffmpeg" / f"{name}.exe",
        root / "tools" / "ffmpeg" / "bin" / f"{name}.exe",
    ]
    located = which(name)
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    return None


def find_chrome(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    environment = os.environ if environ is None else environ
    candidates: list[Path] = []
    explicit = environment.get("DJD_CHROME_PATH")
    if explicit:
        candidates.append(Path(explicit))
    located = which("chrome") or which("chrome.exe")
    if located:
        candidates.append(Path(located))
    for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = environment.get(variable)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return next((item.resolve() for item in candidates if item.is_file()), None)


def _run_version(
    executable: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[bool, str]:
    try:
        result = runner(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    lines = (result.stdout or result.stderr or "").splitlines()
    return result.returncode == 0, lines[0] if lines else f"exit={result.returncode}"


def _probe_writable(directory: Path) -> tuple[bool, str]:
    created_directories: list[Path] = []
    marker: Path | None = None
    try:
        if not directory.exists():
            cursor = directory
            while not cursor.exists() and cursor != cursor.parent:
                created_directories.append(cursor)
                cursor = cursor.parent
            directory.mkdir(parents=True)
        if not directory.is_dir():
            return False, "path exists but is not a directory"
        marker = directory / f".djd-write-probe-{uuid4().hex}.tmp"
        marker.write_bytes(b"djd")
        if marker.read_bytes() != b"djd":
            return False, "write verification did not round-trip"
        return True, str(directory.resolve())
    except OSError as exc:
        return False, str(exc)
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)
        for created in created_directories:
            try:
                created.rmdir()
            except OSError:
                pass


def _config_check(path: Path) -> tuple[bool, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("root must be an object")
        missing = sorted(DEFAULT_SETTING_KEYS - value.keys())
        if missing:
            raise ValueError("missing keys: " + ", ".join(missing))
        if value.get("audio_tail_padding_seconds") != 0.5:
            raise ValueError("audio_tail_padding_seconds must be 0.5")
        if value.get("ffmpeg_concurrency") not in {1, 2}:
            raise ValueError("ffmpeg_concurrency must be 1 or 2")
        return True, str(path.resolve())
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return False, str(exc)


def _qt_checks() -> tuple[PreflightCheck, ...]:
    try:
        from PySide6 import __version__
        from PySide6.QtCore import QLibraryInfo
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtWidgets import QApplication

        plugin_root = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
        platforms = plugin_root / "platforms"
        multimedia = plugin_root / "multimedia"
        return (
            PreflightCheck("pyside6", True, str(__version__)),
            PreflightCheck("qt-platform-plugin", platforms.is_dir(), str(platforms)),
            PreflightCheck("qt-multimedia-plugin", multimedia.is_dir(), str(multimedia)),
            PreflightCheck(
                "qt-imports",
                QApplication is not None and QMediaPlayer is not None,
                "Qt Widgets and Multimedia imports succeeded",
            ),
        )
    except Exception as exc:
        return (
            PreflightCheck("pyside6", False, str(exc)),
            PreflightCheck("qt-platform-plugin", False, "PySide6 unavailable"),
            PreflightCheck("qt-multimedia-plugin", False, "PySide6 unavailable"),
            PreflightCheck("qt-imports", False, "PySide6 unavailable"),
        )


def release_tree_violations(root: Path) -> list[str]:
    """Reject private runtime data and incomplete portable layouts."""
    violations: list[str] = []
    expected = (
        root / "DJDmaker.exe",
        root / "_internal",
        root / "config" / "default-settings.json",
        root / "runtime" / "ffmpeg" / "ffmpeg.exe",
        root / "runtime" / "ffmpeg" / "ffprobe.exe",
        root / "licenses" / "FFmpeg-LICENSE.txt",
    )
    for item in expected:
        if not item.exists():
            violations.append(f"missing release asset: {item.relative_to(root)}")
    for relative in ("browser", "logs", "system", "input", "raw_files", "output", "work"):
        directory = root / relative
        if not directory.is_dir():
            violations.append(f"missing writable directory: {relative}")
            continue
        for item in directory.rglob("*"):
            if item.is_file():
                violations.append(f"runtime/user data included: {item.relative_to(root)}")
    for item in root.rglob("*"):
        if item.is_file() and item.name.casefold() in PRIVATE_PROFILE_NAMES:
            violations.append(f"private browser file included: {item.relative_to(root)}")
    return sorted(set(violations))


def inspect_packaging(
    root: Path,
    *,
    release_tree: bool = False,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    module_finder: Callable[[str], object | None] = importlib.util.find_spec,
) -> PreflightReport:
    root = root.resolve()
    environment = os.environ if environ is None else environ
    checks: list[PreflightCheck] = [
        PreflightCheck("windows", os.name == "nt", os.name),
        PreflightCheck("python", sys.version_info >= (3, 11), sys.version.split()[0]),
        PreflightCheck("onedir-layout", root.is_dir(), str(root)),
    ]
    for module in ("PyInstaller", "playwright"):
        found = module_finder(module) is not None
        checks.append(PreflightCheck(module.casefold(), found, "installed" if found else "not installed"))
    checks.extend(_qt_checks())

    for tool_name in ("ffmpeg", "ffprobe"):
        executable = find_media_tool(root, tool_name, environ=environment, which=which)
        if executable is None:
            checks.append(PreflightCheck(tool_name, False, "not found in portable runtime, tools, explicit path, or PATH"))
        else:
            passed, detail = _run_version(executable, runner)
            checks.append(PreflightCheck(tool_name, passed, f"{executable}: {detail}"))

    chrome = find_chrome(environ=environment, which=which)
    checks.append(
        PreflightCheck(
            "google-chrome",
            chrome is not None,
            str(chrome) if chrome else "installed Google Chrome was not found",
        )
    )
    config = root / "config" / "default-settings.json"
    passed, detail = _config_check(config)
    checks.append(PreflightCheck("default-config", passed, detail))

    for relative in WRITABLE_DIRECTORIES:
        passed, detail = _probe_writable(root / relative)
        checks.append(PreflightCheck(f"writable:{relative}", passed, detail))
    unicode_probe = root / "日本語 path 検証"
    passed, detail = _probe_writable(unicode_probe)
    checks.append(PreflightCheck("unicode-space-path", passed, detail))

    profile = root / "browser" / "chrome-profile"
    private_files = [
        item for item in profile.rglob("*")
        if item.is_file() and item.name.casefold() in PRIVATE_PROFILE_NAMES
    ] if profile.exists() else []
    checks.append(
        PreflightCheck(
            "profile-not-prepopulated",
            not release_tree or not private_files,
            "existing local profile is retained" if not release_tree else f"private files: {len(private_files)}",
        )
    )
    if release_tree:
        violations = release_tree_violations(root)
        checks.append(
            PreflightCheck(
                "release-tree-safety",
                not violations,
                "complete and clean" if not violations else "; ".join(violations),
            )
        )
    return PreflightReport(root, tuple(checks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DJDmaker Windows packaging preflight")
    parser.add_argument("--root", type=Path, default=application_root())
    parser.add_argument("--release-tree", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect_packaging(args.root, release_tree=args.release_tree)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for item in report.checks:
            marker = "PASS" if item.passed else ("FAIL" if item.required else "WARN")
            print(f"[{marker}] {item.name}: {item.detail}")
        print("PACKAGING_PREFLIGHT:", "PASS" if report.passed else "BLOCKED")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
