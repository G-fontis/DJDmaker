"""Immutable, exclusive final-MP4 publication and read-only ownership checks."""
import os
from pathlib import Path
import tempfile

from .cancellation import checkpoint

SKIPPED = 'SKIPPED_BY_SETTING'


def queue_hls_from_mp4(job, directory, validator, jobs):
    """Explicit opt-in only: reuse a completed OFF job without remote work."""
    from .models import JobState
    if job.state is not JobState.COMPLETED or job.hls_zip_enabled:
        raise ValueError('MP4_ONLY_COMPLETED_REQUIRED')
    target = output_target(job, directory)
    if not owned_mp4(job, target, jobs) or not getattr(validator.validate(target), 'valid', True):
        raise ValueError('FINAL_MP4_UNVERIFIED')
    job.hls_zip_enabled = True
    job.edited_path = str(target)
    job.state = JobState.HLS_ENCODING
    job.hls_result = job.zip_result = None
    job.presentation_stage = 'HLS_ENCODING'
    job.progress_percent = 75
    # Prior checkpoints may bind another input. Retain their files, not claims.
    job.hls_checkpoint_directory = job.hls_source_sha256 = job.zip_checkpoint_path = None
    jobs.save(job)
    return job


def output_target(job, directory):
    root = Path(directory)
    return (root / (job.script_name + '.zip') if job.hls_zip_enabled else
            root / 'completed_mp4' / (job.script_name + '.mp4'))


def owned_mp4(job, output, jobs=()):
    from .artifact_ownership import _digest, _sha
    from .output_ownership import path_identity
    try:
        output = Path(output)
        if (not job.final_mp4_path or not _sha(job.final_mp4_sha256)
                or not _sha(job.source_sha256) or not job.safety_gate.remote_deletion_allowed
                or path_identity(job.final_mp4_path) != path_identity(output)
                or output.name != job.script_name + '.mp4'
                or not output.is_file() or output.stat().st_size == 0):
            return False
        values = jobs.list() if callable(getattr(jobs, 'list', None)) else jobs
        for other in values:
            if other.id == job.id or other.duplicate_of_job_id == job.id:
                continue
            if other.final_mp4_path and path_identity(other.final_mp4_path) == path_identity(output):
                return False
            if other.script_name == job.script_name and (
                    path_identity(other.source_path) != path_identity(job.source_path)
                    or other.source_sha256 and other.source_sha256 != job.source_sha256):
                return False
        for value in (job.source_path, job.archived_txt_path):
            if value and Path(value).exists() and _digest(value) != job.source_sha256:
                return False
        return _digest(output) == job.final_mp4_sha256
    except (OSError, ValueError, TypeError):
        return False


def publish_mp4(job, directory, validator, jobs, save):
    """Save intent before exclusive publication; never replace an existing file.

    The final path is separate from RAW even when raw/output folders coincide.
    A crash after publish is recovered by the persisted SHA, without conversion.
    """
    from .artifact_ownership import _digest
    from .output_ownership import path_identity
    source = Path(job.edited_path or '')
    target = output_target(job, directory)
    checkpoint('mp4.validate', job.id)
    if not job.safety_gate.remote_deletion_allowed:
        raise ValueError('RAW_SAFETY_GATE_INCOMPLETE')
    if not source.is_file() or not getattr(validator.validate(source), 'valid', True):
        raise ValueError('FINAL_MP4_INVALID')
    if job.raw_path and path_identity(target) == path_identity(job.raw_path):
        raise ValueError('FINAL_MP4_MUST_NOT_REPLACE_RAW')
    if target.exists():
        if not owned_mp4(job, target, jobs) or not getattr(validator.validate(target), 'valid', True):
            raise FileExistsError('既存の完成MP4を上書き・無条件採用しません')
        return target
    if not job.source_sha256:
        job.source_sha256 = _digest(job.archived_txt_path or job.source_path)
    digest = _digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + job.id + '-', suffix='.mp4', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as destination, source.open('rb') as origin:
            while block := origin.read(1024 * 1024):
                checkpoint('mp4.copy', job.id)
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if _digest(temporary) != digest or not getattr(validator.validate(temporary), 'valid', True):
            raise ValueError('FINAL_MP4_COPY_INVALID')
        job.final_mp4_path = str(target)
        job.final_mp4_sha256 = digest
        save(job)
        checkpoint('mp4.publish', job.id)
        # Same-volume hard link is atomic and fails if a concurrent writer won.
        os.link(temporary, target)
        if not owned_mp4(job, target, jobs):
            raise ValueError('FINAL_MP4_OWNERSHIP_INVALID')
        return target
    finally:
        temporary.unlink(missing_ok=True)
