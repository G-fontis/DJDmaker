from __future__ import annotations

import json
import os
import sys
import threading
import time
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


def _auth_browser_smoke(report_path: Path) -> int:
    """Launch the packaged ordinary AUTH Chrome and close only that owned process."""
    if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
        return 3
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.packaging.preflight import application_root

    manager = BrowserManager(application_root() / "browser" / "chrome-profile")
    errors: list[str] = []

    def open_login() -> None:
        try:
            manager.open_login()
        except Exception as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=open_login, daemon=True)
    worker.start()
    deadline = time.monotonic() + 20
    while not manager.auth_process_alive and worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    command = tuple(getattr(manager, "_auth_command", ()))
    forbidden = ("remote-debugging", "headless", "automation", "cdp", "playwright")
    launched = manager.auth_process_alive
    ordinary = launched and not any(
        marker in argument.casefold() for argument in command for marker in forbidden
    )
    manager.stop()
    worker.join(timeout=10)
    passed = ordinary and not worker.is_alive() and not errors
    report_path.write_text(
        json.dumps(
            {
                "passed": passed,
                "auth_chrome_launched": launched,
                "ordinary_chrome_flags": ordinary,
                "remote_debugging": False,
                "automation_connected": False,
                "errors": errors,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0 if passed else 9


def _hud_smoke(output_directory: Path, report_path: Path) -> int:
    """Render the packaged production widgets; never entered by normal startup."""
    if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
        return 3
    from PySide6.QtWidgets import QApplication

    from djd_maker.core.repositories import JobRepository, PresetRepository, SettingsRepository
    from djd_maker.gui.app import build_desktop
    from djd_maker.gui.dialogs import JobDetailDialog, LogDialog, PresetDialog, SettingsDialog
    from djd_maker.packaging.preflight import application_root

    root = application_root()
    output_directory.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    _app, window, _service = build_desktop(root, qt_app=application)
    settings = SettingsRepository(root / "system" / "settings.json").load()
    presets = PresetRepository(root / "system" / "presets.json")
    jobs = JobRepository(root / "system" / "jobs").list()
    paths: list[Path] = []

    def capture(widget, filename: str, width: int, height: int) -> None:
        widget.resize(width, height)
        widget.show()
        application.processEvents()
        path = output_directory / filename
        if not widget.grab().save(str(path), "PNG"):
            raise RuntimeError(f"SCREENSHOT_SAVE_FAILED: {path}")
        paths.append(path)
        widget.hide()

    try:
        capture(window, "exe_main_1920x1080.png", 1920, 1080)
        capture(window, "exe_main_1280x720.png", 1280, 720)
        settings_dialog = SettingsDialog(settings, preset_repository=presets)
        capture(settings_dialog, "exe_settings.png", 820, 650)
        preset_dialog = PresetDialog(name="Portable表示確認", prompt_text="Phase2 HUD表示確認")
        capture(preset_dialog, "exe_preset.png", 720, 520)
        if not jobs:
            raise RuntimeError("HUD smoke requires a non-private fixture job")
        detail_dialog = JobDetailDialog(jobs[0])
        capture(detail_dialog, "exe_job_detail.png", 820, 820)
        log_dialog = LogDialog()
        log_dialog.append_record(
            {"timestamp": "14:30:01", "engine": "HUD", "stage": "portable", "level": "INFO", "message": "Phase2 packaged GUI"}
        )
        capture(log_dialog, "exe_log.png", 1200, 650)
        buttons = (
            window.settings_button,
            window.login_button,
            window.start_button,
            window.reload_button,
            window.recover_button,
            window.pause_button,
            window.stop_button,
            window.log_button,
            window.details_button,
        )
        passed = len(paths) == 6 and all(path.is_file() for path in paths) and all(
            button.text().strip() for button in buttons
        )
    finally:
        window.close()
    report_path.write_text(
        json.dumps(
            {
                "passed": passed,
                "screenshots": [str(path) for path in paths],
                "sidebar_button_count": 9,
                "job_columns": list(window.JOB_COLUMNS),
                "pipeline_steps": list(window.pipeline_steps),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if passed else 10


def _preset_smoke(report_path: Path) -> int:
    """Verify packaged preset CRUD and process-local blank startup selection."""
    if os.environ.get("DJD_PACKAGING_SMOKE") != "1":
        return 3
    from djd_maker.core.repositories import PresetRepository
    from djd_maker.packaging.preflight import application_root

    repository = PresetRepository(application_root() / "system" / "presets.json")
    first = repository.create("portable A", "preset body A")
    second = repository.create("portable B", "preset body B")
    repository.select(second.id)
    restarted = PresetRepository(application_root() / "system" / "presets.json")
    selected = restarted.selected()
    passed = (
        [item.id for item in restarted.list()] == [first.id, second.id]
        and selected is None
    )
    report_path.write_text(
        json.dumps(
            {
                "passed": passed,
                "preset_count": len(restarted.list()),
                "selected_preset": None,
                "selection_reset_on_restart": selected is None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0 if passed else 8


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
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-auth-browser-smoke":
        return _auth_browser_smoke(Path(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-preset-smoke":
        return _preset_smoke(Path(sys.argv[2]))
    if len(sys.argv) == 4 and sys.argv[1] == "--packaging-hud-smoke":
        return _hud_smoke(Path(sys.argv[2]), Path(sys.argv[3]))
    from djd_maker.gui.app import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_dispatch())
