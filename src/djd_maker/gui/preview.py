from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class EndingPreviewPlayer(QObject):
    """Qt Multimedia preview that releases the source after playback."""

    def __init__(self, fallback: Callable[[Path], object], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fallback = fallback
        self._source: Path | None = None
        self._fallback_used = False
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.errorOccurred.connect(self._error)

    def play(self, path: Path) -> None:
        self.stop()
        self._source = path.resolve()
        self._fallback_used = False
        self.player.setSource(QUrl.fromLocalFile(str(self._source)))
        self.player.play()

    def stop(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self._source = None

    def _status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in {
            QMediaPlayer.MediaStatus.EndOfMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        }:
            if status is QMediaPlayer.MediaStatus.InvalidMedia:
                self._use_fallback()
            self.stop()

    def _error(self, *_args: object) -> None:
        self._use_fallback()
        self.stop()

    def _use_fallback(self) -> None:
        if self._source is not None and not self._fallback_used:
            self._fallback_used = True
            self._fallback(self._source)
