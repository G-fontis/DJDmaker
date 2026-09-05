"""Non-destructive, collision-safe publishing into ``raw_files``."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from uuid import uuid4

from djd_maker.core.interfaces import MediaResult
from djd_maker.core.models import DownloadSafetyGate

from .validator import ValidationResult, VideoValidator


class RawStoreCollisionError(FileExistsError):
    pass


@dataclass(frozen=True, slots=True)
class RawStoreResult:
    path: Path
    media: MediaResult
    source_validation: ValidationResult
    raw_validation: ValidationResult
    safety_gate: DownloadSafetyGate


class RawSafeStore:
    """Copy a verified download through staging and atomically publish it."""

    def __init__(
        self,
        validator: VideoValidator,
        raw_directory: str | Path | None = None,
    ) -> None:
        self.validator = validator
        self.raw_directory = Path(raw_directory) if raw_directory is not None else None

    def save(self, source: str | Path, destination: str | Path) -> RawStoreResult:
        source_path = Path(source)
        source_result = self.validator.validate(source_path)
        destination = Path(destination)
        if destination.suffix.lower() != ".mp4":
            raise ValueError("RAW destination must use the .mp4 extension")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RawStoreCollisionError(f"RAW output already exists: {destination}")

        staging = destination.with_name(f".{destination.name}.{uuid4().hex}.staging")
        try:
            with source_path.open("rb") as read_stream, staging.open("xb") as write_stream:
                shutil.copyfileobj(read_stream, write_stream)
                write_stream.flush()
                os.fsync(write_stream.fileno())
            if staging.stat().st_size != source_result.size_bytes:
                raise IOError("RAW staging size does not match source")
            staging_result = self.validator.validate(
                staging, reject_temporary=False
            )
            try:
                # A same-directory hard link is an atomic no-overwrite publish:
                # unlike os.replace it cannot erase a destination won in a race.
                os.link(staging, destination)
            except FileExistsError:
                raise RawStoreCollisionError(
                    f"RAW output already exists: {destination}"
                ) from None
            staging.unlink()
            raw_result = self.validator.validate(destination)
            sizes_match = raw_result.size_bytes == source_result.size_bytes
            if not sizes_match:
                # The source stays untouched; a mismatching published RAW is
                # retained for diagnosis and is never silently overwritten.
                raise IOError("published RAW size does not match source")
            gate = DownloadSafetyGate(
                download_completed=True,
                not_temporary_file=source_result.not_temporary_file,
                mp4_exists=True,
                non_zero_size=source_result.size_bytes > 0,
                size_stable=source_result.size_stable,
                ffprobe_ok=True,
                video_stream_exists=source_result.metadata.has_video,
                positive_duration=source_result.metadata.duration_seconds > 0,
                raw_copy_succeeded=True,
                raw_exists=destination.is_file(),
                raw_size_matches=sizes_match,
                raw_ffprobe_ok=True,
            )
            media = MediaResult(
                path=destination,
                duration_seconds=raw_result.metadata.duration_seconds,
                size_bytes=raw_result.size_bytes,
            )
            return RawStoreResult(destination, media, source_result, raw_result, gate)
        finally:
            staging.unlink(missing_ok=True)

    def verify_existing(
        self, source: str | Path, destination: str | Path
    ) -> RawStoreResult:
        """Recover a crash after RAW publish without writing either file."""

        source_path = Path(source)
        destination_path = Path(destination)
        source_result = self.validator.validate(source_path)
        raw_result = self.validator.validate(destination_path)
        sizes_match = raw_result.size_bytes == source_result.size_bytes
        if not sizes_match:
            raise IOError("existing RAW size does not match downloaded source")
        gate = DownloadSafetyGate(
            download_completed=True,
            not_temporary_file=source_result.not_temporary_file,
            mp4_exists=True,
            non_zero_size=source_result.size_bytes > 0,
            size_stable=source_result.size_stable,
            ffprobe_ok=True,
            video_stream_exists=source_result.metadata.has_video,
            positive_duration=source_result.metadata.duration_seconds > 0,
            raw_copy_succeeded=True,
            raw_exists=destination_path.is_file(),
            raw_size_matches=True,
            raw_ffprobe_ok=True,
        )
        media = MediaResult(
            path=destination_path,
            duration_seconds=raw_result.metadata.duration_seconds,
            size_bytes=raw_result.size_bytes,
        )
        return RawStoreResult(
            destination_path, media, source_result, raw_result, gate
        )

    def store(self, source: str | Path, filename: str | None = None) -> RawStoreResult:
        """Directory-oriented convenience wrapper used by standalone callers."""
        if self.raw_directory is None:
            raise ValueError("raw_directory was not configured; call save() instead")
        source_path = Path(source)
        return self.save(source_path, self.raw_directory / (filename or source_path.name))
