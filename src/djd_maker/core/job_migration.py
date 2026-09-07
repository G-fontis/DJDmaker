"""Additive, backed-up job migration; never infers remote success offline."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .models import Job, JobState
from .repositories import JobRepository


def failure_class(job: Job) -> str:
    if job.failure_class == 'FATAL_FAILED':
        return 'FATAL_FAILED'
    text = f"{job.error_code or ''} {job.error_message or ''}".casefold()
    if "fatal" in text:
        return "FATAL_FAILED"
    if any(term in text for term in ("source", "upload", "ソース", "アップロード")):
        return "SOURCE_UPLOAD_FAILED"
    if any(term in text for term in ("quota", "credit", "クォータ", "クレジット", "利用制限")):
        return "QUOTA_RECOVERY_PENDING"
    if any(term in text for term in ("response", "返信")):
        return "PRESET_RESPONSE_TIMEOUT"
    if any(term in text for term in ("preset", "chat", "prompt", "送信")):
        return "PRESET_SEND_FAILED"
    if job.raw_path or job.notebook_url:
        return "RECOVERY_PENDING"
    return "FATAL_FAILED"


def migrate_jobs(system: Path) -> dict:
    directory = system / "jobs"
    pending = []
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("kind") != "job" or value.get("schema_version") != 1:
            raise ValueError(f"MIGRATION_UNSUPPORTED: {path.name}")
        job = Job.from_dict(value["job"])
        if job.resume_schema_version > 2:
            raise ValueError("MIGRATION_NEWER_SCHEMA")
        if job.resume_schema_version == 2:
            continue
        pending.append((path, value, job))
    if not pending:
        return {"migrated": 0, "backup": None}
    backup = system / "migration-backups" / "resume-v2"
    # Preserve original bytes once. A interrupted migration resumes without
    # replacing its pre-migration backup with partially migrated documents.
    for path in system.rglob("*.json"):
        if "migration-backups" in path.relative_to(system).parts:
            continue
        destination = backup / path.relative_to(system)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    for path, value, job in pending:
        job.resume_schema_version = 2
        if job.state is JobState.FAILED:
            job.failure_class = failure_class(job)
        value["job"] = job.to_dict()
        JobRepository(directory).save(job)
    return {"migrated": len(pending), "backup": str(backup)}
