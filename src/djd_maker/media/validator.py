"""Common, ffprobe-backed video validation.

The validator deliberately accepts videos without audio.  Callers which need
audio can inspect ``metadata.has_audio``; the ending adapter uses that fact to
apply the safe "keep the complete main video" policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable


TEMPORARY_DOWNLOAD_SUFFIXES = frozenset(
    {".crdownload", ".download", ".part", ".partial", ".tmp"}
)


class MediaValidationError(RuntimeError):
    """Raised when a file cannot be accepted as a usable video."""


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    duration_seconds: float
    video_stream_count: int
    audio_stream_count: int
    width: int | None = None
    height: int | None = None

    @property
    def has_video(self) -> bool:
        return self.video_stream_count > 0

    @property
    def has_audio(self) -> bool:
        return self.audio_stream_count > 0


@dataclass(frozen=True, slots=True)
class ValidationResult:
    path: Path
    size_bytes: int
    metadata: VideoMetadata
    size_stable: bool
    not_temporary_file: bool


def resolve_executable(name: str, explicit: str | Path | None) -> Path:
    candidate = str(explicit) if explicit is not None else shutil.which(name)
    if not candidate or not Path(candidate).is_file():
        raise MediaValidationError(f"{name} executable was not found")
    return Path(candidate).resolve()


class VideoValidator:
    """Validate existence, stable size, ffprobe JSON, video and duration."""

    def __init__(
        self,
        ffprobe_path: str | Path | None = None,
        *,
        stability_checks: int = 2,
        stability_interval_seconds: float = 0.2,
        timeout_seconds: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if stability_checks < 1:
            raise ValueError("stability_checks must be at least 1")
        if stability_interval_seconds < 0:
            raise ValueError("stability_interval_seconds must not be negative")
        self.ffprobe_path = resolve_executable("ffprobe", ffprobe_path)
        self.stability_checks = stability_checks
        self.stability_interval_seconds = stability_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._sleep = sleeper

    @staticmethod
    def is_temporary_download(path: str | Path) -> bool:
        name = Path(path).name.lower()
        return any(name.endswith(suffix) for suffix in TEMPORARY_DOWNLOAD_SUFFIXES)

    def _stable_size(self, path: Path) -> int:
        sizes: list[int] = []
        for index in range(self.stability_checks):
            try:
                sizes.append(path.stat().st_size)
            except OSError as exc:
                raise MediaValidationError(f"cannot stat video: {path}") from exc
            if index + 1 < self.stability_checks:
                self._sleep(self.stability_interval_seconds)
        if len(set(sizes)) != 1:
            raise MediaValidationError(f"video size is not stable: {path}")
        if sizes[-1] <= 0:
            raise MediaValidationError(f"video is empty: {path}")
        return sizes[-1]

    def probe(self, path: str | Path) -> VideoMetadata:
        target = Path(path)
        command = [
            str(self.ffprobe_path), "-v", "error", "-show_streams",
            "-show_format", "-of", "json", str(target),
        ]
        try:
            completed = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MediaValidationError(f"ffprobe could not inspect: {target}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()
            raise MediaValidationError(f"ffprobe rejected {target}: {detail}")
        try:
            payload = json.loads(completed.stdout)
            streams = payload.get("streams", [])
            format_data = payload.get("format", {})
            if not isinstance(streams, list) or not isinstance(format_data, dict):
                raise ValueError
            videos = [s for s in streams if s.get("codec_type") == "video"]
            audios = [s for s in streams if s.get("codec_type") == "audio"]
            duration_value = format_data.get("duration")
            if duration_value in (None, "N/A"):
                duration_value = next(
                    (s.get("duration") for s in streams if s.get("duration") not in (None, "N/A")),
                    None,
                )
            duration = float(duration_value)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MediaValidationError(f"invalid ffprobe output for: {target}") from exc
        if not videos:
            raise MediaValidationError(f"video stream is missing: {target}")
        if duration <= 0:
            raise MediaValidationError(f"video duration is not positive: {target}")
        first_video = videos[0]
        return VideoMetadata(
            duration_seconds=duration,
            video_stream_count=len(videos),
            audio_stream_count=len(audios),
            width=int(first_video["width"]) if first_video.get("width") else None,
            height=int(first_video["height"]) if first_video.get("height") else None,
        )

    def validate(
        self,
        path: str | Path,
        *,
        require_stable_size: bool = True,
        reject_temporary: bool = True,
    ) -> ValidationResult:
        target = Path(path)
        if not target.exists():
            raise MediaValidationError(f"video does not exist: {target}")
        if not target.is_file():
            raise MediaValidationError(f"video is not a regular file: {target}")
        not_temporary = not self.is_temporary_download(target)
        if reject_temporary and not not_temporary:
            raise MediaValidationError(f"temporary download cannot be used: {target}")
        size = self._stable_size(target) if require_stable_size else target.stat().st_size
        if size <= 0:
            raise MediaValidationError(f"video is empty: {target}")
        return ValidationResult(
            path=target,
            size_bytes=size,
            metadata=self.probe(target),
            size_stable=True,
            not_temporary_file=not_temporary,
        )
