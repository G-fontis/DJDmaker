from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import time
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QColor, QBrush
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.settings import AppSettings

from .controller import AsyncControllerBridge
from .dialogs import JobDetailDialog, LogDialog, SettingsDialog, open_local_path
from .preview import EndingPreviewPlayer
from .viewmodels import ACTIVE_STATES, job_stage_texts, state_display, summarize_jobs
from .viewmodels import sanitize_log_text
from djd_maker.core.runtime_operation import operation_text
from djd_maker.core.commands import CommandId, CommandRouter, BUTTON_COMMANDS, InterfaceError, EventId
from .phase2_presentation import Phase2Presentation
from .presentation_models import JobViewModel, CreditLimitViewModel


class SettingsRepositoryPort(Protocol):
    def load(self) -> AppSettings: ...

    def save(self, settings: AppSettings) -> None: ...


class JobRepositoryPort(Protocol):
    def list(self) -> list[Job]: ...


class PresetRepositoryPort(Protocol):
    def list(self) -> list[Preset]: ...

    def selected(self) -> Preset | None: ...


class NaturalItem(QTableWidgetItem):
    def __lt__(self, other):
        if self.flags() & Qt.ItemFlag.ItemIsUserCheckable and other.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            if self.checkState() != other.checkState():
                return self.checkState().value < other.checkState().value
        def key(value):
            return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", value)]
        return key(self.text()) < key(other.text())


class MainWindow(Phase2Presentation, QMainWindow):
    APPLICATION_NAME = "台本から授業動画つくるマシーン Ver1.2.5"
    ENGINE_CAPTION = "GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成"
    CREDIT = "Created by 福ゼミ塾長"
    JOB_COLUMNS = ("No", "台本名", "Notebook", "End処理", "HLS/ZIP", "状態", "選択")

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
        self._checked_job_ids: set[str] = set()
        store = getattr(self.job_repository, '_deferred_state', None)
        self._deferred_overlays = set(store.entries) if store else set()
        self._running = False
        self._paused = False
        self._active_run = False
        self._dispatch_disabled = False
        self.last_command = None
        self._limit_status = {}
        self._stopping = False
        self._closing = False
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
        service = getattr(self.controller, "controller", None)
        refresh_credit = getattr(self.controller, "refresh_credit", None)
        if callable(getattr(service, "refresh_credit", None)) and callable(refresh_credit):
            refresh_credit()

    def _build_ui(self) -> None:
        self.JOB_COLUMNS = type(self).JOB_COLUMNS + (('進捗', '開始時刻') if self.settings.gui_type == 'PHASE2' else ())
        if self.settings.gui_type == 'PHASE2':
            from .hud import HUD_STYLESHEET
            self.setStyleSheet(HUD_STYLESHEET)
            self.resize(1600, 950)
            self._build_phase2()
        else:
            self.setStyleSheet('QWidget { background-color: #ffffff; color: #202020; }')
            self._build_phase1()

    def _build_phase1(self) -> None:
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
        self.recover_button = QPushButton("未回収動画のチェックから続ける")
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
            self.recover_button,
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
        self.credit_state_label = QLabel("クレジット状態: 取得不可")
        self.credit_percent_label = QLabel("クレジット残量: 取得不可")
        self.credit_reset_label = QLabel("リセット時刻: －")
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
            self.credit_state_label,
            self.credit_percent_label,
            self.credit_reset_label,
        ):
            summary_layout.addWidget(label)
        root.addWidget(summary)

        runtime = QGroupBox('現在の処理')
        runtime_grid = QGridLayout(runtime)
        self.runtime_labels = {}
        for index, (key, caption) in enumerate((
            ('job', '現在Job'), ('notebook', '現在Notebook'), ('phase', '現在フェーズ'),
            ('stage', '現在工程'), ('decision', '直前の判断'), ('next_action', '次の処理'),
            ('outcome', '判定結果'), ('attempt', 'Attempt'), ('elapsed', '経過時間'),
            ('count', '処理済み / 対象件数'),
        )):
            label = QLabel(caption + ': －')
            label.setWordWrap(True)
            self.runtime_labels[key] = (caption, label)
            runtime_grid.addWidget(label, index // 2, index % 2)
        self.runtime_messages = QPlainTextEdit()
        self.runtime_messages.setReadOnly(True)
        self.runtime_messages.setMaximumBlockCount(300)
        self.runtime_messages.setMaximumHeight(100)
        self.phase_counts_label = QLabel('生成・回収集計: －')
        runtime_grid.addWidget(self.phase_counts_label, 5, 0, 1, 2)
        runtime_grid.addWidget(self.runtime_messages, 6, 0, 1, 2)
        root.addWidget(runtime)
        self._runtime_record = {}
        self._runtime_timer = QTimer(self)
        self._runtime_timer.timeout.connect(self._refresh_runtime_elapsed)
        self._runtime_timer.start(1000)

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
        self.job_table.setSortingEnabled(True)
        header.setSortIndicatorShown(True)
        self.job_table.itemChanged.connect(self._check_changed)
        deletes = QHBoxLayout()
        self.delete_selected_button = QPushButton("選択したものを削除")
        self.delete_completed_button = QPushButton("完成したジョブを削除")
        deletes.addWidget(self.delete_selected_button)
        deletes.addWidget(self.delete_completed_button)
        root.addLayout(deletes)

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

        self._connect_local_controls()

    def _connect_local_controls(self):
        self.resume_button = QPushButton('再開')
        self.gui_type_switch = QComboBox()
        self.gui_type_switch.addItems(['PHASE1', 'PHASE2'])
        self.gui_type_switch.setCurrentText(self.settings.gui_type)
        self.limit_label = QLabel('AI使用量上限: 未検出')
        self.limit_label.setWordWrap(True)
        tools = QHBoxLayout()
        tools.addWidget(QLabel('GUIタイプ'))
        tools.addWidget(self.gui_type_switch)
        tools.addWidget(self.resume_button)
        tools.addWidget(self.limit_label, 1)
        self.centralWidget().layout().addLayout(tools)
        commands = {
            CommandId.SETTINGS_OPEN: lambda _: self.show_settings(),
            CommandId.GOOGLE_LOGIN: lambda _: self.start_login(),
            CommandId.CLASS_VIDEO_START: lambda _: self.start_processing(),
            CommandId.SCRIPT_RELOAD: lambda _: self.reload_jobs(),
            CommandId.RECOVERY_START: lambda _: self.recover_pending(),
            CommandId.PAUSE: lambda _: self.pause_processing(),
            CommandId.RESUME: lambda _: self.controller.resume(),
            CommandId.STOP: lambda _: self.stop_processing(),
            CommandId.LOG_OPEN: lambda _: self.show_logs(),
            CommandId.JOB_DETAIL_OPEN: lambda _: self.show_selected_job(),
            CommandId.DELETE_SELECTED: lambda p: self._delete_jobs(False, p['job_ids']),
            CommandId.DELETE_COMPLETED: lambda p: self._delete_jobs(True, p['job_ids']),
            CommandId.GUI_SWITCH_PHASE1: lambda p: self.switch_gui(p['gui_type']),
            CommandId.GUI_SWITCH_PHASE2: lambda p: self.switch_gui(p['gui_type']),
            CommandId.INPUT_OPEN: lambda _: self._open_directory(self.input_path_edit.text()),
            CommandId.RAW_OPEN: lambda _: self._open_directory(self.raw_path_edit.text()),
            CommandId.OUTPUT_OPEN: lambda _: self._open_directory(self.output_path_edit.text()),
            CommandId.ENDING_CHANGE: lambda _: self.change_ending(),
            CommandId.ENDING_PREVIEW: lambda _: self.preview_ending(),
        }
        self.command_router = CommandRouter(commands.items())
        self.bound_commands = frozenset(commands)
        for attribute, command in BUTTON_COMMANDS.items():
            button = getattr(self, attribute)
            button.setProperty('command_id', command.value)
            button.clicked.connect(lambda checked=False, cmd=command: self.dispatch_command(cmd))
        self.gui_type_switch.currentTextChanged.connect(lambda value: self.dispatch_command(
            CommandId.GUI_SWITCH_PHASE1 if value == 'PHASE1' else CommandId.GUI_SWITCH_PHASE2, {'gui_type': value}))
        self.job_table.itemSelectionChanged.connect(self._update_action_state)
        self.job_table.itemDoubleClicked.connect(lambda _item: self.dispatch_command(CommandId.JOB_DETAIL_OPEN))

    def dispatch_command(self, command, payload=None):
        if self._dispatch_disabled:
            return False
        if payload is None and command in {CommandId.DELETE_SELECTED, CommandId.DELETE_COMPLETED}:
            payload = {'job_ids': sorted(self._checked_job_ids) if command == CommandId.DELETE_SELECTED else [j.id for j in self.jobs if j.state is JobState.COMPLETED]}
        try:
            result = self.command_router.dispatch(command, payload)
            self.last_command = command
            return result
        except InterfaceError as exc:
            self._dispatch_disabled = True
            for attribute in BUTTON_COMMANDS:
                getattr(self, attribute).setEnabled(False)
            self.gui_type_switch.setEnabled(False)
            self._log_dialog.append_record({'level': 'CRITICAL', 'message': str(exc)})
            self.statusBar().showMessage(str(exc))
            return False

    def switch_gui(self, gui_type):
        if self._active_run or self._running or self._paused or self.controller.busy:
            self.statusBar().showMessage('GUI切替は処理停止中のみ可能です。実行中の表示接続を保持します。')
            self.gui_type_switch.blockSignals(True)
            self.gui_type_switch.setCurrentText(self.settings.gui_type)
            self.gui_type_switch.blockSignals(False)
            return False
        selected = self._selected_job()
        self.settings_repository.save(replace(self.settings, gui_type=gui_type))
        self.settings = self.settings_repository.load()
        self._runtime_timer.stop()
        self._runtime_timer.deleteLater()
        old = self.takeCentralWidget()
        self._build_ui()
        self.apply_settings(self.settings)
        self.set_jobs(self.jobs)
        self._display_limit(self._limit_status)
        if selected:
            for row in range(self.job_table.rowCount()):
                if self.job_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected.id:
                    self.job_table.selectRow(row)
        old.deleteLater()
        return True

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
        self.controller.presentation_event.connect(self.consume_event, Qt.ConnectionType.QueuedConnection)
        self.controller.log_received.connect(self._append_log_record)
        self.controller.log_received.connect(self._append_runtime_message)
        self.controller.operation_started.connect(self._operation_started)
        self.controller.operation_finished.connect(self._operation_finished)
        self.controller.operation_failed.connect(self._operation_failed)

    def consume_event(self, event):
        if event.id == EventId.JOBS_UPDATED:
            self.set_jobs(event.payload)
        elif event.id == EventId.JOB_UPDATED:
            self.update_job(event.payload)
        elif event.id == EventId.RUNTIME_STATUS:
            self._apply_runtime_status(event.payload)
        elif event.id in {EventId.LIMIT_DETECTED, EventId.LIMIT_WAITING, EventId.LIMIT_RELEASED}:
            self._display_limit(event.payload)
        elif event.id == EventId.PAUSED:
            self.statusBar().showMessage('PAUSED / 一時停止中。次のNotebook操作は行いません')
        elif event.id == EventId.PHASE_CHANGED:
            self.statusBar().showMessage(str(event.payload.get('phase') or '待機中'))
        elif event.id == EventId.ERROR:
            self._append_log_record({'level': 'ERROR', **event.payload})

    def _append_log_record(self, record):
        self._log_dialog.append_record(record)
        if self.settings.gui_type != 'PHASE2':
            return
        if isinstance(record, dict):
            level, message = str(record.get('level','INFO')), str(record.get('message',''))
            if str(record.get('stage', '')).startswith('browser-'):
                auth = re.search(r'(?:^|, )authentication_result=([^,]+)', message)
                preflight = re.search(r'(?:^|, )preflight_result=([^,]+)', message)
                self.browser_status_label.setText('● ' + {'authenticated':'認証確認済み', 'login-required':'ログインが必要'}.get(auth.group(1) if auth else '', '認証状態: 未確認'))
                self.browser_detail_label.setText('Pre-flight: ' + (preflight.group(1) if preflight else 'NOT_RUN'))
            runtime = record.get('runtime')
            if isinstance(runtime, dict):
                message = f"[{runtime.get('job', '－')}] {operation_text(runtime.get('stage', ''))} / {operation_text(runtime.get('decision', ''))}"
        else:
            level, message = 'INFO', str(record)
        table = self.execution_log_table
        row = table.rowCount()
        table.insertRow(row)
        for column,value in enumerate((datetime.now().strftime('%H:%M:%S'),level,sanitize_log_text(message))):
            table.setItem(row,column,QTableWidgetItem(value))
        while table.rowCount()>200:
            table.removeRow(0)
        table.scrollToBottom()

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
        self._checked_job_ids.intersection_update(job.id for job in self.jobs)
        self.job_table.blockSignals(True)
        self.job_table.setSortingEnabled(False)
        self.job_table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            notebook, ending, hls = job_stage_texts(job)
            values = (str(row + 1), job.script_name, notebook, ending, hls, state_display(job))
            for column, value in enumerate(values):
                item = NaturalItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.id)
                self.job_table.setItem(row, column, item)
            checkbox = NaturalItem("")
            checkbox.setData(Qt.ItemDataRole.UserRole, job.id)
            checkbox.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox.setCheckState(Qt.CheckState.Checked if job.id in self._checked_job_ids else Qt.CheckState.Unchecked)
            self.job_table.setItem(row, 6, checkbox)
            if self.settings.gui_type == 'PHASE2':
                model = JobViewModel.from_job(job)
                for column, value in ((7, f'{model.progress}%'), (8, model.started)):
                    item = NaturalItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, job.id)
                    self.job_table.setItem(row, column, item)
        self.job_table.setSortingEnabled(True)
        self.job_table.blockSignals(False)
        self._refresh_summary()

    @Slot(object)
    def update_job(self, job: object) -> None:
        if not isinstance(job, Job) or getattr(self, '_closing', False):
            return
        old = next((j for j in self.jobs if j.id == job.id), None)
        if old and old.presentation_revision > job.presentation_revision:
            return
        selected = self._selected_job()
        selected_id = selected.id if selected else None
        scroll = self.job_table.verticalScrollBar().value()
        horizontal = self.job_table.horizontalScrollBar().value()
        self.jobs = [job if j.id == job.id else j for j in self.jobs]
        if old is None:
            self.jobs.append(job)
        self.job_table.blockSignals(True)
        sorting = self.job_table.isSortingEnabled()
        self.job_table.setSortingEnabled(False)
        row = next((r for r in range(self.job_table.rowCount()) if self.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)==job.id), None)
        if row is None:
            row = self.job_table.rowCount()
            self.job_table.insertRow(row)
        model = JobViewModel.from_job(job)
        values = (str(row+1), model.name, *model.stages, model.state, '')
        if self.settings.gui_type == 'PHASE2':
            values += (f'{model.progress}%', model.started)
        for column, value in enumerate(values):
            item = self.job_table.item(row,column)
            if item is None:
                item = NaturalItem(value)
                item.setData(Qt.ItemDataRole.UserRole,job.id)
                self.job_table.setItem(row,column,item)
            else:
                item.setText(value)
            if column == 6:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if job.id in self._checked_job_ids else Qt.CheckState.Unchecked)
        self.job_table.setSortingEnabled(sorting)
        if selected_id:
            for r in range(self.job_table.rowCount()):
                if self.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)==selected_id:
                    self.job_table.selectRow(r)
                    break
        self.job_table.verticalScrollBar().setValue(scroll)
        self.job_table.horizontalScrollBar().setValue(horizontal)
        self.job_table.blockSignals(False)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self._render_deferred_overlays()
        summary = summarize_jobs(self.jobs)
        self.total_label.setText(f"全Job: {summary.total}")
        self.active_label.setText(f"処理中: {summary.active}")
        self.notebook_complete_label.setText(f"Notebook完了: {summary.notebook_complete}/{summary.total}")
        self.zip_complete_label.setText(f"ZIP完了: {summary.zip_complete}/{summary.total}")
        self.error_label.setText(f"Error: {summary.errors}")
        runtime_id = getattr(self, '_runtime_record', {}).get('job_id')
        current = next((job for job in self.jobs if job.id == runtime_id), None)
        if current is None:
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
            + (f" / 状態保存保留 {len(self._deferred_overlays)}件（完全成功ではありません）" if self._deferred_overlays else '')
        )
        self.completion_group.setVisible(all_finished or bool(self._deferred_overlays))
        if self.settings.gui_type == 'PHASE2':
            self.total_metric.setValue(summary.total)
            self.complete_metric.setValue(summary.zip_complete)
            self.error_metric.setValue(summary.errors)
            self.active_metric.setValue(summary.active)
            self.job_total_badge.setText(f'TOTAL {summary.total}')
            self.progress_bar.setValue(int(current.progress_percent) if current else 0)
            for key, state in (JobViewModel.from_job(current).timeline if current else [(k,'waiting') for k in self.pipeline_steps]):
                self.pipeline_steps[key].setState(state)
            import shutil
            try:
                self.storage_free_label.setText(f'空き容量: {shutil.disk_usage(self.app_root).free // (1024**3)} GiB')
            except OSError:
                self.storage_free_label.setText('空き容量: 取得不可')
        self._update_action_state()

    def _selected_job(self) -> Job | None:
        row = self.job_table.currentRow()
        if row < 0 or row >= len(self.jobs):
            return None
        item = self.job_table.item(row, 0)
        if item is None:
            return None
        return next((job for job in self.jobs if job.id == item.data(Qt.ItemDataRole.UserRole)), None)

    def _check_changed(self, item) -> None:
        if item.column() != 6:
            return
        job_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() is Qt.CheckState.Checked:
            self._checked_job_ids.add(job_id)
        else:
            self._checked_job_ids.discard(job_id)

    def _delete_jobs(self, all_completed: bool, job_ids=None) -> None:
        selected = [job for job in self.jobs if (job.id in job_ids if job_ids is not None else (job.state is JobState.COMPLETED if all_completed else job.id in self._checked_job_ids))]
        if not selected:
            return
        if self._running or any(job.state is not JobState.COMPLETED for job in selected):
            QMessageBox.warning(self, "削除できません", "処理停止後、完成ジョブだけを選択してください。")
            return
        if QMessageBox.question(self, "完成ジョブ削除", f"{len(selected)}件の一覧記録を削除します。RAW・TXT・ZIP・Notebookは保持します。よろしいですか？") != QMessageBox.StandardButton.Yes:
            return
        self.controller.delete_completed([job.id for job in selected])

    def show_selected_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        dialog = JobDetailDialog(job, self)
        if job.id in self._deferred_overlays:
            dialog.layout().insertWidget(0, QLabel('状態保存待ち：成果物を照合してから再試行します。詳細はログを確認してください。'))
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
        if self.settings.ending_video and self._ending_path() is None:
            QMessageBox.warning(
                self,
                "Endingファイルが見つかりません",
                "選択したEnding動画を再選択するか、設定を空にしてください。未選択なら結合せずに進みます。",
            )
            return
        if self.controller.start():
            self._running = True
            self.statusBar().showMessage("認証・プロファイル・Notebookホーム画面を確認しています")
            self._update_action_state()

    def start_login(self) -> None:
        self.statusBar().showMessage(
            "Googleログイン用Chromeを開きます。ログイン後、このChromeを閉じてください。"
            "パスワード等をアプリが取得することはありません。"
        )
        self.controller.login()

    def recover_pending(self) -> None:
        if self.settings.ending_video and self._ending_path() is None:
            QMessageBox.warning(
                self,
                "Endingファイルが見つかりません",
                "選択したEnding動画を再選択するか、設定を空にしてください。",
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
        if (self._running or self._paused or self._active_run) and not self._stopping:
            self._stopping = True
            self.stop_button.setEnabled(False)
            self.controller.stop()
            self.statusBar().showMessage("安全な停止を要求しました。実行中工程の終了を待っています")

    def _operation_started(self, operation: str) -> None:
        if operation == "start":
            self.statusBar().showMessage("認証・プロファイル・Notebookホーム画面を確認しています")
        elif operation == "login":
            self.statusBar().showMessage(
                "Googleログイン用Chromeでログインし、完了後にChromeを閉じてください"
            )
        elif operation == "recover":
            self._running = True
            self.statusBar().showMessage("未回収動画を確認中...")
        else:
            self.statusBar().showMessage(f"{operation} 実行中")

    def _operation_finished(self, operation: str, _result: object) -> None:
        if operation == 'stop':
            self._stopping = False
        if operation in {"stop", "pause", "recover"}:
            self._running = False
        if operation == "stop" and isinstance(_result, dict):
            self._running = bool(_result.get("running", False))
        if operation == "login":
            self.statusBar().showMessage("ログイン確認待ち。［授業動画作成開始］で自動確認します")
        elif operation == "start":
            if not self._runtime_record:
                self.statusBar().showMessage("認証・プロファイル・Notebookホーム画面を確認しています")
        elif operation == "recover":
            self.statusBar().showMessage("未回収動画の確認が完了しました")
        elif operation == "stop" and self._running:
            self.statusBar().showMessage("停止要求済み。処理の安全な終了を待っています")
        else:
            self.statusBar().showMessage(f"{operation} 完了")
        self._update_action_state()
        if operation != "reload":
            self.reload_jobs(local_only=True)

    def _operation_failed(self, operation: str, message: str) -> None:
        if operation in {"start", "recover"}:
            self._running = False
        self.statusBar().showMessage(f"{operation} 失敗")
        self._log_dialog.append_record({"level": "ERROR", "stage": operation, "message": message})
        QMessageBox.critical(self, "処理エラー", f"{operation}: {message}")
        self._update_action_state()

    def _apply_runtime_status(self, status: object) -> None:
        if isinstance(status, dict):
            self._paused = bool(status.get('paused', False))
            self._active_run = bool(status.get('active', status.get('running', False)))
            limit = status.get('cloud_limit', {})
            self._display_limit(limit)
            record = status.get('runtime')
            if isinstance(record, dict) and str(record.get('stage', '')).startswith('save.'):
                job_id = record.get('job_id')
                if job_id:
                    if record['stage'] == 'save.recovered':
                        self._deferred_overlays.discard(job_id)
                        job = next((j for j in self.jobs if j.id == job_id), None)
                        if job is not None:
                            self.update_job(job)
                    else:
                        self._deferred_overlays.add(job_id)
                self._refresh_summary()
            if isinstance(record, dict) and record:
                if record.get('phase_counts'):
                    self.phase_counts_label.setText(str(record['phase_counts']))
                if record.get('stage') in {'stop.requested','stop.wait','stop.complete','pause','resume'}:
                    for row in range(self.job_table.rowCount()):
                        if self.job_table.item(row,0).data(Qt.ItemDataRole.UserRole) == record.get('job_id'):
                            self.job_table.item(row,5).setText('－ ' + operation_text(record['stage']))
                self._runtime_record = dict(record)
                if record.get('job_id'):
                    self.current_job_label.setText(f"現在ジョブ: {record.get('job', '－')}")
                    self.current_stage_label.setText(f"現在工程: {operation_text(record.get('stage', ''))}")
                for key, (caption, label) in self.runtime_labels.items():
                    value = record.get(key, '－')
                    if key == 'count':
                        value = f"{record.get('processed', 0)} / {record.get('total', 0)}"
                    label.setText(f'{caption}: {operation_text(value)}')
                self._refresh_runtime_elapsed()
                self.statusBar().showMessage(
                    f"{record.get('phase', '処理中')} {record.get('processed', 0)}/{record.get('total', 0)}: "
                    f"{record.get('job', '－')} / {operation_text(record.get('stage', ''))}")
            self._running = bool(status.get("running", self._running))
            next_check = status.get("next_check", "－")
            self.next_check_label.setText(f"次回確認: {next_check}")
            credit_state = str(status.get("credit_state", "CREDIT_UNKNOWN"))
            credit_percent = status.get("credit_percent")
            credit_reset_at = status.get("credit_reset_at")
            self.credit_state_label.setText(f"クレジット状態: {credit_state}")
            self.credit_percent_label.setText(
                "クレジット残量: 取得不可"
                if credit_percent is None
                else f"クレジット残量: {credit_percent}%"
            )
            self.credit_reset_label.setText(
                f"リセット時刻: {credit_reset_at or '－'}"
            )
            if limit.get('active'):
                self.credit_state_label.setText('AI LIMIT / CLOUD PAUSED')
                self.credit_reset_label.setText(f"Notebook再開予定: {limit.get('cloud_resume_at') or '時刻確認必要'}")
            self._update_action_state()

    def _render_deferred_overlays(self):
        # Presentation only: never modify Job or paint a failed save as success.
        sorting = self.job_table.isSortingEnabled()
        self.job_table.setSortingEnabled(False)
        for row in range(self.job_table.rowCount()):
            item = self.job_table.item(row, 5)
            job_id = self.job_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if job_id in self._deferred_overlays:
                item.setText('状態保存待ち（他job続行）')
                item.setForeground(QBrush(QColor('#9b6500')))
            else:
                item.setForeground(QBrush())
        self.job_table.setSortingEnabled(sorting)

    def _display_limit(self, state):
        self._limit_status = dict(state)
        self.limit_label.setText(CreditLimitViewModel.from_status(state).text)

    def _refresh_runtime_elapsed(self):
        limit = dict(self._limit_status)
        if limit.get('active') and limit.get('cloud_resume_at'):
            try:
                deadline = datetime.fromisoformat(limit['cloud_resume_at'])
                limit['remaining_seconds'] = max(0, int((deadline-datetime.now().astimezone()).total_seconds()))
            except (ValueError, TypeError):
                pass
            self._display_limit(limit)
        record = self._runtime_record
        elapsed = record.get('elapsed', 0)
        if self._running and record.get('started') and record.get('stage') not in {'job.next', 'stop.complete'}:
            elapsed = max(0, time.monotonic() - record['started'])
        self.runtime_labels['elapsed'][1].setText(f'経過時間: {int(elapsed)}秒')

    def _append_runtime_message(self, value):
        if not isinstance(value, dict) or not isinstance(value.get('runtime'), dict):
            return
        record = value['runtime']
        stage = operation_text(record.get('stage', ''))
        decision = operation_text(record.get('decision', ''))
        if record.get('message'):
            decision = str(record['message'])
        text = f"{datetime.now():%H:%M:%S} [{record.get('job', '－')}] {stage} / {decision}"
        self.runtime_messages.appendPlainText(sanitize_log_text(text))

    def _update_action_state(self) -> None:
        if self._dispatch_disabled:
            return
        self.resume_button.setEnabled(self._paused)
        self.gui_type_switch.setEnabled(not (self._active_run or self._running or self._paused))
        self.start_button.setEnabled(not self._running)
        self.recover_button.setEnabled(
            not self._running
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
        self.stop_button.setEnabled((self._running or self._paused or self._active_run) and not self._stopping)
        self.login_button.setEnabled(not self._running)
        self.details_button.setEnabled(self._selected_job() is not None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.ignore()
            return
        self._closing = True
        self._preview_player.stop()
        self.setEnabled(False)
        if self.controller.shutdown(timeout_ms=5000):
            event.accept()
        else:
            self._closing = False
            self.setEnabled(True)
            QMessageBox.warning(
                self,
                "終了待機中",
                "バックグラウンド処理が安全に停止していません。少し待ってから再度終了してください。",
            )
            event.ignore()
