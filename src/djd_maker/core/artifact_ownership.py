"""Read-only evidence gates for adopting an already published ZIP."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile, BadZipFile

from .completed_reconciliation import validated_output
from .output_ownership import path_identity


def _digest(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value.lower())


def owned_zip(job, output, jobs):
    """Require identity, source and content evidence; uncertainty returns False.

    ``output`` is the expected final ZIP, ``jobs`` an iterable or repository.
    A source moved beside RAW/output is checked when present. Missing source is
    allowed only with its durable SHA and ZIP hash or validated HLS binding.
    No media, state or repository objects are modified.
    """
    try:
        output = Path(output)
        if output.name != job.script_name + '.zip' or not job.zip_path:
            return False
        if path_identity(job.zip_path) != path_identity(output) or not _sha(job.source_sha256):
            return False
        values = jobs.list() if callable(getattr(jobs, 'list', None)) else jobs
        for other in values:
            if other.id == job.id or getattr(other, 'duplicate_of_job_id', None) == job.id:
                continue
            if other.zip_path and path_identity(other.zip_path) == path_identity(output):
                return False
            if other.script_name == job.script_name and (
                    path_identity(other.source_path) != path_identity(job.source_path)
                    or other.source_sha256 and other.source_sha256.lower() != job.source_sha256.lower()):
                return False
        sources = {Path(job.source_path), output.with_suffix('.txt')}
        if job.raw_path:
            sources.add(Path(job.raw_path).with_suffix('.txt'))
        for source in sources:
            if source.exists() and (not source.is_file() or _digest(source) != job.source_sha256.lower()):
                return False
        if not validated_output(job, output.parent):
            return False
        bound_hash = getattr(job, 'output_zip_sha256', None)
        if bound_hash:
            return _sha(bound_hash) and _digest(output) == bound_hash.lower()
        if not job.hls_checkpoint_directory or not _sha(job.hls_source_sha256):
            return False
        video = Path(job.edited_path or job.raw_path or '')
        if not video.is_file() or _digest(video) != job.hls_source_sha256.lower():
            return False
        directory = Path(job.hls_checkpoint_directory).resolve()
        if directory.parent != output.resolve().parent or not directory.name.startswith('.' + output.stem + '.hls-'):
            return False
        from djd_maker.adapters.hls import validate_hls
        playlist, segments = validate_hls(directory)
        paths = (playlist, *segments)
        with ZipFile(output) as archive:
            if set(archive.namelist()) != {path.name for path in paths}:
                return False
            return all(sha256(archive.read(path.name)).hexdigest() == _digest(path) for path in paths)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, BadZipFile):
        return False
