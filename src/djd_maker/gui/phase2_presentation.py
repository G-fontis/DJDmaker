from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFrame,QLabel,QStyle,QPushButton,QTableWidget,QHeaderView,QGroupBox,QGridLayout,QScrollArea,QProgressBar,QLineEdit,QPlainTextEdit)
from .hud import (HudBackground,HudHeader,HudPanel,sidebar_button,PipelineStepWidget,CircularStatusWidget,PALETTE)

class Phase2Presentation:
    def _build_phase2(self) -> None:
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
        for column in (0, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.job_table.setSortingEnabled(True)
        self.job_table.itemChanged.connect(self._check_changed)
        deletes = QHBoxLayout()
        self.delete_selected_button = QPushButton('選択したものを削除')
        self.delete_completed_button = QPushButton('完成したジョブを削除')
        deletes.addWidget(self.delete_selected_button)
        deletes.addWidget(self.delete_completed_button)
        jobs_panel.body.addWidget(self.job_table, 1)
        jobs_panel.body.addLayout(deletes)
        layout.addWidget(jobs_panel, 5)

        timeline_panel = HudPanel("処理ステップ", eyebrow="CURRENT JOB")
        timeline_row = QHBoxLayout()
        timeline_row.setSpacing(2)
        self.pipeline_steps: dict[str, PipelineStepWidget] = {}
        for key, label in (
            ("auth", "Google認証"),
            ("preflight", "Pre-flight"),
            ("notebook", "Notebook"),
            ("credit", "AI LIMIT"),
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
        runtime = QGroupBox('現在の処理')
        runtime.setMinimumHeight(280)
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
            label.setMinimumHeight(20)
            self.runtime_labels[key] = (caption, label)
            runtime_grid.addWidget(label, index, 0)
        self.runtime_messages = QPlainTextEdit()
        self.runtime_messages.setReadOnly(True)
        self.runtime_messages.setMaximumBlockCount(300)
        self.runtime_messages.setMaximumHeight(100)
        self.phase_counts_label = QLabel('生成・回収集計: －')
        self.phase_counts_label.setWordWrap(True)
        runtime_grid.addWidget(self.phase_counts_label, 10, 0)
        self.runtime_messages.hide()
        current.body.addWidget(runtime)
        self._runtime_record = {}
        self._runtime_timer = QTimer(self)
        self._runtime_timer.timeout.connect(self._refresh_runtime_elapsed)
        self._runtime_timer.start(1000)


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

        credit = HudPanel("AI使用量上限", eyebrow="AI LIMIT")
        credit.setAccent(PALETTE["credit_waiting"])
        self.credit_state_label = QLabel("取得不可")
        self.credit_state_label.setObjectName("creditStateValue")
        self.credit_percent_label = QLabel("クレジット残量: 取得不可")
        self.credit_reset_label = QLabel("リセット予定: －")
        self.reserved_count_label = QLabel("Legacy reservation: 0")
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
        for label in column.findChildren(QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
        return scroll
