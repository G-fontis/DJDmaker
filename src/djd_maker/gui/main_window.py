from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings

from .controller import AsyncControllerBridge
from .dialogs import JobDetailDialog, LogDialog, SettingsDialog, open_local_path
from .preview import EndingPreviewPlayer
from .viewmodels import ACTIVE_STATES, job_stage_texts, state_display, summarize_jobs


class SettingsRepositoryPort(Protocol):
    def load(self) -> AppSettings: ...

    def save(self, settings: AppSettings) -> None: ...


class JobRepositoryPort(Protocol):
    def list(self) -> list[Job]: ...


class MainWindow(QMainWindow):
    APPLICATION_NAME = "台本から授業動画つくるマシーン v0.1.1"
    ENGINE_CAPTION = "GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成"
    CREDIT = "Created by 福ゼミ塾長"
    JOB_COLUMNS = ("No", "台本名", "Notebook", "End処理", "HLS/ZIP", "状態")

    def __init__(
        self,
        *,
        app_root: Path,
        settings_repository: SettingsRepositoryPort,
        job_repository: JobRepositoryPort,
        controller: AsyncControllerBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_root = app_root.resolve()
        self.settings_repository = settings_repository
        self.job_repository = job_repository
        self.controller = controller
        self.settings = self.settings_repository.load()
        self.jobs: list[Job] = []
        self._running = False
        self._log_dialog = LogDialog(self)
        self._preview_player = EndingPreviewPlayer(
            lambda path: open_local_path(path, parent=self), self
        )
        self.setWindowTitle(self.APPLICATION_NAME)
        self.setMinimumSize(1000, 680)
        self._build_ui()
        self._connect_controller()
        self.apply_settings(self.settings)
        self.reload_jobs(local_only=True)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        title = QLabel(self.APPLICATION_NAME)
        title.setObjectName("applicationTitle")
        font = title.font()
        font.setPointSize(font.pointSize() + 5)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        engine = QLabel(self.ENGINE_CAPTION)
        engine.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(engine)
        credit = QLabel(self.CREDIT)
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(credit)

        paths = QGroupBox("パス設定")
        grid = QGridLayout(paths)
        self.input_path_edit, self.open_input_button = self._path_row(grid, 0, "台本フォルダー")
        self.raw_path_edit, self.open_raw_folder_button = self._path_row(grid, 1, "RAW保存先")
        self.output_path_edit, self.open_output_button = self._path_row(grid, 2, "ZIP出力先")
        self.ending_path_edit = QLineEdit()
        self.ending_path_edit.setReadOnly(True)
        self.change_ending_button = QPushButton("Ending変更…")
        self.preview_ending_button = QPushButton("Ending確認")
        grid.addWidget(QLabel("Ending動画"), 3, 0)
        grid.addWidget(self.ending_path_edit, 3, 1)
        grid.addWidget(self.change_ending_button, 3, 2)
        grid.addWidget(self.preview_ending_button, 3, 3)
        root.addWidget(paths)

        controls = QHBoxLayout()
        self.reload_button = QPushButton("台本再読込")
        self.start_button = QPushButton("授業動画作成開始")
        self.pause_button = QPushButton("一時停止")
        self.stop_button = QPushButton("停止")
        self.login_button = QPushButton("Googleログイン")
        self.details_button = QPushButton("ジョブ詳細")
        self.log_button = QPushButton("ログを見る")
        self.settings_button = QPushButton("設定")
        for button in (
            self.settings_button,
            self.login_button,
            self.start_button,
            self.reload_button,
            self.pause_button,
            self.stop_button,
            self.log_button,
            self.details_button,
        ):
            controls.addWidget(button)
        root.addLayout(controls)

        summary = QGroupBox("状態集計")
        summary_layout = QHBoxLayout(summary)
        self.total_label = QLabel()
        self.current_job_label = QLabel("現在ジョブ: －")
        self.current_stage_label = QLabel("現在工程: －")
        self.progress_label = QLabel("進捗率: 0%")
        self.active_label = QLabel()
        self.notebook_complete_label = QLabel()
        self.zip_complete_label = QLabel()
        self.error_label = QLabel()
        self.next_check_label = QLabel("次回確認: －")
        for label in (
            self.total_label,
            self.current_job_label,
            self.current_stage_label,
            self.progress_label,
            self.active_label,
            self.notebook_complete_label,
            self.zip_complete_label,
            self.error_label,
            self.next_check_label,
        ):
            summary_layout.addWidget(label)
        root.addWidget(summary)

        self.job_table = QTableWidget(0, len(self.JOB_COLUMNS))
        self.job_table.setHorizontalHeaderLabels(self.JOB_COLUMNS)
        self.job_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.job_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.job_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.job_table.verticalHeader().setVisible(False)
        header = self.job_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.job_table, 1)

        self.completion_group = QGroupBox("授業作成 完了")
        completion_layout = QHBoxLayout(self.completion_group)
        self.completion_label = QLabel()
        self.completion_output_button = QPushButton("完成ZIPフォルダ")
        self.completion_raw_button = QPushButton("RAWフォルダ")
        self.completion_error_button = QPushButton("エラー確認")
        completion_layout.addWidget(self.completion_label, 1)
        completion_layout.addWidget(self.completion_output_button)
        completion_layout.addWidget(self.completion_raw_button)
        completion_layout.addWidget(self.completion_error_button)
        self.completion_group.hide()
        root.addWidget(self.completion_group)
        self.statusBar().showMessage("待機中")

        self.open_input_button.clicked.connect(lambda: self._open_directory(self.input_path_edit.text()))
        self.open_raw_folder_button.clicked.connect(lambda: self._open_directory(self.raw_path_edit.text()))
        self.open_output_button.clicked.connect(lambda: self._open_directory(self.output_path_edit.text()))
        self.change_ending_button.clicked.connect(self.change_ending)
        self.preview_ending_button.clicked.connect(self.preview_ending)
        self.reload_button.clicked.connect(self.reload_jobs)
        self.start_button.clicked.connect(self.start_processing)
        self.pause_button.clicked.connect(self.pause_processing)
        self.stop_button.clicked.connect(self.stop_processing)
        self.login_button.clicked.connect(self.controller.login)
        self.details_button.clicked.connect(self.show_selected_job)
        self.log_button.clicked.connect(self.show_logs)
        self.settings_button.clicked.connect(self.show_settings)
        self.completion_output_button.clicked.connect(
            lambda: self._open_directory(self.output_path_edit.text())
        )
        self.completion_raw_button.clicked.connect(
            lambda: self._open_directory(self.raw_path_edit.text())
        )
        self.completion_error_button.clicked.connect(self.show_logs)
        self.job_table.itemSelectionChanged.connect(self._update_action_state)
        self.job_table.itemDoubleClicked.connect(lambda _item: self.show_selected_job())

    @staticmethod
    def _path_row(layout: QGridLayout, row: int, label: str) -> tuple[QLineEdit, QPushButton]:
        edit = QLineEdit()
        edit.setReadOnly(True)
        button = QPushButton("開く")
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(edit, row, 1, 1, 2)
        layout.addWidget(button, row, 3)
        return edit, button

    def _connect_controller(self) -> None:
        self.controller.jobs_changed.connect(self.set_jobs)
        self.controller.status_changed.connect(self._apply_runtime_status)
        self.controller.log_received.connect(self._log_dialog.append_record)
        self.controller.operation_started.connect(self._operation_started)
        self.controller.operation_finished.connect(self._operation_finished)
        self.controller.operation_failed.connect(self._operation_failed)

    def _resolve_setting_path(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.app_root / path).resolve()

    def apply_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.input_path_edit.setText(str(self._resolve_setting_path(settings.input_directory)))
        self.raw_path_edit.setText(str(self._resolve_setting_path(settings.raw_directory)))
        self.output_path_edit.setText(str(self._resolve_setting_path(settings.output_directory)))
        self.ending_path_edit.setText(
            str(self._resolve_setting_path(settings.ending_video)) if settings.ending_video else ""
        )
        self.preview_ending_button.setEnabled(self._ending_path() is not None)
        self._update_action_state()

    def _ending_path(self) -> Path | None:
        if not self.settings.ending_video:
            return None
        path = self._resolve_setting_path(self.settings.ending_video)
        return path if path.is_file() else None

    def _open_directory(self, value: str) -> None:
        open_local_path(Path(value), parent=self)

    def change_ending(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Ending動画を選択",
            self.ending_path_edit.text(),
            "動画 (*.mp4 *.mov *.mkv *.webm);;すべて (*)",
        )
        if not selected:
            return
        updated = replace(self.settings, ending_video=selected)
        try:
            self.settings_repository.save(updated)
        except Exception as exc:
            QMessageBox.critical(self, "設定保存エラー", str(exc))
            return
        self.apply_settings(updated)

    def preview_ending(self) -> None:
        ending = self._ending_path()
        if ending is None:
            QMessageBox.warning(self, "Ending未設定", "有効なEnding動画を設定してください。")
            return
        self._preview_player.play(ending)

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if not dialog.exec():
            return
        updated = dialog.value()
        try:
            self.settings_repository.save(updated)
        except Exception as exc:
            QMessageBox.critical(self, "設定保存エラー", str(exc))
            return
        self.apply_settings(updated)

    def reload_jobs(self, _checked: bool = False, *, local_only: bool = False) -> None:
        try:
            self.set_jobs(self.job_repository.list())
        except Exception as exc:
            QMessageBox.critical(self, "ジョブ読込エラー", str(exc))
            return
        if not local_only:
            self.controller.reload()

    def set_jobs(self, jobs: object) -> None:
        if not isinstance(jobs, (list, tuple)) or not all(isinstance(job, Job) for job in jobs):
            return
        self.jobs = list(jobs)
        self.job_table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            notebook, ending, hls = job_stage_texts(job)
            values = (str(row + 1), job.script_name, notebook, ending, hls, state_display(job))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.job_table.setItem(row, column, item)
        summary = summarize_jobs(self.jobs)
        self.total_label.setText(f"全Job: {summary.total}")
        self.active_label.setText(f"処理中: {summary.active}")
        self.notebook_complete_label.setText(f"Notebook完了: {summary.notebook_complete}/{summary.total}")
        self.zip_complete_label.setText(f"ZIP完了: {summary.zip_complete}/{summary.total}")
        self.error_label.setText(f"Error: {summary.errors}")
        current = next((job for job in self.jobs if job.state in ACTIVE_STATES), None)
        self.current_job_label.setText(
            f"現在ジョブ: {current.script_name if current else '－'}"
        )
        self.current_stage_label.setText(
            f"現在工程: {state_display(current) if current else '－'}"
        )
        self.progress_label.setText(
            f"進捗率: {max(0.0, min(100.0, current.progress_percent if current else 0.0)):.0f}%"
        )
        all_finished = bool(self.jobs) and all(
            job.state in {JobState.COMPLETED, JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}
            for job in self.jobs
        )
        raw_count = sum(bool(job.raw_path) for job in self.jobs)
        self.completion_label.setText(
            f"全 {summary.total}授業 / 完成 {summary.zip_complete} / "
            f"エラー {summary.errors} / RAW {raw_count}本 / 完成ZIP {summary.zip_complete}本"
        )
        self.completion_group.setVisible(all_finished)
        self._update_action_state()

    def _selected_job(self) -> Job | None:
        row = self.job_table.currentRow()
        if row < 0 or row >= len(self.jobs):
            return None
        return self.jobs[row]

    def show_selected_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        dialog = JobDetailDialog(job, self)
        dialog.retry_requested.connect(self.controller.retry)
        dialog.exec()

    def show_logs(self) -> None:
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def start_processing(self) -> None:
        if self._ending_path() is None:
            QMessageBox.warning(
                self,
                "Ending未設定",
                "授業動画作成を開始する前に、有効なEnding動画を設定してください。",
            )
            return
        if self.controller.start():
            self._running = True
            self.statusBar().showMessage("開始要求を送信しました")
            self._update_action_state()

    def pause_processing(self) -> None:
        if self._running:
            self.controller.pause()
            self.statusBar().showMessage("安全な工程境界で一時停止します")

    def stop_processing(self) -> None:
        if self._running:
            self.controller.stop()
            self.statusBar().showMessage("安全な停止を要求しました。実行中工程の終了を待っています")

    def _operation_started(self, operation: str) -> None:
        self.statusBar().showMessage(f"{operation} 実行中")

    def _operation_finished(self, operation: str, _result: object) -> None:
        if operation in {"stop", "pause"}:
            self._running = False
        self.statusBar().showMessage(f"{operation} 完了")
        self._update_action_state()
        if operation != "reload":
            self.reload_jobs(local_only=True)

    def _operation_failed(self, operation: str, message: str) -> None:
        if operation == "start":
            self._running = False
        self.statusBar().showMessage(f"{operation} 失敗")
        self._log_dialog.append_record({"level": "ERROR", "stage": operation, "message": message})
        QMessageBox.critical(self, "処理エラー", f"{operation}: {message}")
        self._update_action_state()

    def _apply_runtime_status(self, status: object) -> None:
        if isinstance(status, dict):
            self._running = bool(status.get("running", self._running))
            next_check = status.get("next_check", "－")
            self.next_check_label.setText(f"次回確認: {next_check}")
            self._update_action_state()

    def _update_action_state(self) -> None:
        self.start_button.setEnabled(not self._running and self._ending_path() is not None)
        self.pause_button.setEnabled(self._running)
        self.stop_button.setEnabled(self._running)
        self.login_button.setEnabled(not self._running)
        self.details_button.setEnabled(self._selected_job() is not None)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._preview_player.stop()
        self.setEnabled(False)
        if self.controller.shutdown(timeout_ms=5000):
            event.accept()
        else:
            self.setEnabled(True)
            QMessageBox.warning(
                self,
                "終了待機中",
                "バックグラウンド処理が安全に停止していません。少し待ってから再度終了してください。",
            )
            event.ignore()
