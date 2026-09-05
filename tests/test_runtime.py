from __future__ import annotations

import subprocess
from pathlib import Path

from djd_maker.runtime import RuntimeReport, _tool_check, inspect_runtime


def test_tool_check_reports_command_version(monkeypatch) -> None:
    monkeypatch.setattr("djd_maker.runtime.shutil.which", lambda name: f"C:/{name}.exe")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "ffmpeg version test\n", "")

    result = _tool_check("ffmpeg", runner)

    assert result.passed
    assert result.detail == "ffmpeg version test"


def test_runtime_report_aggregates_failures() -> None:
    from djd_maker.runtime import RuntimeCheck

    assert RuntimeReport((RuntimeCheck("ok", True, ""),)).passed
    assert not RuntimeReport((RuntimeCheck("bad", False, ""),)).passed


def test_runtime_inspection_checks_unicode_path(tmp_path: Path) -> None:
    report = inspect_runtime(tmp_path)
    by_name = {check.name: check for check in report.checks}

    assert by_name["python"].passed
    assert by_name["pyside6"].passed
    assert by_name["qt_plugins"].passed
    assert by_name["qt_multimedia"].passed
    assert by_name["ffmpeg"].passed
    assert by_name["ffprobe"].passed
    assert by_name["windows_shell_open"].passed
    assert by_name["unicode_path"].passed
    assert not (tmp_path / "日本語パス確認").exists()
