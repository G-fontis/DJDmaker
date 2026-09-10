"""Reconcile current output claims before changing any job state.

No historical stem registry is authoritative. Duplicate imports are retained as
inactive references; remote/local evidence and the current target path decide
real conflicts. This module never touches media or remote notebooks.
"""
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import unicodedata

from .models import JobState
from .repositories import _thread_lock


def path_identity(value):
    return unicodedata.normalize('NFC', str(Path(value).resolve())).casefold()


def import_lock(jobs, fallback):
    return _thread_lock(Path(getattr(jobs, 'directory', fallback)) / 'source-import-identity')


def source_digest(path):
    with Path(path).open('rb') as stream:
        return sha256(stream.read()).hexdigest()


def same_source(a, b):
    return path_identity(a.source_path) == path_identity(b.source_path) and not (
        a.source_sha256 and b.source_sha256 and a.source_sha256 != b.source_sha256)


def has_work(job):
    return bool(job.notebook_id or job.notebook_url or job.raw_path or job.zip_path
                or job.generation_started_at)


def released_terminal(job):
    # An old classification without exhaustion evidence is still unfinished.
    # It cannot release a live output claim while discovery may retry it.
    return (job.state in {JobState.FAILED, JobState.DOWNLOAD_VERIFY_FAILED}
            and job.failure_class == 'TERMINAL_FAILED'
            and any(job.attempt_by_stage.get(key, 0) >= 3 for key in
                    ('scheduler.recovery', 'source.reupload', 'generation.failed_retry')))


def restore_collision(job):
    if job.error_code != 'OUTPUT_NAME_COLLISION':
        return
    if job.output_resume_state in JobState._value2member_map_ and job.output_resume_state != 'FAILED':
        job.state = JobState(job.output_resume_state)
    elif job.raw_path and job.safety_gate.remote_deletion_allowed:
        job.state = JobState.RAW_READY
    elif job.notebook_id and job.notebook_url:
        job.state = JobState.RECOVERY_PENDING
    else:
        job.state = JobState.WAITING
    job.error_code = job.error_message = job.failure_class = None
    job.output_conflict_job_id = None
    job.resume_checkpoint = job.state.value
    job.runtime_reason = 'OUTPUT_OWNERSHIP_RECONCILED'


def reconcile_output_ownership(jobs, output_directory, save=None):
    with import_lock(jobs, output_directory):
        # One complete snapshot/plan precedes all saves (no incremental owner
        # election based on records already failed by the same pass).
        values = list({j.id:j for j in jobs.list()}.values())
        before = {j.id:j.to_dict() for j in values}
        groups = defaultdict(list)
        digests = {}
        unreadable_sources = []
        for job in values:
            if not job.source_sha256 and not has_work(job) and Path(job.source_path).is_file():
                key = path_identity(job.source_path)
                if key not in digests:
                    try:
                        digests[key] = source_digest(job.source_path)
                    except OSError:
                        # Source access belongs to this job's normal retry path,
                        # not a global startup failure of unrelated jobs.
                        digests[key] = None
                        unreadable_sources.append(job.id)
                job.source_sha256 = digests[key]
            job.duplicate_of_job_id = None
            job.output_conflict_job_id = None
            groups[path_identity(job.source_path)].append(job)
        aliases = {}
        for group in groups.values():
            ordered = sorted(group, key=lambda j:(j.state is not JobState.COMPLETED,
                                                  not has_work(j), j.created_at, j.id))
            canonical = []
            for job in ordered:
                if released_terminal(job):
                    continue
                owner = next((c for c in canonical if same_source(c,job)), None)
                if owner and not has_work(job):
                    restore_collision(job)
                    job.duplicate_of_job_id = owner.id
                    job.runtime_reason = 'DUPLICATE_SOURCE_REFERENCE'
                    aliases[job.id] = owner.id
                else:
                    canonical.append(job)
        claims = defaultdict(list)
        stale = []
        for job in values:
            if job.id in aliases:
                continue
            target = Path(output_directory) / (job.script_name+'.zip')
            if job.state is JobState.COMPLETED:
                # Historical completion in another output directory is not a
                # claim on this directory. Existing media remain untouched.
                if not job.zip_path or not Path(job.zip_path).is_file() or path_identity(job.zip_path) != path_identity(target):
                    stale.append(job.id)
                    continue
            elif released_terminal(job) or (
                not Path(job.source_path).is_file() and not has_work(job)
            ):
                stale.append(job.id)
                continue
            claims[path_identity(target)].append(job)
        conflicts = {}
        for group in claims.values():
            owner = min(group,key=lambda j:(j.state is not JobState.COMPLETED, not has_work(j),j.created_at,j.id))
            for job in group:
                if job.id != owner.id and job.state is not JobState.COMPLETED:
                    conflicts[job.id] = owner.id
        for job in values:
            if job.id in aliases:
                continue
            owner = conflicts.get(job.id)
            if owner:
                if job.error_code != 'OUTPUT_NAME_COLLISION':
                    job.output_resume_state = job.state.value
                job.output_conflict_job_id = owner
                job.error_code = 'OUTPUT_NAME_COLLISION'
                job.error_message = f'出力名が他の有効jobと競合しています。競合job ID：{owner}。他のjobを継続します。'
                job.failure_class = 'OUTPUT_BLOCKED'
                job.state = JobState.FAILED
                job.runtime_reason = 'OUTPUT_BLOCKED'
            else:
                restore_collision(job)
        changed = []
        for job in values:
            if job.to_dict() != before[job.id]:
                changed.append(job.id)
                if save:
                    save(job)
                else:
                    from .deferred_state import isolate_auxiliary_save
                    with isolate_auxiliary_save(jobs,job):
                        jobs.save(job)
        return dict(changed=changed, duplicate_references=aliases, conflicts=conflicts,
                    stale_owners=stale, unreadable_sources=unreadable_sources, jobs=len(values))
