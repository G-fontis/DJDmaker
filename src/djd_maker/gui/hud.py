from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


PALETTE = {
    "background": "#040B12",
    "background_alt": "#07131E",
    "panel": "#091B29",
    "panel_alt": "#0B2231",
    "cyan": "#00D9FF",
    "bright_cyan": "#00F0FF",
    "blue": "#008CFF",
    "teal": "#00E6C3",
    "success": "#39EFA8",
    "reservation": "#B985FF",
    "credit_waiting": "#F6C453",
    "warning": "#FFB33E",
    "error": "#FF5D47",
    "text": "#EAF8FF",
    "secondary": "#8FB7CB",
    "muted": "#456A7D",
}


HUD_STYLESHEET = """
QWidget {
    color: #EAF8FF;
    font-family: "Yu Gothic UI", "Meiryo UI", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog, QWidget#hudRoot { background: #040B12; }
QFrame#hudHeader, QFrame#hudSidebar, QFrame#hudPanel {
    background-color: rgba(7, 22, 34, 238);
    border: 1px solid #007FA6;
    border-radius: 3px;
}
QLabel#applicationTitle { color: #EAF8FF; font-size: 23pt; font-weight: 700; }
QLabel#engineCaption { color: #9ED8ED; font-size: 10pt; }
QLabel#creatorCaption, QLabel#panelEyebrow, QLabel#metricCaption {
    color: #00D9FF; font-size: 8pt; letter-spacing: 1px;
}
QLabel#panelTitle { color: #BEEFFF; font-size: 12pt; font-weight: 700; }
QLabel#secondaryText { color: #8FB7CB; }
QLabel#currentTaskName { color: #EAF8FF; font-size: 14pt; font-weight: 700; }
QLabel#currentTaskStage { color: #00E6C3; font-size: 10pt; }
QLabel#creditStateValue { color: #F6C453; font-size: 13pt; font-weight: 700; }
QLabel#storagePath { color: #BEEFFF; font-family: "Consolas", "Yu Gothic UI"; font-size: 8pt; }
QPushButton {
    min-height: 28px;
    padding: 5px 12px;
    color: #DDF7FF;
    background: #0A2536;
    border: 1px solid #087DA5;
    border-radius: 3px;
}
QPushButton:hover { background: #0E354B; border-color: #00D9FF; color: white; }
QPushButton:pressed { background: #061824; border-color: #00F0FF; padding-top: 7px; }
QPushButton:disabled { color: #456A7D; background: #08141D; border-color: #234554; }
QPushButton[hudRole="sidebar"] { min-height: 48px; text-align: left; padding-left: 16px; font-size: 11pt; }
QPushButton[hudRole="primary"] { min-height: 56px; background: #063A3E; border: 2px solid #00E6C3; color: #E9FFFF; font-weight: 700; }
QPushButton[hudRole="primary"]:hover { background: #07535A; border-color: #00F0FF; }
QPushButton[hudRole="recovery"] { min-height: 52px; background: #1B2144; border: 1px solid #8E6DE0; color: #E8DCFF; }
QPushButton[hudRole="danger"] { min-height: 48px; background: #321C20; border: 1px solid #FF714F; color: #FFD8CE; }
QPushButton[hudRole="danger"]:hover { background: #4A2425; border-color: #FFB33E; }
QGroupBox {
    margin-top: 13px; padding-top: 11px;
    color: #BEEFFF; font-weight: 600;
    border: 1px solid #116482; border-radius: 3px;
    background: rgba(8, 27, 41, 210);
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #00D9FF; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    color: #EAF8FF; background: #06131D; border: 1px solid #24596E;
    border-radius: 2px; padding: 5px; selection-background-color: #007CA3;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #00D9FF; }
QTableWidget, QTableView {
    color: #D9F4FF; background: #06131D; alternate-background-color: #091D2B;
    border: 1px solid #087DA5; gridline-color: #173C4D; selection-background-color: #0B5D75;
    selection-color: white;
}
QHeaderView::section {
    color: #BCEBFA; background: #0B2638; border: 0; border-right: 1px solid #245064;
    border-bottom: 1px solid #008CB8; padding: 6px; font-weight: 600;
}
QTableWidget::item, QTableView::item { padding: 4px; }
QAbstractScrollArea::corner { background: #06131D; }
QScrollArea { background: #040B12; border: 0; }
QScrollArea > QWidget > QWidget { background: #040B12; }
QScrollBar:vertical { background: #06131D; width: 10px; }
QScrollBar::handle:vertical { background: #17617A; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #06131D; height: 10px; }
QScrollBar::handle:horizontal { background: #17617A; min-width: 24px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar { color: #EAF8FF; background: #06131D; border: 1px solid #28576A; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #00BFE8; border-radius: 4px; }
QStatusBar { color: #8FDDF5; background: #06131D; border-top: 1px solid #075A78; }
QDialogButtonBox QPushButton { min-width: 90px; }
QToolTip { color: #EAF8FF; background: #091B29; border: 1px solid #00D9FF; }
"""


def apply_hud_theme(widget: QWidget) -> None:
    widget.setStyleSheet(HUD_STYLESHEET)


class HudBackground(QWidget):
    """Static technical-grid background. It intentionally owns no timer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hudRoot")

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PALETTE["background"]))
        painter.setPen(QPen(QColor(0, 102, 135, 30), 1))
        step = 32
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
        painter.setPen(QPen(QColor(0, 217, 255, 38), 1))
        painter.drawLine(0, 92, self.width(), 92)


class HudPanel(QFrame):
    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        eyebrow: str = "SYSTEM MODULE",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("hudPanel")
        self._accent = QColor(PALETTE["cyan"])
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 9, 12, 11)
        self.body.setSpacing(7)
        heading = QHBoxLayout()
        marker = QLabel("▰")
        marker.setStyleSheet("color: #00E6C3; font-size: 9pt;")
        self.title_label = QLabel(title)
        self.title_label.setObjectName("panelTitle")
        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setObjectName("panelEyebrow")
        heading.addWidget(marker)
        heading.addWidget(self.title_label)
        heading.addStretch(1)
        heading.addWidget(eyebrow_label)
        self.body.addLayout(heading)

    def setAccent(self, color: str) -> None:  # noqa: N802 - Qt-style API
        self._accent = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._accent, 2)
        painter.setPen(pen)
        width, height = self.width() - 1, self.height() - 1
        corner = 15
        painter.drawLine(1, corner, 1, 1)
        painter.drawLine(1, 1, corner, 1)
        painter.drawLine(width - corner, height, width, height)
        painter.drawLine(width, height, width, height - corner)


class HudHeader(QFrame):
    def __init__(self, title: str, engine: str, creator: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("hudHeader")
        self.setMinimumHeight(86)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 10, 20, 10)
        emblem = QLabel("▷")
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setFixedSize(60, 60)
        emblem.setStyleSheet(
            "border: 2px solid #00D9FF; border-radius: 30px; color: #A8F1FF;"
            "font-size: 28pt; background: #071E2D;"
        )
        layout.addWidget(emblem)
        names = QVBoxLayout()
        names.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("applicationTitle")
        engine_label = QLabel(engine)
        engine_label.setObjectName("engineCaption")
        names.addWidget(title_label)
        names.addWidget(engine_label)
        layout.addLayout(names, 1)
        system = QVBoxLayout()
        system.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        system_name = QLabel("AUTOMATIC CLASSROOM VIDEO\nPRODUCTION SYSTEM")
        system_name.setObjectName("creatorCaption")
        system_name.setAlignment(Qt.AlignmentFlag.AlignRight)
        creator_label = QLabel(creator)
        creator_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        creator_label.setObjectName("engineCaption")
        system.addWidget(system_name)
        system.addWidget(creator_label)
        layout.addLayout(system)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QColor(0, 217, 255, 180))
        gradient.setColorAt(0.62, QColor(0, 140, 255, 110))
        gradient.setColorAt(1, QColor(0, 217, 255, 0))
        painter.fillRect(QRectF(18, self.height() - 3, self.width() - 36, 2), gradient)


class CircularStatusWidget(QWidget):
    def __init__(self, caption: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self.caption = caption
        self.value = "0"
        self.color = QColor(color)
        self.setMinimumSize(60, 70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(72, 82)

    def setValue(self, value: object) -> None:  # noqa: N802
        self.value = str(value)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height() - 18)
        rect = QRectF((self.width() - side) / 2 + 5, 3, side - 10, side - 10)
        painter.setPen(QPen(QColor(37, 83, 103), 3))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QPen(self.color, 3))
        painter.drawArc(rect, 38 * 16, 268 * 16)
        painter.setPen(self.color)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(rect.adjusted(0, 8, 0, 0), Qt.AlignmentFlag.AlignHCenter, self.caption)
        painter.setPen(QColor(PALETTE["text"]))
        painter.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, 24, 0, 0), Qt.AlignmentFlag.AlignHCenter, self.value)


class PipelineStepWidget(QWidget):
    def __init__(self, label: str, key: str, parent=None) -> None:
        super().__init__(parent)
        self.label = label
        self.key = key
        self.state = "waiting"
        self.setMinimumHeight(72)

    def setState(self, state: str) -> None:  # noqa: N802
        self.state = state
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        colors = {
            "done": QColor(PALETTE["success"]),
            "active": QColor(PALETTE["bright_cyan"]),
            "reserved": QColor(PALETTE["reservation"]),
            "error": QColor(PALETTE["error"]),
            "waiting": QColor(PALETTE["muted"]),
        }
        color = colors.get(self.state, colors["waiting"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, 25)
        painter.setPen(QPen(QColor(20, 64, 82), 5))
        painter.drawEllipse(center, 17, 17)
        painter.setPen(QPen(color, 3))
        painter.drawEllipse(center, 17, 17)
        painter.setPen(color)
        painter.setFont(QFont("Segoe UI Symbol", 13, QFont.Weight.Bold))
        glyph = "✓" if self.state == "done" else "◆" if self.state in {"active", "reserved"} else "○"
        painter.drawText(QRectF(center.x() - 15, center.y() - 15, 30, 30), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.setPen(QColor(PALETTE["text"] if self.state != "waiting" else PALETTE["secondary"]))
        painter.setFont(QFont("Yu Gothic UI", 8))
        painter.drawText(QRectF(0, 49, self.width(), 20), Qt.AlignmentFlag.AlignHCenter, self.label)


def sidebar_button(text: str, *, role: str = "sidebar") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("hudRole", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button
