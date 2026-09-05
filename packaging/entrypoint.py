from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path


def _settings_smoke(mode: str, report_path: Path, expected_value: int = 137) -> int:
    """Exercise packaged JSON persistence only when the build verifier opts in."""
    if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
        return 3
    from djd_maker.core.repositories import SettingsRepository
    from djd_maker.packaging.preflight import application_root

    repository = SettingsRepository(application_root() / "system" / "settings.json")
    if mode == "write":
        saved = replace(repository.load(), notebook_poll_seconds=expected_value)
        repository.save(saved)
        passed = repository.load().notebook_poll_seconds == expected_value
    elif mode == "read":
        passed = repository.load().notebook_poll_seconds == expected_value
    else:
        return 4
    report_path.write_text(
        json.dumps(
            {"mode": mode, "passed": passed, "expected_value": expected_value},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0 if passed else 5


def _browser_smoke(report_path: Path) -> int:
    if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
        return 3
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.packaging.preflight import application_root

    manager = BrowserManager(
        application_root() / "browser" / "chrome-profile",
        headless=True,
    )
    try:
        page = manager.start()
        page.set_content("<title>DJD portable browser smoke</title><p>ok</p>")
        passed = page.title() == "DJD portable browser smoke"
    finally:
        manager.stop()
    report_path.write_text(
        json.dumps({"passed": passed, "title": "DJD portable browser smoke"}),
        encoding="utf-8",
    )
    return 0 if passed else 7


def _dispatch() -> int:
    if len(sys.argv) in {4, 5} and sys.argv[1] == "--packaging-settings-smoke":
        expected_value = int(sys.argv[4]) if len(sys.argv) == 5 else 137
        return _settings_smoke(sys.argv[2], Path(sys.argv[3]), expected_value)
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-fake-e2e":
        if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
            return 3
        from djd_maker.packaging.portable_e2e import run_portable_fake_e2e

        return run_portable_fake_e2e(Path(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-browser-smoke":
        return _browser_smoke(Path(sys.argv[2]))
    from djd_maker.gui.app import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_dispatch())
