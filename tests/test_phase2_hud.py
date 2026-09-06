from __future__ import annotations

import inspect
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.gui import app as production_app
from djd_maker.gui.controller import AsyncControllerBridge
from djd_maker.gui.dialogs import JobDetailDialog, LogDialog, PresetDialog, SettingsDialog
from djd_maker.gui.hud import HUD_STYLESHEET, CircularStatusWidget, HudPanel, PipelineStepWidget
from djd_maker.gui.main_window import MainWindow
from djd_maker.gui.viewmodels import LogRecord
from djd_maker.testing.hud_preview import (
    _MemoryJobs,
    _MemoryPresets,
    _MemorySettings,
    _PreviewController,
    _sample_jobs,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def hud_window(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = _app()
    ending = tmp_path / "ending.mp4"
    ending.write_bytes(b"phase2-test")
    settings = AppSettings(
        input_directory=str(tmp_path / "台本 入力"),
        raw_directory=str(tmp_path / "RAW 保管"),
        output_directory=str(tmp_path / "ZIP 出力"),
        ending_video=str(ending),
    )
    jobs = _sample_jobs()
    pool = QThreadPool()
    bridge = AsyncControllerBridge(_PreviewController(), thread_pool=pool)
    window = MainWindow(
        app_root=tmp_path,
        settings_repository=_MemorySettings(settings),
        job_repository=_MemoryJobs(jobs),
        controller=bridge,
        preset_repository=_MemoryPresets(),
    )
    window.show()
    application.processEvents()
    yield window
    window.close()
    pool.waitForDone(1000)


def test_phase2_window_launches_with_static_hud_architecture(hud_window: MainWindow) -> None:
    assert hud_window.windowTitle() == "台本から授業動画つくるマシーン Ver1.1"
    assert hud_window.findChildren(HudPanel)
    assert hud_window.findChildren(CircularStatusWidget)
    assert hud_window.centralWidget().objectName() == "hudRoot"
    assert "#040B12" in hud_window.styleSheet()


def test_phase2_sidebar_retains_all_actions_in_required_order(hud_window: MainWindow) -> None:
    layout = hud_window.sidebar.layout()
    buttons = [
        layout.itemAt(index).widget()
        for index in range(layout.count())
        if isinstance(layout.itemAt(index).widget(), QPushButton)
    ]
    assert buttons == [
        hud_window.settings_button,
        hud_window.login_button,
        hud_window.start_button,
        hud_window.reload_button,
        hud_window.recover_button,
        hud_window.pause_button,
        hud_window.stop_button,
        hud_window.log_button,
        hud_window.details_button,
    ]
    assert all(not button.icon().isNull() for button in buttons)
    assert hud_window.start_button.property("hudRole") == "primary"
    assert hud_window.recover_button.property("hudRole") == "recovery"
    assert hud_window.stop_button.property("hudRole") == "danger"


def test_phase2_credit_and_recovery_are_visible(hud_window: MainWindow) -> None:
    hud_window._apply_runtime_status(
        {
            "credit_state": "CREDIT_EXHAUSTED",
            "credit_percent": 0,
            "credit_reset_at": "2026-09-08T14:30:00+09:00",
        }
    )
    assert "枯渇" in hud_window.credit_state_label.text()
    assert "0%" in hud_window.credit_percent_label.text()
    assert "2026-09-08" in hud_window.credit_reset_label.text()
    assert hud_window.recover_button.text().replace("\n", "").strip()


def test_phase2_job_states_use_text_and_distinct_colors(hud_window: MainWindow) -> None:
    jobs = _sample_jobs()
    hud_window.set_jobs(jobs)
    state_texts = [hud_window.job_table.item(row, 5).text() for row in range(len(jobs))]
    state_colors = {
        hud_window.job_table.item(row, 5).foreground().color().name()
        for row in range(len(jobs))
    }
    assert any("完成" in text for text in state_texts)
    assert any("クレジット" in text for text in state_texts)
    assert any("未回収" in text for text in state_texts)
    assert any("エラー" in text for text in state_texts)
    assert len(state_colors) >= 4
    assert hud_window.job_table.item(0, 7).text() == "14:10"


def test_phase2_process_timeline_has_all_static_steps(hud_window: MainWindow) -> None:
    assert tuple(hud_window.pipeline_steps) == (
        "auth",
        "preflight",
        "notebook",
        "credit",
        "ending",
        "hls",
        "zip",
    )
    assert all(isinstance(step, PipelineStepWidget) for step in hud_window.pipeline_steps.values())


def test_phase2_current_task_uses_real_job_values(hud_window: MainWindow) -> None:
    job = Job("現実値.txt", state=JobState.ENDING, progress_percent=74)
    hud_window.set_jobs([job])
    assert "現実値" in hud_window.current_job_label.text()
    assert "End処理中" in hud_window.current_stage_label.text()
    assert hud_window.progress_bar.value() == 74


def test_phase2_embedded_log_sanitizes_and_colors_levels(hud_window: MainWindow) -> None:
    hud_window._append_log_record(
        {"timestamp": "12:34:56", "level": "WARNING", "message": "token=secret"}
    )
    row = hud_window.execution_log_table.rowCount() - 1
    assert "secret" not in hud_window.execution_log_table.item(row, 2).text()
    assert "REDACTED" in hud_window.execution_log_table.item(row, 2).text()
    assert hud_window.execution_log_table.item(row, 1).foreground().color() != QColor("#EAF8FF")

    hud_window._append_log_record(
        LogRecord("2026-09-07T12:35:57+09:00", level="INFO", message="structured")
    )
    structured_row = hud_window.execution_log_table.rowCount() - 1
    assert hud_window.execution_log_table.item(structured_row, 0).text() == "12:35:57"
    assert hud_window.execution_log_table.item(structured_row, 2).text() == "structured"


def test_phase2_settings_and_preset_keep_all_management_controls() -> None:
    presets = _MemoryPresets()
    settings = SettingsDialog(AppSettings(), preset_repository=presets)
    assert all(
        hasattr(settings, name)
        for name in (
            "input_directory_edit",
            "raw_directory_edit",
            "output_directory_edit",
            "ending_video_edit",
            "first_check_spin",
            "poll_spin",
            "ffmpeg_concurrency_combo",
            "new_preset_button",
            "edit_preset_button",
            "duplicate_preset_button",
            "delete_preset_button",
        )
    )
    preset = PresetDialog(name="A", prompt_text="本文")
    assert preset.name_edit.text() == "A"
    assert preset.prompt_edit.toPlainText() == "本文"
    assert "#040B12" in settings.styleSheet()
    assert "#040B12" in preset.styleSheet()
    settings.close()
    preset.close()


def test_phase2_detail_and_log_dialogs_have_hud_theme_and_credit_fields() -> None:
    job = Job(
        "detail.txt",
        state=JobState.RESERVED_WAITING_CREDIT_RESET,
        credit_state="CREDIT_EXHAUSTED",
        recovery_retry_count=2,
    )
    detail = JobDetailDialog(job)
    rendered_text = " ".join(child.text() for child in detail.findChildren(QLabel))
    assert "CREDIT_EXHAUSTED" in rendered_text
    assert "#040B12" in detail.styleSheet()
    log = LogDialog()
    log.append_record({"level": "ERROR", "message": "retry"})
    assert log.model.rowCount() == 1
    assert "#040B12" in log.styleSheet()
    detail.close()
    log.close()


@pytest.mark.parametrize("size", [(1920, 1080), (1600, 900), (1280, 720)])
def test_phase2_layout_retains_primary_controls_at_supported_sizes(
    hud_window: MainWindow, size: tuple[int, int]
) -> None:
    hud_window.resize(*size)
    QApplication.processEvents()
    assert hud_window.start_button.isVisibleTo(hud_window)
    assert hud_window.recover_button.isVisibleTo(hud_window)
    assert hud_window.job_table.isVisibleTo(hud_window)
    assert hud_window.pipeline_steps["zip"].isVisibleTo(hud_window)
    assert hud_window.width() >= 1180
    assert hud_window.height() >= 700


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_phase2_dpi_equivalent_layout_has_no_fixed_external_font_dependency(scale: float) -> None:
    logical_width = round(1920 / scale)
    logical_height = round(1080 / scale)
    assert logical_width >= 1280
    assert logical_height >= 720
    assert "Yu Gothic UI" in HUD_STYLESHEET
    assert "url(" not in HUD_STYLESHEET.casefold()


def test_phase2_preview_is_not_imported_by_production_startup() -> None:
    source = inspect.getsource(production_app)
    assert "djd_maker.testing" not in source
    assert "hud_preview" not in source


def test_phase2_contains_no_timer_or_animation_runtime() -> None:
    import djd_maker.gui.hud as hud
    import djd_maker.gui.main_window as main_window

    source = inspect.getsource(hud) + inspect.getsource(main_window)
    assert "QPropertyAnimation" not in source
    assert "QVariantAnimation" not in source
    assert "QTimer" not in source
    assert "startTimer" not in source
