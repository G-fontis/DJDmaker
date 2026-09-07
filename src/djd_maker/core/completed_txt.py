"""Archive a completed job's source without overwriting another TXT."""
from hashlib import sha256
import os
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .models import JobState
from .repositories import _thread_lock


def reconcile_completed_txt(jobs, raw_directory: Path) -> int:
    with _thread_lock(raw_directory / "txt-move"):
        return _reconcile_completed_txt(jobs, raw_directory)


def _reconcile_completed_txt(jobs, raw_directory: Path) -> int:
    moved = 0
    for job in jobs.list():
        if job.state is not JobState.COMPLETED or job.txt_move_status == "MOVED":
            continue
        try:
            source = Path(job.source_path)
            destination = raw_directory / source.name
            if source.suffix.casefold() != ".txt" or not job.zip_path:
                raise ValueError("TXT_MOVE_IDENTITY_UNVERIFIED")
            archive_path = Path(job.zip_path)
            if archive_path.stem.casefold() != source.stem.casefold():
                raise ValueError("TXT_MOVE_IDENTITY_UNVERIFIED")
            with ZipFile(archive_path) as archive:
                if not archive.namelist() or archive.testzip() is not None:
                    raise ValueError("TXT_MOVE_ZIP_INVALID")
            if not source.is_file():
                if not destination.is_file() or not job.source_sha256:
                    raise ValueError("TXT_MOVE_SOURCE_MISSING")
                if sha256(destination.read_bytes()).hexdigest() != job.source_sha256:
                    raise ValueError("TXT_MOVE_COLLISION")
            else:
                payload = source.read_bytes()
                digest = sha256(payload).hexdigest()
                if job.source_sha256 and job.source_sha256 != digest:
                    raise ValueError("TXT_MOVE_SOURCE_CHANGED")
                job.source_sha256 = digest
                jobs.save(job)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if destination.read_bytes() != payload:
                        raise ValueError("TXT_MOVE_COLLISION")
                else:
                    with destination.open("xb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                if destination.read_bytes() != payload or source.read_bytes() != payload:
                    raise ValueError("TXT_MOVE_VERIFY_FAILED")
                if source.resolve() != destination.resolve():
                    source.unlink()
            job.archived_txt_path = str(destination.resolve())
            job.txt_move_status = "MOVED"
            moved += 1
        except (OSError, ValueError, BadZipFile) as exc:
            job.txt_move_status = "TXT_MOVE_COLLISION" if "COLLISION" in str(exc) else "TXT_MOVE_PENDING"
        jobs.save(job)
    return moved
