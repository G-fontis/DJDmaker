from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import shutil
from typing import Protocol

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QCloseEvent, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.settings import AppSettings

from .controller import AsyncControllerBridge
from .dialogs import JobDetailDialog, LogDialog, SettingsDialog, open_local_path
from .hud import (
    HUD_STYLESHEET,
    PALETTE,
    CircularStatusWidget,
    HudBackground,
    HudHeader,
    HudPanel,
    PipelineStepWidget,
    sidebar_button,
)
from .preview import EndingPreviewPlayer
from .viewmodels import (
    ACTIVE_STATES,
    LogRecord,
    job_stage_texts,
    sanitize_log_text,
    state_display,
    summarize_jobs,
)


class SettingsRepositoryPort(Protocol):
    def load(self) -> AppSettings: ...

    def save(self, settings: AppSettings) -> None: ...


class JobRepositoryPort(Protocol):
    def list(self) -> list[Job]: ...


class PresetRepositoryPort(Protocol):
    def list(self) -> list[Preset]: ...

    def selected(self) -> Preset | None: ...


class MainWindow(QMainWindow):
    APPLICATION_NAME = "台本から授業動画つくるマシーン Ver1.1"
    ENGINE_CAPTION = "GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成"
    CREDIT = "Created by 福ゼミ塾長"
    JOB_COLUMNS = ("No", "台本名", "Notebook", "End処理", "HLS/ZIP", "状態", "進捗", "開始時刻")

    def __init__(
        self,
        *,
        app_root: Path,
        settings_repository: SettingsRepositoryPort,
        job_repository: JobRepositoryPort,
        controller: AsyncControllerBridge,
        preset_repository: PresetRepositoryPort | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_root = app_root.resolve()
        self.settings_repository = settings_repository
        self.job_repository = job_repository
        self.controller = controller
        self.preset_repository = preset_repository
        self.settings = self.settings_repository.load()
        self.jobs: list[Job] = []
        self._running = False
        self._log_dialog = LogDialog(self)
        self._preview_player = EndingPreviewPlayer(
            lambda path: open_local_path(path, parent=self), self
        )
        self.setWindowTitle(self.APPLICATION_NAME)
        self.setMinimumSize(1180, 700)
        self.resize(1600, 900)
        self.setStyleSheet(HUD_STYLESHEET)
        self._build_ui()
        self._connect_controller()
        self.apply_settings(self.settings)
        self.reload_jobs(local_only=True)
        service = getattr(self.controller, "controller", None)
        refresh_credit = getattr(self.controller, "refresh_credit", None)
        if callable(getattr(service, "refresh_credit", None)) and callable(refresh_credit):
            refresh_credit()

    def _build_ui(self) -> None:
        central = HudBackground()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(8)
        self.header = HudHeader(
            self.APPLICATION_NAME,
            self.ENGINE_CAPTION,
            self.CREDIT,
        )
        root.addWidget(self.header)

        dashboard = QHBoxLayout()
        dashboard.setSpacing(8)
        root.addLayout(dashboard, 1)
        self.sidebar = self._build_sidebar()
        self.center_column = self._build_center()
        self.status_column = self._build_status_column()
        dashboard.addWidget(self.sidebar)
        dashboard.addWidget(self.center_column, 1)
        dashboard.addWidget(self.status_column)

        self.statusBar().showMessage("SYSTEM READY / 待機中")
        self._connect_local_controls()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("hudSidebar")
        sidebar.setMinimumWidth(205)
        sidebar.setMaximumWidth(238)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(11, 12, 11, 12)
        layout.setSpacing(9)
        title = QLabel("ACTION CONSOLE")
        title.setObjectName("panelEyebrow")
        layout.addWidget(title)

        self.settings_button = sidebar_button("  設定")
        self.login_button = sidebar_button("  Googleログイン")
        self.start_button = sidebar_button("  授業動画作成開始", role="primary")
        self.reload_button = sidebar_button("  台本再読込")
        self.recover_button = sidebar_button(
            "  未回収動画の\n  チェックから続ける", role="recovery"
        )
        self.pause_button = sidebar_button("  一時停止")
        self.stop_button = sidebar_button("  停止", role="danger")
        self.log_button = sidebar_button("  ログを見る")
        self.details_button = sidebar_button("  ジョブ詳細")
        standard_icons = (
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            QStyle.StandardPixmap.SP_DialogApplyButton,
            QStyle.StandardPixmap.SP_MediaPlay,
            QStyle.StandardPixmap.SP_BrowserReload,
            QStyle.StandardPixmap.SP_BrowserReload,
            QStyle.StandardPixmap.SP_MediaPause,
            QStyle.StandardPixmap.SP_MediaStop,
            QStyle.StandardPixmap.SP_FileDialogContentsView,
            QStyle.StandardPixmap.SP_FileDialogInfoView,
        )
        for button, icon in zip(
            (
                self.settings_button,
                self.login_button,
                self.start_button,
                self.reload_button,
                self.recover_button,
                self.pause_button,
                self.stop_button,
                self.log_button,
                self.details_button,
            ),
            standard_icons,
            strict=True,
        ):
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(22, 22))
            layout.addWidget(button)
        layout.addStretch(1)
        footer = QLabel("EDUCATION × TECHNOLOGY\nSTATIC HUD / PHASE 2")
        footer.setObjectName("creatorCaption")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        return sidebar

    def _build_center(self) -> QWidget:
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        jobs_panel = HudPanel("ジョブ一覧", eyebrow="MISSION QUEUE")
        self.job_total_badge = QLabel("TOTAL 0")
        self.job_total_badge.setObjectName("panelEyebrow")
        jobs_panel.body.insertWidget(1, self.job_total_badge)
        self.job_table = QTableWidget(0, len(self.JOB_COLUMNS))
        self.job_table.setObjectName("jobTable")
        self.job_table.setHorizontalHeaderLabels(self.JOB_COLUMNS)
        self.job_table.setAlternatingRowColors(True)
        self.job_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.job_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.job_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.job_table.verticalHeader().setVisible(False)
        self.job_table.verticalHeader().setDefaultSectionSize(34)
        header = self.job_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column in (0, 6, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        jobs_panel.body.addWidget(self.job_table, 1)
        layout.addWidget(jobs_panel, 5)

        timeline_panel = HudPanel("処理ステップ", eyebrow="CURRENT JOB")
        timeline_row = QHBoxLayout()
        timeline_row.setSpacing(2)
        self.pipeline_steps: dict[str, PipelineStepWidget] = {}
        for key, label in (
            ("auth", "Google認証"),
            ("preflight", "Pre-flight"),
            ("notebook", "Notebook"),
            ("credit", "Credit/予約"),
            ("ending", "End処理"),
            ("hls", "HLS"),
            ("zip", "ZIP"),
        ):
            step = PipelineStepWidget(label, key)
            self.pipeline_steps[key] = step
            timeline_row.addWidget(step, 1)
            if key != "zip":
                connector = QLabel("━━")
                connector.setStyleSheet("color: #17617A; font-size: 11pt;")
                connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                timeline_row.addWidget(connector)
        timeline_panel.body.addLayout(timeline_row)
        layout.addWidget(timeline_panel, 2)

        log_panel = HudPanel("実行ログ", eyebrow="LIVE FEED")
        self.execution_log_table = QTableWidget(0, 3)
        self.execution_log_table.setObjectName("executionLogTable")
        self.execution_log_table.setHorizontalHeaderLabels(("時刻", "LEVEL", "MESSAGE"))
        self.execution_log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.execution_log_table.verticalHeader().setVisible(False)
        self.execution_log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.execution_log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.execution_log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        log_panel.body.addWidget(self.execution_log_table)
        layout.addWidget(log_panel, 3)

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
        layout.addWidget(self.completion_group)
        return center

    def _build_status_column(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(278)
        scroll.setMaximumWidth(326)
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        metrics = HudPanel("システムステータス", eyebrow="OVERVIEW")
        metric_row = QHBoxLayout()
        self.total_metric = CircularStatusWidget("総ジョブ", PALETTE["cyan"])
        self.complete_metric = CircularStatusWidget("完了", PALETTE["success"])
        self.error_metric = CircularStatusWidget("エラー", PALETTE["error"])
        self.active_metric = CircularStatusWidget("進行中", PALETTE["blue"])
        for metric in (self.total_metric, self.complete_metric, self.error_metric, self.active_metric):
            metric_row.addWidget(metric, 1)
        metrics.body.addLayout(metric_row)
        layout.addWidget(metrics)

        current = HudPanel("現在のタスク", eyebrow="ACTIVE MISSION")
        self.current_job_label = QLabel("－")
        self.current_job_label.setObjectName("currentTaskName")
        self.current_stage_label = QLabel("待機中")
        self.current_stage_label.setObjectName("currentTaskStage")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("進捗率: 0%")
        self.progress_label.setObjectName("secondaryText")
        self.next_check_label = QLabel("次回確認: －")
        self.next_check_label.setObjectName("secondaryText")
        current.body.addWidget(self.current_job_label)
        current.body.addWidget(self.current_stage_label)
        current.body.addWidget(self.progress_bar)
        current.body.addWidget(self.progress_label)
        current.body.addWidget(self.next_check_label)
        layout.addWidget(current)

        auth = HudPanel("ブラウザ / 認証状態", eyebrow="SECURE HANDOFF")
        self.browser_status_label = QLabel("● 認証状態: 未確認")
        self.browser_status_label.setObjectName("currentTaskStage")
        self.browser_detail_label = QLabel("専用profile / 個人情報は表示しません")
        self.browser_detail_label.setObjectName("secondaryText")
        self.browser_detail_label.setWordWrap(True)
        auth.body.addWidget(self.browser_status_label)
        auth.body.addWidget(self.browser_detail_label)
        layout.addWidget(auth)

        credit = HudPanel("クレジット", eyebrow="NOTEBOOK QUOTA")
        credit.setAccent(PALETTE["credit_waiting"])
        self.credit_state_label = QLabel("取得不可")
        self.credit_state_label.setObjectName("creditStateValue")
        self.credit_percent_label = QLabel("クレジット残量: 取得不可")
        self.credit_reset_label = QLabel("リセット予定: －")
        self.reserved_count_label = QLabel("予約待ち: 0")
        for label in (self.credit_state_label, self.credit_percent_label, self.credit_reset_label, self.reserved_count_label):
            credit.body.addWidget(label)
        layout.addWidget(credit)

        storage = HudPanel("出力先 / ストレージ", eyebrow="LOCAL SAFE STORE")
        storage_grid = QGridLayout()
        self.input_path_edit, self.open_input_button = self._path_row(storage_grid, 0, "台本")
        self.raw_path_edit, self.open_raw_folder_button = self._path_row(storage_grid, 1, "RAW")
        self.output_path_edit, self.open_output_button = self._path_row(storage_grid, 2, "出力")
        self.ending_path_edit = QLineEdit()
        self.ending_path_edit.setReadOnly(True)
        self.change_ending_button = QPushButton("Ending変更…")
        self.preview_ending_button = QPushButton("Ending確認")
        storage_grid.addWidget(QLabel("Ending"), 3, 0)
        storage_grid.addWidget(self.ending_path_edit, 3, 1)
        storage_grid.addWidget(self.change_ending_button, 3, 2)
        storage_grid.addWidget(self.preview_ending_button, 4, 2)
        self.storage_free_label = QLabel("空き容量: 取得中")
        self.storage_free_label.setObjectName("secondaryText")
        storage_grid.addWidget(self.storage_free_label, 4, 0, 1, 2)
        storage.body.addLayout(storage_grid)
        layout.addWidget(storage)

        # Compatibility labels remain available to the existing tests and
        # presentation adapters, but the visible values live in HUD panels.
        compatibility = QWidget()
        compatibility.hide()
        compatibility_layout = QVBoxLayout(compatibility)
        self.total_label = QLabel()
        self.active_label = QLabel()
        self.notebook_complete_label = QLabel()
        self.zip_complete_label = QLabel()
        self.error_label = QLabel()
        for label in (
            self.total_label,
            self.active_label,
            self.notebook_complete_label,
            self.zip_complete_label,
            self.error_label,
        ):
            compatibility_layout.addWidget(label)
        layout.addWidget(compatibility)
        layout.addStretch(1)
        scroll.setWidget(column)
        return scroll

    def _connect_local_controls(self) -> None:
        self.open_input_button.clicked.connect(lambda: self._open_directory(self.input_path_edit.text()))
        self.open_raw_folder_button.clicked.connect(lambda: self._open_directory(self.raw_path_edit.text()))
        self.open_output_button.clicked.connect(lambda: self._open_directory(self.output_path_edit.text()))
        self.change_ending_button.clicked.connect(self.change_ending)
        self.preview_ending_button.clicked.connect(self.preview_ending)
        self.reload_button.clicked.connect(self.reload_jobs)
        self.start_button.clicked.connect(self.start_processing)
        self.recover_button.clicked.connect(self.recover_pending)
        self.pause_button.clicked.connect(self.pause_processing)
        self.stop_button.clicked.connect(self.stop_processing)
        self.login_button.clicked.connect(self.start_login)
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
        edit.setObjectName("storagePath")
        button.setMaximumWidth(52)
        layout.addWidget(edit, row, 1)
        layout.addWidget(button, row, 2)
        return edit, button

    def _connect_controller(self) -> None:
        self.controller.jobs_changed.connect(self.set_jobs)
        self.controller.status_changed.connect(self._apply_runtime_status)
        self.controller.log_received.connect(self._append_log_record)
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
        try:
            usage = shutil.disk_usage(self._resolve_setting_path(settings.output_directory))
            free_gib = usage.free / (1024**3)
            total_gib = usage.total / (1024**3)
            self.storage_free_label.setText(f"空き容量: {free_gib:.0f} GB / {total_gib:.0f} GB")
        except OSError:
            self.storage_free_label.setText("空き容量: 取得不可")
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
        dialog = SettingsDialog(
            self.settings,
            self,
            preset_repository=self.preset_repository,
        )
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
            values = (
                str(row + 1),
                job.script_name,
                notebook,
                ending,
                hls,
                state_display(job),
                f"{max(0.0, min(100.0, job.progress_percent)):.0f}%",
                self._display_time(job.generation_started_at),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                if column == 5:
                    item.setForeground(QBrush(self._state_color(job)))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column in {0, 2, 3, 4, 6, 7}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.job_table.setItem(row, column, item)
        summary = summarize_jobs(self.jobs)
        self.job_total_badge.setText(f"TOTAL {summary.total}")
        self.total_label.setText(f"全Job: {summary.total}")
        self.active_label.setText(f"処理中: {summary.active}")
        self.notebook_complete_label.setText(f"Notebook完了: {summary.notebook_complete}/{summary.total}")
        self.zip_complete_label.setText(f"ZIP完了: {summary.zip_complete}/{summary.total}")
        self.error_label.setText(f"Error: {summary.errors}")
        self.total_metric.setValue(summary.total)
        self.complete_metric.setValue(summary.zip_complete)
        self.error_metric.setValue(summary.errors)
        self.active_metric.setValue(summary.active)
        reserved_count = sum(
            job.state is JobState.RESERVED_WAITING_CREDIT_RESET for job in self.jobs
        )
        self.reserved_count_label.setText(f"予約待ち: {reserved_count}")
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
        self.progress_bar.setValue(
            round(max(0.0, min(100.0, current.progress_percent if current else 0.0)))
        )
        self._update_pipeline_steps(current)
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

    @staticmethod
    def _state_color(job: Job) -> QColor:
        if job.state is JobState.COMPLETED:
            return QColor(PALETTE["success"])
        if job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}:
            return QColor(PALETTE["error"])
        if job.state is JobState.RESERVED_WAITING_CREDIT_RESET:
            return QColor(PALETTE["credit_waiting"])
        if job.state is JobState.RECOVERY_PENDING:
            return QColor(PALETTE["reservation"])
        if job.state in ACTIVE_STATES or job.state is JobState.WAITING_VIDEO:
            return QColor(PALETTE["bright_cyan"])
        return QColor(PALETTE["secondary"])

    def _update_pipeline_steps(self, current: Job | None) -> None:
        for step in self.pipeline_steps.values():
            step.setState("waiting")
        if current is None:
            if self.jobs and all(job.state is JobState.COMPLETED for job in self.jobs):
                for step in self.pipeline_steps.values():
                    step.setState("done")
            return
        order = ("auth", "preflight", "notebook", "credit", "ending", "hls", "zip")
        state_index = {
            JobState.WAITING: 0,
            JobState.UPLOADING: 2,
            JobState.CREDIT_EXHAUSTED: 3,
            JobState.RESERVED_WAITING_CREDIT_RESET: 3,
            JobState.RECOVERY_PENDING: 3,
            JobState.GENERATING: 2,
            JobState.WAITING_VIDEO: 2,
            JobState.DOWNLOAD_PENDING: 2,
            JobState.DOWNLOADING: 2,
            JobState.RAW_READY: 4,
            JobState.ENDING: 4,
            JobState.HLS_ENCODING: 5,
            JobState.ZIPPING: 6,
            JobState.COMPLETED: 6,
            JobState.FAILED: 2,
            JobState.DOWNLOAD_VERIFY_FAILED: 2,
        }.get(current.state, 0)
        for index, key in enumerate(order):
            if index < state_index:
                self.pipeline_steps[key].setState("done")
            elif index == state_index:
                visual = "error" if current.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED} else "active"
                if current.state in {JobState.CREDIT_EXHAUSTED, JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING}:
                    visual = "reserved"
                self.pipeline_steps[key].setState(visual)

    def _append_log_record(self, record: object) -> None:
        self._log_dialog.append_record(record)
        if isinstance(record, LogRecord):
            value = record.sanitized()
            timestamp = self._display_time(value.timestamp, include_seconds=True)
            level = value.level.upper()
            message = value.message
        elif isinstance(record, dict):
            timestamp = sanitize_log_text(str(record.get("timestamp", "")))[-8:]
            level = sanitize_log_text(str(record.get("level", "INFO"))).upper()
            message = sanitize_log_text(str(record.get("message", "")))
        else:
            timestamp, level, message = "", "INFO", sanitize_log_text(str(record))
        row = self.execution_log_table.rowCount()
        self.execution_log_table.insertRow(row)
        color = {
            "ERROR": QColor(PALETTE["error"]),
            "WARNING": QColor(PALETTE["warning"]),
        }.get(level, QColor(PALETTE["cyan"]))
        for column, value in enumerate((timestamp, level, message)):
            item = QTableWidgetItem(value)
            if column == 1:
                item.setForeground(QBrush(color))
            self.execution_log_table.setItem(row, column, item)
        while self.execution_log_table.rowCount() > 200:
            self.execution_log_table.removeRow(0)
        self.execution_log_table.scrollToBottom()

    @staticmethod
    def _display_time(value: str | None, *, include_seconds: bool = False) -> str:
        if not value:
            return "－"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return sanitize_log_text(value)[-8:] if include_seconds else sanitize_log_text(value)
        return parsed.strftime("%H:%M:%S" if include_seconds else "%H:%M")

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
        if self.preset_repository is not None and self.preset_repository.selected() is None:
            QMessageBox.warning(
                self,
                "プリセット未設定",
                "動画生成プリセットを登録・選択してください。",
            )
            return
        if self._ending_path() is None:
            QMessageBox.warning(
                self,
                "Ending未設定",
                "授業動画作成を開始する前に、有効なEnding動画を設定してください。",
            )
            return
        if self.controller.start():
            self._running = True
            self.statusBar().showMessage("準備確認中...")
            self._update_action_state()

    def start_login(self) -> None:
        self.browser_status_label.setText("● 認証操作中")
        self.statusBar().showMessage(
            "Googleログイン用Chromeを開きます。ログイン後、このChromeを閉じてください。"
            "パスワード等をアプリが取得することはありません。"
        )
        self.controller.login()

    def recover_pending(self) -> None:
        if self._ending_path() is None:
            QMessageBox.warning(
                self,
                "Ending未設定",
                "未回収動画の後工程を続ける前に、有効なEnding動画を設定してください。",
            )
            return
        if self.controller.recover_pending():
            self._running = True
            self.statusBar().showMessage("未回収動画を確認中...")
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
        if operation == "start":
            self.browser_status_label.setText("● Pre-flight確認中")
            self.statusBar().showMessage("準備確認中...")
        elif operation == "login":
            self.browser_status_label.setText("● 認証用Chrome起動中")
            self.statusBar().showMessage(
                "Googleログイン用Chromeでログインし、完了後にChromeを閉じてください"
            )
        elif operation == "recover":
            self._running = True
            self.statusBar().showMessage("未回収動画を確認中...")
        else:
            self.statusBar().showMessage(f"{operation} 実行中")

    def _operation_finished(self, operation: str, _result: object) -> None:
        if operation in {"stop", "pause", "recover"}:
            self._running = False
        if operation == "login":
            self.browser_status_label.setText("● ログイン確認待ち")
            self.statusBar().showMessage("ログイン確認待ち。［授業動画作成開始］で自動確認します")
        elif operation == "start":
            self.browser_status_label.setText("● Pre-flight / 処理監視中")
            self.statusBar().showMessage("準備確認中...")
        elif operation == "recover":
            self.statusBar().showMessage("未回収動画の確認が完了しました")
        else:
            self.statusBar().showMessage(f"{operation} 完了")
        self._update_action_state()
        if operation != "reload":
            self.reload_jobs(local_only=True)

    def _operation_failed(self, operation: str, message: str) -> None:
        if operation in {"start", "recover"}:
            self._running = False
        if operation in {"start", "login"}:
            self.browser_status_label.setText("● 認証エラー")
        self.statusBar().showMessage(f"{operation} 失敗")
        self._log_dialog.append_record({"level": "ERROR", "stage": operation, "message": message})
        QMessageBox.critical(self, "処理エラー", f"{operation}: {message}")
        self._update_action_state()

    def _apply_runtime_status(self, status: object) -> None:
        if isinstance(status, dict):
            self._running = bool(status.get("running", self._running))
            next_check = status.get("next_check", "－")
            self.next_check_label.setText(f"次回確認: {next_check}")
            credit_state = str(status.get("credit_state", "CREDIT_UNKNOWN"))
            credit_percent = status.get("credit_percent")
            credit_reset_at = status.get("credit_reset_at")
            credit_display = {
                "CREDIT_AVAILABLE": "利用可能",
                "CREDIT_LOW": "残量低下",
                "CREDIT_EXHAUSTED": "枯渇 / 予約待機",
                "CREDIT_UNKNOWN": "取得不可",
            }.get(credit_state, "取得不可")
            self.credit_state_label.setText(f"{credit_display}  /  {credit_state}")
            self.credit_percent_label.setText(
                "クレジット残量: 取得不可"
                if credit_percent is None
                else f"クレジット残量: {credit_percent}%"
            )
            self.credit_reset_label.setText(
                f"リセット予定: {credit_reset_at or '－'}"
            )
            self._update_action_state()

    def _update_action_state(self) -> None:
        self.start_button.setEnabled(not self._running and self._ending_path() is not None)
        self.recover_button.setEnabled(
            not self._running
            and self._ending_path() is not None
            and any(
                job.state
                in {
                    JobState.RESERVED_WAITING_CREDIT_RESET,
                    JobState.WAITING_VIDEO,
                    JobState.DOWNLOAD_PENDING,
                    JobState.RECOVERY_PENDING,
                }
                for job in self.jobs
            )
        )
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
