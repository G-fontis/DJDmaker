"""Monotonic repository numbers, independent of identity and row ordering."""
from pathlib import Path


def valid_number(value):
    return type(value) is int and value > 0


class JobNumbering:
    @property
    def _number_path(self):
        return self.directory.parent / 'job-number-sequence.json'

    def _number_document(self):
        from .repositories import _VersionedDocument
        return _VersionedDocument(self._number_path, 'job_number_sequence', use_file_lock=False)

    def _number_floor(self):
        # Include saved tombstones so a deleted highest number is never reused.
        jobs, errors = self.list_with_errors()
        if errors:
            raise ValueError('JOB_NUMBER_SEQUENCE_UNREADABLE_JOBS: ' + ','.join(errors))
        values = [job.job_no for job in jobs]
        values += [job.job_no for job in self.completed_checkpoint_history()]
        return max([n for n in values if valid_number(n)] + [0]) + 1

    def _reserve_number(self):
        from .repositories import _document_lock
        with _document_lock(self._number_path):
            return self._reserve_number_locked()

    def _reserve_number_locked(self):
        from .repositories import SCHEMA_VERSION
        doc = self._number_document()
        value = doc.load(default={'schema_version': SCHEMA_VERSION,
                                 'kind': 'job_number_sequence', 'next_job_no': 1})
        number = value.get('next_job_no')
        if not valid_number(number):
            raise ValueError('JOB_NUMBER_SEQUENCE_INVALID')
        number = max(number, self._number_floor())
        value['next_job_no'] = number + 1
        doc.save(value)  # Reserve before job save; failed saves leave safe gaps.
        return number

    def ensure_job_numbers(self):
        """Startup migration: patch ONLY job_no; leave every other field intact."""
        from .repositories import _document_lock, SCHEMA_VERSION
        with _document_lock(self._number_path):
            jobs, errors = self.list_with_errors()
            if errors:
                raise ValueError('JOB_NUMBER_MIGRATION_UNREADABLE_JOBS: ' + ','.join(errors))
            doc = self._number_document()
            value = doc.load(default={'schema_version': SCHEMA_VERSION,
                                     'kind': 'job_number_sequence', 'next_job_no': 1})
            if not valid_number(value.get('next_job_no')):
                raise ValueError('JOB_NUMBER_SEQUENCE_INVALID')
            next_no = max(value['next_job_no'], self._number_floor())
            used = set()
            changes = []
            for job in sorted(jobs, key=lambda j: (j.created_at, j.id)):
                if valid_number(job.job_no) and job.job_no not in used:
                    used.add(job.job_no)
                    continue
                changes.append((job, next_no))
                used.add(next_no)
                next_no += 1
            value['next_job_no'] = next_no
            if changes or not doc.path.exists() or doc.load()['next_job_no'] != next_no:
                doc.save(value)
            for job, number in changes:
                document = self._document(job.id)
                def patch(payload, number=number):
                    payload['job']['job_no'] = number
                    return payload
                document.update({}, patch)
            return {job.id: number for job, number in changes}
