# -*- mode: python ; coding: utf-8 -*-
"""Candidate PyInstaller 6 one-folder definition; it does not build a ZIP."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


PROJECT_ROOT = Path(SPEC).resolve().parent.parent


def reviewed_file(variable, expected_name):
    value = os.environ.get(variable, "")
    candidate = Path(value).expanduser().resolve() if value else None
    if candidate is None or not candidate.is_file() or candidate.name.casefold() != expected_name.casefold():
        raise SystemExit(f"{variable} must name a reviewed {expected_name}")
    return candidate


ffmpeg = reviewed_file("DJD_FFMPEG_PATH", "ffmpeg.exe")
ffprobe = reviewed_file("DJD_FFPROBE_PATH", "ffprobe.exe")
ffmpeg_license = reviewed_file("DJD_FFMPEG_LICENSE", "FFmpeg-LICENSE.txt")
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[
        *playwright_binaries,
        (str(ffmpeg), "runtime/ffmpeg"),
        (str(ffprobe), "runtime/ffmpeg"),
    ],
    datas=[
        *playwright_datas,
        (str(PROJECT_ROOT / "config" / "default-settings.json"), "config"),
        (str(ffmpeg_license), "licenses"),
    ],
    hiddenimports=[
        *playwright_hiddenimports,
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "playwright.sync_api",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "runtime_hooks" / "portable_paths.py")],
    excludes=["pytest", "pytestqt", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DJDmaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=str(PROJECT_ROOT / "packaging" / "windows_version_info.txt"),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DJDmaker_v0.1",
)
