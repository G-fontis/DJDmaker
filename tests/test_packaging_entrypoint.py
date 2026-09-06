from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys


ENTRYPOINT_PATH = Path(__file__).resolve().parents[1] / "packaging" / "entrypoint.py"
SPEC = importlib.util.spec_from_file_location("djd_packaging_entrypoint", ENTRYPOINT_PATH)
assert SPEC is not None and SPEC.loader is not None
ENTRYPOINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENTRYPOINT)


def test_settings_smoke_can_change_and_read_expected_value(
    tmp_path, monkeypatch
) -> None:
    from djd_maker.packaging import preflight

    monkeypatch.setenv("DJD_PACKAGING_SMOKE", "1")
    monkeypatch.setattr(preflight, "application_root", lambda: tmp_path)
    write_report = tmp_path / "write.json"
    read_report = tmp_path / "read.json"

    assert ENTRYPOINT._settings_smoke("write", write_report, 241) == 0
    assert ENTRYPOINT._settings_smoke("read", read_report, 241) == 0
    assert json.loads(write_report.read_text(encoding="utf-8")) == {
        "mode": "write",
        "passed": True,
        "expected_value": 241,
    }
    assert json.loads(read_report.read_text(encoding="utf-8"))["passed"] is True


def test_preset_smoke_starts_unselected_after_repository_restart(
    tmp_path, monkeypatch
) -> None:
    from djd_maker.packaging import preflight

    monkeypatch.setenv("DJD_PACKAGING_SMOKE", "1")
    monkeypatch.setattr(preflight, "application_root", lambda: tmp_path)
    report = tmp_path / "preset-report.json"
    assert ENTRYPOINT._preset_smoke(report) == 0
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "passed": True,
        "preset_count": 2,
        "selected_preset": None,
        "selection_reset_on_restart": True,
    }


def test_hud_smoke_dispatch_is_gated_and_uses_two_paths(tmp_path, monkeypatch) -> None:
    output = tmp_path / "screens"
    report = tmp_path / "report.json"
    monkeypatch.delenv("DJD_PACKAGING_SMOKE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["DJDmaker.exe", "--packaging-hud-smoke", str(output), str(report)],
    )
    assert ENTRYPOINT._dispatch() == 3
    assert not output.exists()
    assert not report.exists()


def test_auth_browser_smoke_is_gated(tmp_path, monkeypatch) -> None:
    report = tmp_path / "auth.json"
    monkeypatch.delenv("DJD_PACKAGING_SMOKE", raising=False)
    assert ENTRYPOINT._auth_browser_smoke(report) == 3
    assert not report.exists()
