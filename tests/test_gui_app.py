from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from djd_maker.gui.app import build_desktop
from djd_maker.core.repositories import SettingsRepository
from djd_maker.core.settings import AppSettings


class BrowserMustStayLazy:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        raise AssertionError("browser must not start while only opening the GUI")

    def stop(self):
        self.stopped = True

    def open_login(self):
        raise AssertionError("login must stay lazy while only opening GUI")

    def runtime_status(self):
        return {}

    def prepare_for_processing(self):
        raise AssertionError("browser preflight must not run without a preset")


def test_real_composition_opens_gui_before_browser_or_ending_configuration(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    browser = BrowserMustStayLazy()
    _app, window, service = build_desktop(
        tmp_path, qt_app=application, browser_manager=browser  # type: ignore[arg-type]
    )

    assert window.windowTitle() == "台本から授業動画つくるマシーン Ver1.0"
    assert not browser.started
    assert not window.start_button.isEnabled()
    window.close()
    assert not service.status()["running"]
    assert browser.stopped


def test_missing_preset_preflight_stops_before_browser_and_notebook(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"ending")
    SettingsRepository(tmp_path / "system" / "settings.json").save(
        AppSettings(ending_video=str(ending))
    )
    browser = BrowserMustStayLazy()
    _app, window, service = build_desktop(
        tmp_path, qt_app=application, browser_manager=browser  # type: ignore[arg-type]
    )
    errors = []
    service.bind(
        jobs=lambda _value: None,
        status=lambda _value: None,
        log=lambda _value: None,
        error=lambda operation, message: errors.append((operation, message)),
    )
    service.start()
    deadline = time.monotonic() + 2
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    assert errors == [("startup", "動画生成プリセットを登録・選択してください。")]
    assert not browser.started
    window.close()
