from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from djd_maker.core.models import Job, JobState


ACTIVE_STATES = frozenset(
    {
        JobState.UPLOADING,
        JobState.CREDIT_EXHAUSTED,
        JobState.GENERATING,
        JobState.DOWNLOAD_PENDING,
        JobState.DOWNLOADING,
        JobState.ENDING,
        JobState.HLS_ENCODING,
        JobState.ZIPPING,
    }
)


def _mark(done: bool, active: bool, failed: bool, label: str) -> str:
    if failed:
        return f"× {label}失敗"
    if done:
        return f"○ {label}完了"
    if active:
        return f"▶ {label}処理中"
    return f"－ {label}待機"


def job_stage_texts(job: Job) -> tuple[str, str, str]:
    failed = job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}
    notebook_done = job.state in {
        JobState.DOWNLOAD_PENDING,
        JobState.DOWNLOADING,
        JobState.DOWNLOAD_VERIFY_FAILED,
        JobState.RAW_READY,
        JobState.ENDING,
        JobState.HLS_ENCODING,
        JobState.ZIPPING,
        JobState.COMPLETED,
    }
    ending_done = bool(job.edited_path)
    hls_done = job.state is JobState.COMPLETED and bool(job.zip_path)
    notebook_active = job.state in {
        JobState.UPLOADING,
        JobState.CREDIT_EXHAUSTED,
        JobState.RESERVED_WAITING_CREDIT_RESET,
        JobState.RECOVERY_PENDING,
        JobState.GENERATING,
        JobState.WAITING_VIDEO,
        JobState.DOWNLOAD_PENDING,
        JobState.DOWNLOADING,
    }
    return (
        _mark(notebook_done, notebook_active, failed and not notebook_done, "Notebook"),
        _mark(ending_done, job.state is JobState.ENDING, failed and bool(job.raw_path) and not ending_done, "End"),
        _mark(
            hls_done,
            job.state in {JobState.HLS_ENCODING, JobState.ZIPPING},
            failed and ending_done and not hls_done,
            "HLS/ZIP",
        ),
    )


def state_display(job: Job) -> str:
    if job.state is JobState.COMPLETED:
        return "○ 完成"
    if job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}:
        return "× エラー"
    if job.state is JobState.RESERVED_WAITING_CREDIT_RESET:
        return "－ クレジット回復待ち（予約済み）"
    if job.state is JobState.RECOVERY_PENDING:
        return "－ 未回収動画の確認待ち"
    if job.state in {
        JobState.UPLOADING,
        JobState.CREDIT_EXHAUSTED,
        JobState.GENERATING,
        JobState.WAITING_VIDEO,
        JobState.DOWNLOAD_PENDING,
        JobState.DOWNLOADING,
    }:
        return "▶ 動画生成中"
    if job.state is JobState.ENDING:
        return "▶ End処理中"
    if job.state in {JobState.HLS_ENCODING, JobState.ZIPPING}:
        return "▶ HLS変換中"
    return "－ 未処理"


@dataclass(frozen=True, slots=True)
class JobSummary:
    total: int
    active: int
    notebook_complete: int
    zip_complete: int
    errors: int


def summarize_jobs(jobs: Iterable[Job]) -> JobSummary:
    values = tuple(jobs)
    return JobSummary(
        total=len(values),
        active=sum(job.state in ACTIVE_STATES for job in values),
        notebook_complete=sum(bool(job.raw_path) for job in values),
        zip_complete=sum(job.state is JobState.COMPLETED and bool(job.zip_path) for job in values),
        errors=sum(job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED} for job in values),
    )


_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(access[_-]?token|refresh[_-]?token|token|api[_-]?key|password|cookie|session)\b(\s*[:=]\s*)([^\s,;]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(<input\b[^>]*\bvalue\s*=\s*)(['\"])[^'\"]*\2"), r"\1\2[REDACTED]\2"),
    (re.compile(r"(?i)(browser[/\\](?:profile|user.data)[/\\])[^\s'\"]+"), r"\1[REDACTED]"),
)


def sanitize_log_text(value: object) -> str:
    text = str(value).replace("\x00", "�")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@dataclass(frozen=True, slots=True)
class LogRecord:
    timestamp: str
    job_id: str = ""
    engine: str = ""
    stage: str = ""
    level: str = "INFO"
    message: str = ""

    def sanitized(self) -> "LogRecord":
        return LogRecord(
            *(sanitize_log_text(value) for value in (
                self.timestamp,
                self.job_id,
                self.engine,
                self.stage,
                self.level,
                self.message,
            ))
        )


def safe_existing_file(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None
