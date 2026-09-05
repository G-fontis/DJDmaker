from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AppSettings:
    input_directory: str = "input"
    raw_directory: str = "raw_files"
    output_directory: str = "output"
    ending_video: str = ""
    first_notebook_check_seconds: int = 600
    notebook_poll_seconds: int = 120
    audio_tail_padding_seconds: float = 0.5
    ffmpeg_concurrency: int = 1

    def validate(self) -> None:
        if self.first_notebook_check_seconds < 1:
            raise ValueError("first_notebook_check_seconds must be positive")
        if self.notebook_poll_seconds < 1:
            raise ValueError("notebook_poll_seconds must be positive")
        if self.audio_tail_padding_seconds != 0.5:
            raise ValueError("Unit 0 safety baseline requires 0.5 seconds")
        if self.ffmpeg_concurrency not in {1, 2}:
            raise ValueError("ffmpeg_concurrency must be 1 or 2")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolved_directories(self, app_root: Path) -> tuple[Path, Path, Path]:
        return tuple(
            (app_root / value).resolve()
            for value in (self.input_directory, self.raw_directory, self.output_directory)
        )

