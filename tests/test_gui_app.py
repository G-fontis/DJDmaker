from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from djd_maker.gui.app import build_desktop


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


def test_real_composition_opens_gui_before_browser_or_ending_configuration(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    browser = BrowserMustStayLazy()
    _app, window, service = build_desktop(
        tmp_path, qt_app=application, browser_manager=browser  # type: ignore[arg-type]
    )

    assert window.windowTitle() == "台本から授業動画つくるマシーン v0.1.3"
    assert not browser.started
    assert not window.start_button.isEnabled()
    window.close()
    assert not service.status()["running"]
    assert browser.stopped
