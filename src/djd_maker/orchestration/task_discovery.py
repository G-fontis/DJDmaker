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
    return job.state is JobState.COMPLETED or job.failure_class in {'TERMINAL_FAILED', 'FATAL_FAILED'}


def due(job, now):
    if not job.next_poll_at:
        return True
    try:
        deadline = datetime.fromisoformat(job.next_poll_at.replace('Z', '+00:00'))
        return deadline.tzinfo is not None and now >= deadline
    except (ValueError, TypeError):
        # Invalid persisted deadlines require explicit error recovery, not hot polling.
        return False
