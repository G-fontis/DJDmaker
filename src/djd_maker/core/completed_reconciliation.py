"""Remove obsolete queue records only after verifying completed HLS output.

Media and remote objects are read-only. The repository archives every removed
record and records a tombstone so reloading the input cannot resurrect it.
"""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, BadZipFile


def validated_output(job, output_directory):
    from .output_ownership import path_identity
    if not job.zip_path:
        return False
    package = Path(job.zip_path)
    if path_identity(package) != path_identity(Path(output_directory)/(job.script_name+'.zip')):
        return False
    try:
        with ZipFile(package) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names) or archive.testzip() is not None:
                return False
            playlists = [name for name in names if name.endswith('.m3u8')]
            if len(playlists) != 1:
                return False
            lines = archive.read(playlists[0]).decode('utf-8-sig').splitlines()
            if not lines or lines[0].strip() != '#EXTM3U' or '#EXT-X-ENDLIST' not in lines:
                return False
            segments = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
            if not segments or not any(line.startswith('#EXTINF:') for line in lines):
                return False
            for segment in segments:
                relative = PurePosixPath(segment)
                if relative.is_absolute() or '..' in relative.parts or ':' in segment or '\\' in segment:
                    return False
                name = (PurePosixPath(playlists[0]).parent/relative).as_posix()
                if name not in names or archive.getinfo(name).file_size <= 0:
                    return False
        return True
    except (OSError, ValueError, KeyError, UnicodeError, RuntimeError, BadZipFile):
        return False


def reconcile_completed_duplicates(repository, output_directory):
    from .models import JobState
    from .output_ownership import same_source, has_work, source_digest
    from .deferred_state import store_for
    archive = getattr(repository, 'archive_redundant_job', None)
    if not callable(archive):
        return {'removed': [], 'unverified': []}
    values = repository.list()
    completed = [job for job in values if job.state is JobState.COMPLETED]
    history = getattr(repository, 'completed_checkpoint_history', None)
    if callable(history):
        completed.extend(history(output_directory))
    removed, unverified = [], []
    checked = {}
    deferred = store_for(repository).entries
    for job in values:
        if (job.state not in {JobState.WAITING, JobState.FAILED} or has_work(job)
                or job.id in deferred or job.edited_path or job.hls_checkpoint_directory
                or job.zip_checkpoint_path):
            continue
        matches = {owner.id:owner for owner in completed if owner.id != job.id and same_source(owner,job)}
        if len(matches) != 1:
            continue
        owner = next(iter(matches.values()))
        if owner.runtime_reason == 'LEGACY_COMPLETED_TOMBSTONE':
            # Never discard a newly supplied or independently hashed input on
            # the strength of a path-only historical record.
            if (job.state is not JobState.FAILED or job.source_sha256
                    or Path(job.source_path).exists()
                    or job.error_code not in {'NOTEBOOK_STAGE_FAILED', 'OUTPUT_NAME_COLLISION', 'LOCAL_SOURCE_FILE_MISSING'}):
                unverified.append(job.id)
                continue
            if any(other.id != job.id and other.script_name == job.script_name
                   and not same_source(other, job) for other in values):
                unverified.append(job.id)
                continue
        if Path(job.source_path).is_file():
            try:
                if not owner.source_sha256 or source_digest(job.source_path) != owner.source_sha256:
                    unverified.append(job.id)
                    continue
            except OSError:
                unverified.append(job.id)
                continue
        if job.preset_body_sha256 and owner.preset_body_sha256 and job.preset_body_sha256 != owner.preset_body_sha256:
            continue
        if owner.id not in checked:
            from .artifact_ownership import owned_zip
            checked[owner.id] = owned_zip(owner, Path(output_directory)/(owner.script_name+'.zip'), values)
        if not checked[owner.id]:
            unverified.append(job.id)
            continue
        try:
            archive(job, owner)
            removed.append(job.id)
        except (OSError, ValueError):
            unverified.append(job.id)
    return {'removed': removed, 'unverified': unverified}
