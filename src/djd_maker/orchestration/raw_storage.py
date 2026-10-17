"""Canonical user RAW storage; legacy work paths are read-only migration inputs."""
from pathlib import Path
from djd_maker.core.output_ownership import path_identity


class RawStorage:
    def _raw_path(self, job):
        return self.paths.raw_directory / f'{job.script_name}.mp4'

    def _download_path(self, job):
        if Path(job.id).name != job.id or job.id in {'.', '..'}:
            raise ValueError('RAW_JOB_ID_INVALID')
        if self.paths.raw_directory.resolve().is_relative_to(self.paths.work_directory.resolve()):
            raise ValueError('RAW_DIRECTORY_UNDER_WORK_UNSUPPORTED')
        return (self.paths.raw_directory / '.djd_download_tmp' / job.id
                / f'session-{job.manual_download_session}' / f'{job.script_name}.mp4')

    def _canonical_raw(self, job, *, recover=False):
        """Adopt an owned canonical RAW or migrate one explicitly bound old file."""
        canonical = self._raw_path(job)
        saved = Path(job.raw_path) if job.raw_path else None
        others = self.jobs.list()
        if any(other.id != job.id and other.raw_path and
               path_identity(other.raw_path) == path_identity(canonical) for other in others):
            raise ValueError('OUTPUT_EXISTING_UNVERIFIED: RAW belongs to another job')
        if canonical.is_file():
            # Existence alone never establishes ownership.
            if saved is None or path_identity(saved) != path_identity(canonical):
                from djd_maker.core.models import JobState
                from djd_maker.core.artifact_ownership import _digest
                claimed = (saved is None and job.raw_publish_sha256 and
                           job.state in {JobState.DOWNLOADING,JobState.DOWNLOAD_PENDING,JobState.RECOVERY_PENDING}
                           and _digest(canonical) == job.raw_publish_sha256)
                if not claimed and (job.raw_storage_version >= 2 or not self._legacy_raw_source(job)):
                    return None
            stored = self.raw_store.verify_existing(canonical, canonical)
        else:
            legacy = self._legacy_raw_source(job)
            if legacy is None:
                return None
            if self.download_quality_profile is not None:
                from djd_maker.core.download_quality import evaluate_download
                if not evaluate_download(legacy, self.validator, self.download_quality_profile).passed:
                    raise ValueError('LEGACY_RAW_QUALITY_FAILED')
            stored = self.raw_store.save(legacy, canonical)
        if stored.safety_gate.failed_checks:
            raise ValueError('RAW safety gate failed: ' + ', '.join(stored.safety_gate.failed_checks))
        old = job.raw_path
        job.raw_path = str(canonical)
        if job.edited_path and old and path_identity(job.edited_path) == path_identity(old):
            job.edited_path = str(canonical)
        if recover or job.raw_storage_version < 2:
            job.safety_gate = stored.safety_gate
        job.raw_storage_version = 2
        return canonical

    def _legacy_raw_source(self, job):
        if job.raw_storage_version >= 2 or Path(job.id).name != job.id or job.id in {'.', '..'}:
            return None
        root = self.paths.work_directory / job.id
        download = root / 'download' / f'{job.script_name}.mp4'
        saved = Path(job.raw_path) if job.raw_path else None
        candidates = [download]
        if job.manual_download_session:
            candidates.append(root / f'manual-{job.manual_download_session}' / 'raw.mp4')
        for candidate in candidates:
            if not candidate.is_file():
                continue
            explicit = saved is not None and path_identity(saved) == path_identity(candidate)
            bound_download = (candidate == download and job.download_status == 'DOWNLOADED'
                              and (saved is None or path_identity(saved) == path_identity(self._raw_path(job))))
            if not (explicit or bound_download):
                continue
            if any(other.id != job.id and other.raw_path and
                   path_identity(other.raw_path) == path_identity(candidate) for other in self.jobs.list()):
                continue
            return candidate
        return None
