"""GUI-independent runnable task discovery; no I/O or implicit state writes."""
from dataclasses import dataclass
from datetime import datetime
from djd_maker.core.models import JobState

RESCAN_SECONDS = 600
MAX_ERROR_ATTEMPTS = 3


def no_generation_checkpoint(job):
    """Pre-generation records only; never erase legacy in-flight checkpoints."""
    if (job.duplicate_of_job_id or job.failure_class in {'OUTPUT_BLOCKED', 'TERMINAL_FAILED'}
            # A recorded operational failure is not an untouched job. Keep it
            # on the existing bounded recovery/identity-reconciliation path.
            or job.error_code not in {None, '', 'QUOTA_EXHAUSTED', 'CLOUD_BLOCKED_UNTIL'}
            or any(job.attempt_by_stage.get(k, 0) >= MAX_ERROR_ATTEMPTS
                   for k in ('scheduler.recovery', 'source.reupload', 'generation.failed_retry'))):
        return False
    if job.state not in {JobState.WAITING, JobState.UPLOADING, JobState.FAILED,
                          JobState.CREDIT_EXHAUSTED, JobState.RECOVERY_PENDING}:
        return False
    return (not any((job.notebook_id, job.notebook_url, job.generation_started_at,
                     job.raw_path, job.edited_path, job.zip_path, job.hls_checkpoint_directory,
                     job.zip_checkpoint_path, job.remote_checkpoint, job.resume_checkpoint,
                     job.reservation_created_at))
            and job.source_status in {'UNKNOWN', 'ABSENT', 'MISSING'}
            and job.artifact_status in {'NOT_CHECKED', 'ABSENT', 'NOT_STARTED', 'UNKNOWN'}
            and not job.safety_gate.remote_deletion_allowed)


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


def remote_reconcile_candidate(job):
    return (job.state is JobState.FAILED and bool(job.notebook_id or job.notebook_url
            or job.error_code == 'NOTEBOOK_STAGE_FAILED')
            and not job.duplicate_of_job_id and job.failure_class != 'OUTPUT_BLOCKED' and job.error_code != 'OUTPUT_NAME_COLLISION'
            and not (job.failure_class == 'TERMINAL_FAILED' and job.remote_checkpoint is not None))


def terminal(job):
    return bool(job.duplicate_of_job_id) or job.state is JobState.COMPLETED or (
        not remote_reconcile_candidate(job) and job.state in {JobState.FAILED,JobState.DOWNLOAD_VERIFY_FAILED} and job.failure_class == 'TERMINAL_FAILED' and
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
        remote = remote or remote_reconcile_candidate(job)
        allowed=job.id not in deferred and (remote_reconcile_candidate(job) or job.failure_class not in {'FATAL_FAILED','OUTPUT_BLOCKED'})
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
