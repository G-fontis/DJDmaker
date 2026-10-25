"""Monotonic reservations; job scans and job publish never hold sequence lock."""
from contextvars import ContextVar
import json
import os
import threading
from .sequence_lock import sequence_lock, SequenceUnavailable, RETRY_DELAYS

_allocation = ContextVar('number_allocation', default=None)


def valid_number(value):
    return type(value) is int and value > 0


class JobNumbering:
    @property
    def _number_path(self):
        return self.directory.parent / 'job-number-sequence.json'

    def _number_document(self):
        from .repositories import _VersionedDocument
        return _VersionedDocument(self._number_path,'job_number_sequence',use_file_lock=False)

    def _number_snapshot(self):
        jobs,errors=self.list_with_errors()
        if errors:raise ValueError('JOB_NUMBER_SEQUENCE_UNREADABLE_JOBS: '+','.join(errors))
        history=self.completed_checkpoint_history()
        for p in (self.directory.parent/'removed-job-records').glob('*.json'):
            value=json.loads(p.read_text(encoding='utf8'))
            if valid_number(value.get('job',{}).get('job_no')):
                from .models import Job
                history.append(Job.from_dict(value['job']))
        pending=[]
        store=getattr(self,'_deferred_state',None)
        if store:
            pending=[entry.snapshot.get('job_no') for entry in store.entries.copy().values() if entry.snapshot]
        floor=max([j.job_no for j in jobs+history if valid_number(j.job_no)]+
                  [n for n in pending if valid_number(n)]+[0])+1
        return jobs,floor

    def _number_floor(self):
        return self._number_snapshot()[1]

    def _sequence_value(self,floor):
        from .repositories import SCHEMA_VERSION,UnsupportedSchemaError
        doc=self._number_document();values=[]
        for p in (doc.path,doc.backup_path):
            try:value=json.loads(p.read_text(encoding='utf8'))
            except (FileNotFoundError,UnicodeError,json.JSONDecodeError):continue
            if not isinstance(value,dict):continue
            version=value.get('schema_version')
            if version is not None and (type(version) is not int or version>SCHEMA_VERSION):
                raise UnsupportedSchemaError('JOB_NUMBER_SEQUENCE_SCHEMA_UNSUPPORTED')
            if version!=SCHEMA_VERSION or value.get('kind')!='job_number_sequence' or not valid_number(value.get('next_job_no')):continue
            values.append(value)
        result={'schema_version':SCHEMA_VERSION,'kind':'job_number_sequence','next_job_no':floor,'assignments':{}}
        for value in values:
            result['next_job_no']=max(result['next_job_no'],value['next_job_no'])
            assignments=value.get('assignments',{})
            if not isinstance(assignments,dict):raise ValueError('JOB_NUMBER_ASSIGNMENT_INVALID')
            for jid,n in assignments.items():
                if not valid_number(n):raise ValueError('JOB_NUMBER_ASSIGNMENT_INVALID')
                old=result['assignments'].get(jid)
                if old is not None and old!=n:raise ValueError('JOB_NUMBER_ASSIGNMENT_CONFLICT')
                result['assignments'][jid]=n
                result['next_job_no']=max(result['next_job_no'],n+1)
        if len(set(result['assignments'].values()))!=len(result['assignments']):
            raise ValueError('JOB_NUMBER_ASSIGNMENT_CONFLICT')
        return result

    def _write_sequence(self,value):
        from .storage import JsonStore
        doc=self._number_document();tag=f'sequence-{os.getpid()}-{threading.get_ident()}'
        JsonStore(doc.path,temporary_tag=tag).save(value)
        JsonStore(doc.backup_path,temporary_tag=tag).save(value) # One fixed current high-water backup.

    def _sequence_update(self,action):
        from .cancellation import interruptible_sleep
        from .storage import is_transient_replace_error
        for attempt in range(len(RETRY_DELAYS)+1):
            try:
                with sequence_lock(self._number_path,delays=()):return action()
            except SequenceUnavailable as exc:last=exc
            except OSError as exc:
                if not is_transient_replace_error(exc):raise
                last=SequenceUnavailable(self._number_path,'temporary file busy: '+str(exc))
            if attempt<len(RETRY_DELAYS):interruptible_sleep(RETRY_DELAYS[attempt])
        raise last

    def _reserve_number(self,job_id=None):
        jobs,floor=self._number_snapshot() # All disk scans OUTSIDE sequence lock.
        token=_allocation.set((job_id,floor))
        try:return self._sequence_update(self._reserve_number_locked)
        finally:_allocation.reset(token)

    def _reserve_number_locked(self):
        job_id,floor=_allocation.get()
        value=self._sequence_value(floor)
        number=value['assignments'].get(job_id) if job_id else None
        if number is None:
            number=value['next_job_no'];value['next_job_no']=number+1
            if job_id:value['assignments'][job_id]=number
        self._write_sequence(value)
        return number

    def ensure_job_numbers(self):
        jobs,floor=self._number_snapshot()
        used=set();missing=[]
        for job in sorted(jobs,key=lambda j:(j.created_at,j.id)):
            if valid_number(job.job_no) and job.job_no not in used:used.add(job.job_no)
            else:missing.append(job)
        if not missing:
            try:
                value=json.loads(self._number_path.read_text(encoding='utf8'))
                if isinstance(value,dict) and value.get('kind')=='job_number_sequence' and value.get('schema_version')==1 and valid_number(value.get('next_job_no')) and value['next_job_no']>=floor:
                    return {} # Healthy startup/display needs NO sequence write lock.
            except (FileNotFoundError,UnicodeError,json.JSONDecodeError):pass
        def prepare():
            value=self._sequence_value(floor);changes={};reserved=set(used)
            for job in missing:
                number=value['assignments'].get(job.id)
                if number is None or number in reserved:
                    number=value['next_job_no'];value['next_job_no']=number+1
                value['assignments'][job.id]=number;reserved.add(number);changes[job.id]=number
            self._write_sequence(value)
            return changes
        changes=self._sequence_update(prepare)
        for jid,number in changes.items():
            def patch(payload,number=number):payload['job']['job_no']=number;return payload
            self._document(jid).update({},patch) # No job write/retry under sequence lock.
        return changes
