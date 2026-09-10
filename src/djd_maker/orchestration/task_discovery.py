"""GUI-independent runnable task discovery; no I/O or implicit state writes."""
from dataclasses import dataclass
from datetime import datetime
from djd_maker.core.models import JobState

RESCAN_SECONDS = 600
MAX_ERROR_ATTEMPTS = 3


@dataclass(frozen=True)
class Capabilities:
    can_generate: bool
    can_upload_source: bool
    can_chat: bool
    can_check_artifact: bool = True
    can_download: bool = True
    can_local_process: bool = True

    @classmethod
    def from_limit(cls, blocked):
        return cls(not blocked, not blocked, not blocked)


def terminal(job):
    return bool(job.duplicate_of_job_id) or job.state is JobState.COMPLETED or (
        job.state in {JobState.FAILED,JobState.DOWNLOAD_VERIFY_FAILED} and job.failure_class == 'TERMINAL_FAILED' and
        any(job.attempt_by_stage.get(key,0) >= MAX_ERROR_ATTEMPTS
            for key in ('scheduler.recovery','source.reupload','generation.failed_retry')))


def final_discovery(jobs, now, blocked=False, deferred=()):
    """Conservative complete gate: unknown/blocked/future states are unfinished."""
    values=list(jobs)
    owners={j.id:j for j in values}
    outstanding=[]; runnable=[]; waiting=[]
    for job in values:
        owner=owners.get(job.duplicate_of_job_id)
        if terminal(job) and (not job.duplicate_of_job_id or owner and not owner.duplicate_of_job_id):
            continue
        outstanding.append(job.id)
        local=job.state in {JobState.RAW_READY,JobState.ENDING,JobState.HLS_ENCODING,JobState.ZIPPING}
        remote=job.state in {JobState.GENERATING,JobState.WAITING_VIDEO,JobState.DOWNLOAD_PENDING,
                            JobState.DOWNLOADING,JobState.RECOVERY_PENDING,JobState.RESERVED_WAITING_CREDIT_RESET}
        allowed=job.id not in deferred and job.failure_class not in {'FATAL_FAILED','OUTPUT_BLOCKED'}
        if allowed and (local or due(job,now) and (remote or not blocked)):
            runnable.append(job.id)
        else:
            waiting.append(job.id)
    return dict(complete=not outstanding and not deferred, unfinished=outstanding,
                runnable=runnable, waiting=waiting, deferred=list(deferred))


def due(job, now):
    if not job.next_poll_at:
        return True
    try:
        deadline = datetime.fromisoformat(job.next_poll_at.replace('Z', '+00:00'))
        return deadline.tzinfo is not None and now >= deadline
    except (ValueError, TypeError):
        # Invalid persisted deadlines require explicit error recovery, not hot polling.
        return False
