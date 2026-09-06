from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.settings import AppSettings

from .viewmodels import LogRecord, safe_existing_file


def open_local_path(path: Path, *, parent: QWidget | None = None) -> bool:
    if not path.exists():
        QMessageBox.warning(parent, "パスを開けません", f"見つかりません:\n{path}")
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


class PresetRepositoryPort(Protocol):
    def list(self) -> list[Preset]: ...

    def selected(self) -> Preset | None: ...

    def create(self, name: str, prompt_text: str) -> Preset: ...

    def update(self, preset_id: str, name: str, prompt_text: str) -> Preset: ...

    def delete(self, preset_id: str) -> None: ...

    def select(self, preset_id: str | None) -> None: ...

    def duplicate(self, preset_id: str) -> Preset: ...


class PresetDialog(QDialog):
    """GNB Creator compatible name/body editor."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name: str = "",
        prompt_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("動画生成プリセット")
        self.resize(620, 430)
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("例：福ゼミ通常講義")
        self.prompt_edit = QPlainTextEdit(prompt_text)
        self.prompt_edit.setPlaceholderText(
            "NotebookLMの動画解説カスタムトピックへ送信する文章を入力してください"
        )
        form = QFormLayout()
        form.addRow("プリセット名", self.name_edit)
        form.addRow("本文", self.prompt_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip() or not self.prompt_edit.toPlainText().strip():
            QMessageBox.warning(self, "入力不足", "プリセット名と本文を入力してください。")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.prompt_edit.toPlainText().strip()


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
        *,
        preset_repository: PresetRepositoryPort | None = None,
    ) -> None:
        super().__init__(parent)
        self.preset_repository = preset_repository
        self.setWindowTitle("設定")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.input_directory_edit = self._directory_row(form, "台本フォルダー", settings.input_directory)
        self.raw_directory_edit = self._directory_row(form, "RAW保存先", settings.raw_directory)
        self.output_directory_edit = self._directory_row(form, "ZIP出力先", settings.output_directory)
        self.ending_video_edit = self._file_row(form, "Ending動画", settings.ending_video)

        self.first_check_spin = QSpinBox()
        self.first_check_spin.setRange(1, 86400)
        self.first_check_spin.setValue(settings.first_notebook_check_seconds)
        form.addRow("Notebook初回確認（秒）", self.first_check_spin)
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(1, 86400)
        self.poll_spin.setValue(settings.notebook_poll_seconds)
        form.addRow("Notebook再確認（秒）", self.poll_spin)
        self.ffmpeg_concurrency_combo = QComboBox()
        self.ffmpeg_concurrency_combo.addItems(["1", "2"])
        self.ffmpeg_concurrency_combo.setCurrentText(str(settings.ffmpeg_concurrency))
        form.addRow("FFmpeg同時処理数", self.ffmpeg_concurrency_combo)
        padding = QLabel("0.5秒（安全仕様・変更不可）")
        form.addRow("音声末尾余白", padding)

        if self.preset_repository is not None:
            preset_group = QGroupBox("動画生成プリセット")
            preset_layout = QVBoxLayout(preset_group)
            preset_form = QFormLayout()
            self.preset_combo = QComboBox()
            preset_form.addRow("選択中プリセット", self.preset_combo)
            preset_layout.addLayout(preset_form)
            preset_buttons = QHBoxLayout()
            self.new_preset_button = QPushButton("新規登録")
            self.edit_preset_button = QPushButton("編集")
            self.duplicate_preset_button = QPushButton("複製")
            self.delete_preset_button = QPushButton("削除")
            for button in (
                self.new_preset_button,
                self.edit_preset_button,
                self.duplicate_preset_button,
                self.delete_preset_button,
            ):
                preset_buttons.addWidget(button)
            preset_buttons.addStretch()
            preset_layout.addLayout(preset_buttons)
            layout.addWidget(preset_group)
            self.preset_combo.currentIndexChanged.connect(self._preset_changed)
            self.new_preset_button.clicked.connect(self._new_preset)
            self.edit_preset_button.clicked.connect(self._edit_preset)
            self.duplicate_preset_button.clicked.connect(self._duplicate_preset)
            self.delete_preset_button.clicked.connect(self._delete_preset)
            self._reload_presets()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_preset(self) -> Preset | None:
        if self.preset_repository is None:
            return None
        preset_id = self.preset_combo.currentData()
        return next((item for item in self.preset_repository.list() if item.id == preset_id), None)

    def _reload_presets(self, select_id: str | None = None) -> None:
        assert self.preset_repository is not None
        if select_id is None:
            selected = self.preset_repository.selected()
            select_id = selected.id if selected else None
        presets = self.preset_repository.list()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in presets:
            self.preset_combo.addItem(preset.name, preset.id)
        index = self.preset_combo.findData(select_id) if select_id is not None else -1
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        has_selection = index >= 0
        self.edit_preset_button.setEnabled(has_selection)
        self.duplicate_preset_button.setEnabled(has_selection)
        self.delete_preset_button.setEnabled(has_selection)

    def _preset_changed(self) -> None:
        assert self.preset_repository is not None
        self.preset_repository.select(self.preset_combo.currentData())
        has_selection = self.preset_combo.currentIndex() >= 0
        self.edit_preset_button.setEnabled(has_selection)
        self.duplicate_preset_button.setEnabled(has_selection)
        self.delete_preset_button.setEnabled(has_selection)

    def _new_preset(self) -> None:
        assert self.preset_repository is not None
        dialog = PresetDialog(self)
        if not dialog.exec():
            return
        try:
            preset = self.preset_repository.create(*dialog.values())
            self.preset_repository.select(preset.id)
            self._reload_presets(preset.id)
        except ValueError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))

    def _edit_preset(self) -> None:
        assert self.preset_repository is not None
        preset = self._selected_preset()
        if preset is None:
            QMessageBox.information(self, "プリセット", "編集するプリセットを選択してください。")
            return
        dialog = PresetDialog(self, name=preset.name, prompt_text=preset.prompt_text)
        if not dialog.exec():
            return
        try:
            self.preset_repository.update(preset.id, *dialog.values())
            self._reload_presets(preset.id)
        except ValueError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))

    def _duplicate_preset(self) -> None:
        assert self.preset_repository is not None
        preset = self._selected_preset()
        if preset is None:
            return
        try:
            duplicate = self.preset_repository.duplicate(preset.id)
            self.preset_repository.select(duplicate.id)
            self._reload_presets(duplicate.id)
        except ValueError as exc:
            QMessageBox.warning(self, "複製できません", str(exc))

    def _delete_preset(self) -> None:
        assert self.preset_repository is not None
        preset = self._selected_preset()
        if preset is None:
            return
        answer = QMessageBox.question(
            self, "プリセット削除", f"「{preset.name}」を削除しますか？"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.preset_repository.delete(preset.id)
            self._reload_presets()

    def _directory_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        edit = QLineEdit(value)
        button = QPushButton("選択…")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(button)
        button.clicked.connect(lambda: self._choose_directory(edit))
        form.addRow(label, row)
        return edit

    def _file_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        edit = QLineEdit(value)
        button = QPushButton("選択…")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(button)
        button.clicked.connect(lambda: self._choose_file(edit))
        form.addRow(label, row)
        return edit

    def _choose_directory(self, edit: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "フォルダーを選択", edit.text())
        if selected:
            edit.setText(selected)

    def _choose_file(self, edit: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Ending動画を選択", edit.text(), "動画 (*.mp4 *.mov *.mkv *.webm);;すべて (*)"
        )
        if selected:
            edit.setText(selected)

    def value(self) -> AppSettings:
        return AppSettings(
            input_directory=self.input_directory_edit.text().strip(),
            raw_directory=self.raw_directory_edit.text().strip(),
            output_directory=self.output_directory_edit.text().strip(),
            ending_video=self.ending_video_edit.text().strip(),
            first_notebook_check_seconds=self.first_check_spin.value(),
            notebook_poll_seconds=self.poll_spin.value(),
            audio_tail_padding_seconds=0.5,
            ffmpeg_concurrency=int(self.ffmpeg_concurrency_combo.currentText()),
        )

    def _validate_and_accept(self) -> None:
        try:
            value = self.value()
            value.validate()
            if not value.input_directory or not value.raw_directory or not value.output_directory:
                raise ValueError("3つのフォルダーを設定してください")
        except ValueError as exc:
            QMessageBox.warning(self, "設定を確認してください", str(exc))
            return
        self.accept()


class JobDetailDialog(QDialog):
    retry_requested = Signal(str, str)

    def __init__(self, job: Job, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job = job
        self.setWindowTitle(f"ジョブ詳細 - {job.script_name}")
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Job ID", QLabel(job.id))
        form.addRow("台本", QLabel(job.source_path))
        form.addRow("状態", QLabel(job.state.value))
        form.addRow("Notebook ID", QLabel(job.notebook_id or "－"))
        url = QLabel(job.notebook_url or "－")
        url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Notebook URL", url)
        form.addRow("生成開始", QLabel(job.generation_started_at or "－"))
        form.addRow("最終確認", QLabel(job.last_polled_at or "－"))
        form.addRow("次回確認", QLabel(job.next_poll_at or "－"))
        form.addRow("RAW", QLabel(job.raw_path or "－"))
        form.addRow("RAW size", QLabel(str(job.raw_size_bytes) if job.raw_size_bytes is not None else "－"))
        form.addRow("duration", QLabel(str(job.duration_seconds) if job.duration_seconds is not None else "－"))
        form.addRow("video codec", QLabel(job.video_codec or "－"))
        form.addRow("audio codec", QLabel(job.audio_codec or "－"))
        form.addRow("最終音声位置", QLabel(str(job.last_audio_position_seconds) if job.last_audio_position_seconds is not None else "－"))
        form.addRow("cut位置", QLabel(str(job.cut_position_seconds) if job.cut_position_seconds is not None else "－"))
        form.addRow("End処理済み", QLabel(job.edited_path or "－"))
        form.addRow("Ending結果", QLabel(job.ending_result or "－"))
        form.addRow("ZIP", QLabel(job.zip_path or "－"))
        form.addRow("HLS結果", QLabel(job.hls_result or "－"))
        form.addRow("試行回数", QLabel(", ".join(f"{k}: {v}" for k, v in job.attempt_by_stage.items()) or "－"))
        form.addRow("エラーコード", QLabel(job.error_code or "－"))
        error = QLabel(job.error_message or "－")
        error.setWordWrap(True)
        form.addRow("エラー詳細", error)
        layout.addLayout(form)

        timeline = QGroupBox("工程タイムライン")
        timeline_layout = QHBoxLayout(timeline)
        for text in ("Notebook", "RAW保存", "End処理", "HLS/ZIP"):
            timeline_layout.addWidget(QLabel(text))
        layout.addWidget(timeline)

        actions = QGridLayout()
        self.retry_job_button = QPushButton("ジョブ再実行")
        self.redownload_button = QPushButton("Geminiから再回収")
        self.retry_ending_button = QPushButton("Endから再実行")
        self.retry_hls_button = QPushButton("HLSから再実行")
        self.open_raw_button = QPushButton("RAWを開く")
        self.open_zip_button = QPushButton("ZIPを開く")
        self.open_notebook_button = QPushButton("Notebookを開く")
        buttons = (
            (self.retry_job_button, "job"),
            (self.redownload_button, "download"),
            (self.retry_ending_button, "ending"),
            (self.retry_hls_button, "hls"),
        )
        for index, (button, stage) in enumerate(buttons):
            actions.addWidget(button, index // 2, index % 2)
            button.clicked.connect(lambda _checked=False, value=stage: self.retry_requested.emit(job.id, value))
        actions.addWidget(self.open_raw_button, 2, 0)
        actions.addWidget(self.open_zip_button, 2, 1)
        actions.addWidget(self.open_notebook_button, 3, 0, 1, 2)
        layout.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        failed = job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}
        raw = safe_existing_file(job.raw_path)
        edited = safe_existing_file(job.edited_path)
        zip_path = safe_existing_file(job.zip_path)
        self.retry_job_button.setEnabled(failed)
        self.redownload_button.setEnabled(
            job.state is JobState.DOWNLOAD_VERIFY_FAILED and bool(job.notebook_id and job.notebook_url)
        )
        self.retry_ending_button.setEnabled(failed and raw is not None)
        self.retry_hls_button.setEnabled(failed and edited is not None)
        self.open_raw_button.setEnabled(raw is not None)
        self.open_zip_button.setEnabled(zip_path is not None)
        self.open_notebook_button.setEnabled(
            bool(job.notebook_url and job.notebook_url.startswith("https://notebook.google.com/"))
        )
        self.open_raw_button.clicked.connect(lambda: open_local_path(Path(job.raw_path or ""), parent=self))
        self.open_zip_button.clicked.connect(lambda: open_local_path(Path(job.zip_path or ""), parent=self))
        self.open_notebook_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(job.notebook_url or ""))
        )


class _LogFilter(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.criteria = ["", "", "", "", ""]

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # type: ignore[no-untyped-def]
        model = self.sourceModel()
        assert model is not None
        for column, criterion in enumerate(self.criteria, start=1):
            if criterion and criterion.casefold() not in str(model.index(source_row, column, source_parent).data() or "").casefold():
                return False
        return True


class LogDialog(QDialog):
    HEADERS = ("時刻", "Job", "Engine", "Stage", "Level", "Message")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("実行ログ")
        self.resize(1000, 520)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.filter_edits: list[QLineEdit] = []
        for placeholder in ("Job", "Engine", "Stage", "Level", "Message"):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.textChanged.connect(self._update_filter)
            filters.addWidget(edit)
            self.filter_edits.append(edit)
        layout.addLayout(filters)
        self.model = QStandardItemModel(0, len(self.HEADERS), self)
        self.model.setHorizontalHeaderLabels(self.HEADERS)
        self.proxy = _LogFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def append_record(self, record: LogRecord | dict[str, object] | object) -> None:
        if isinstance(record, dict):
            allowed = {key: record.get(key, "") for key in ("timestamp", "job_id", "engine", "stage", "level", "message")}
            record = LogRecord(**allowed)  # type: ignore[arg-type]
        if not isinstance(record, LogRecord):
            record = LogRecord("", message=str(record))
        value = record.sanitized()
        self.model.appendRow([QStandardItem(text) for text in (
            value.timestamp, value.job_id, value.engine, value.stage, value.level, value.message
        )])

    def _update_filter(self) -> None:
        self.proxy.criteria = [edit.text().strip() for edit in self.filter_edits]
        self.proxy.invalidate()
