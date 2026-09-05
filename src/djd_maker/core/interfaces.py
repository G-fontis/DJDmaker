from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import DownloadSafetyGate, Job


@dataclass(frozen=True, slots=True)
class MediaResult:
    path: Path
    duration_seconds: float
    size_bytes: int


@dataclass(frozen=True, slots=True)
class HlsResult:
    directory: Path
    playlist: Path
    segments: tuple[Path, ...]
    zip_path: Path


class RemoteDeletionDenied(RuntimeError):
    pass


def require_remote_deletion_gate(gate: DownloadSafetyGate) -> None:
    if not gate.remote_deletion_allowed:
        failed = ", ".join(gate.failed_checks)
        raise RemoteDeletionDenied(f"remote deletion denied; failed checks: {failed}")


class NotebookEngine(Protocol):
    def submit(self, job: Job) -> tuple[str, str]: ...

    def inspect_status(self, job: Job) -> str: ...

    def download_artifact(self, job: Job, destination: Path) -> Path: ...

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None: ...


class EndingEngine(Protocol):
    def process(
        self, raw_video: Path, ending_video: Path, output_path: Path, padding_seconds: float
    ) -> MediaResult: ...


class HlsEngine(Protocol):
    def convert_validate_and_zip(self, video: Path, output_zip: Path) -> HlsResult: ...
