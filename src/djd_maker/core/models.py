from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


class JobState(StrEnum):
    WAITING = "WAITING"
    UPLOADING = "UPLOADING"
    GENERATING = "GENERATING"
    WAITING_VIDEO = "WAITING_VIDEO"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOAD_VERIFY_FAILED = "DOWNLOAD_VERIFY_FAILED"
    RAW_READY = "RAW_READY"
    ENDING = "ENDING"
    HLS_ENCODING = "HLS_ENCODING"
    ZIPPING = "ZIPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.WAITING: frozenset({JobState.UPLOADING, JobState.FAILED}),
    JobState.UPLOADING: frozenset({JobState.GENERATING, JobState.FAILED}),
    JobState.GENERATING: frozenset(
        {JobState.WAITING_VIDEO, JobState.DOWNLOADING, JobState.FAILED}
    ),
    JobState.WAITING_VIDEO: frozenset({JobState.DOWNLOADING, JobState.FAILED}),
    JobState.DOWNLOADING: frozenset(
        {JobState.RAW_READY, JobState.DOWNLOAD_VERIFY_FAILED, JobState.FAILED}
    ),
    JobState.DOWNLOAD_VERIFY_FAILED: frozenset(
        {JobState.DOWNLOADING, JobState.FAILED}
    ),
    JobState.RAW_READY: frozenset({JobState.ENDING, JobState.FAILED}),
    JobState.ENDING: frozenset({JobState.HLS_ENCODING, JobState.FAILED}),
    JobState.HLS_ENCODING: frozenset({JobState.ZIPPING, JobState.FAILED}),
    JobState.ZIPPING: frozenset({JobState.COMPLETED, JobState.FAILED}),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
}


class InvalidStateTransition(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class DownloadSafetyGate:
    """NotebookLM側の削除許可に必要な全検証結果。"""

    download_completed: bool = False
    not_temporary_file: bool = False
    mp4_exists: bool = False
    non_zero_size: bool = False
    size_stable: bool = False
    ffprobe_ok: bool = False
    video_stream_exists: bool = False
    positive_duration: bool = False
    raw_copy_succeeded: bool = False
    raw_exists: bool = False
    raw_size_matches: bool = False
    raw_ffprobe_ok: bool = False

    @property
    def remote_deletion_allowed(self) -> bool:
        return all(asdict(self).values())

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(name for name, passed in asdict(self).items() if not passed)


@dataclass(slots=True)
class Job:
    source_path: str
    id: str = field(default_factory=lambda: uuid4().hex)
    parent_job_id: str | None = None
    state: JobState = JobState.WAITING
    notebook_id: str | None = None
    notebook_url: str | None = None
    generation_started_at: str | None = None
    next_poll_at: str | None = None
    last_polled_at: str | None = None
    raw_path: str | None = None
    raw_size_bytes: int | None = None
    duration_seconds: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    last_audio_position_seconds: float | None = None
    cut_position_seconds: float | None = None
    edited_path: str | None = None
    ending_result: str | None = None
    zip_path: str | None = None
    hls_result: str | None = None
    progress_percent: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    attempt_by_stage: dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    safety_gate: DownloadSafetyGate = field(default_factory=DownloadSafetyGate)

    @property
    def script_name(self) -> str:
        return Path(self.source_path).stem

    def transition_to(self, target: JobState) -> None:
        target = JobState(target)
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidStateTransition(f"{self.state} -> {target} is not allowed")
        self.state = target
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        values = dict(data)
        values["state"] = JobState(values["state"])
        gate = values.get("safety_gate", {})
        values["safety_gate"] = DownloadSafetyGate(**gate)
        return cls(**values)
