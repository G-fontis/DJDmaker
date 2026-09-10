from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from djd_maker.core.models import Job, JobState
from djd_maker.core.notebook_identity import notebook_identity, recover_unique_identity
from djd_maker.orchestration.task_discovery import final_discovery
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from test_pipeline import MemoryJobs, coordinator


class Remote(FakeNotebookAdapter):
    def __init__(self, job, media, status):
        super().__init__({job.source_path: media})
        self.status = status
        self.inspections = []

    def diagnose_resume(self, job):
        self.inspections.append(job.id)
        return {'artifact': self.status, 'source': 'READY', 'preset': 'NOT_SENT'}


@pytest.mark.parametrize('legacy_class', [None, 'FATAL_FAILED', 'RECOVERY_PENDING'])
def test_missing_txt_failed_ready_downloads_without_resend(tmp_path, legacy_class):
    job = Job(str(tmp_path/'missing.txt'), state=JobState.FAILED,
              notebook_id='remote', notebook_url='https://notebook.google.com/notebook/remote',
              error_code='NOTEBOOK_STAGE_FAILED', failure_class=legacy_class)
    media = tmp_path/'fixture.mp4'; media.write_bytes(b'fixture')
    remote = Remote(job, media, 'READY')
    jobs = MemoryJobs(job); pipeline = coordinator(tmp_path, jobs, remote)
    events = []; pipeline.runtime_callback = events.append
    pipeline.run_cycle()
    result = jobs.get(job.id)
    assert result.state is JobState.COMPLETED
    assert result.error_code is None
    assert result.local_error_code == 'LOCAL_SOURCE_FILE_MISSING'
    assert result.remote_checkpoint == 'ARTIFACT_READY'
    assert remote.inspections == [job.id]
    assert remote.submit_calls == []
    assert remote.artifact_delete_calls == []
    assert result.safety_gate.remote_deletion_allowed
    assert result.artifact_status == 'RETAINED'
    assert Path(result.raw_path).read_bytes() == b'fixture'
    assert Path(result.zip_path).is_file()
    assert any(e.get('scheduler', {}).get('priority') == 3 for e in events)
    pipeline.run_cycle()
    assert remote.inspections == [job.id]
    assert remote.artifact_delete_calls == []


@pytest.mark.parametrize('status,target', [('GENERATING', JobState.WAITING_VIDEO),
                                         ('FAILED', JobState.WAITING),
                                         ('UNKNOWN', JobState.FAILED),
                                         ('NOT_STARTED', JobState.WAITING)])
def test_remote_checkpoint_repairs_failed_without_blind_action(tmp_path, status, target):
    job = Job(str(tmp_path/'missing.txt'), state=JobState.FAILED,
              notebook_id='remote', notebook_url='https://notebook.google.com/notebook/remote',
              error_code='NOTEBOOK_STAGE_FAILED')
    remote = Remote(job, tmp_path/'unused.mp4', status)
    jobs = MemoryJobs(job); pipeline = coordinator(tmp_path, jobs, remote)
    pipeline._reconcile_failed_remote(job)
    assert jobs.get(job.id).state is target
    assert remote.submit_calls == remote.artifact_delete_calls == []
    if status == 'UNKNOWN':
        assert job.error_code == 'NOTEBOOK_STATE_UNKNOWN'
        assert job.remote_checkpoint == 'ARTIFACT_UNKNOWN'
    if status == 'GENERATING':
        assert job.next_poll_at


def test_due_failed_remote_remains_unfinished_even_with_old_fatal_class():
    job = Job('missing.txt', state=JobState.FAILED, notebook_id='remote', failure_class='FATAL_FAILED')
    result = final_discovery([job], datetime.now(UTC), blocked=True)
    assert not result['complete']
    assert result['runnable'] == [job.id]
    job.next_poll_at = (datetime.now(UTC)+timedelta(hours=1)).isoformat()
    assert final_discovery([job], datetime.now(UTC), blocked=True)['waiting'] == [job.id]


@pytest.mark.parametrize('state', [JobState.RAW_READY, JobState.ENDING, JobState.HLS_ENCODING])
def test_legacy_pending_delete_is_retained_during_local_resume(tmp_path, state):
    raw = tmp_path/'raw.mp4'; raw.write_bytes(b'raw')
    job = Job('retained.txt', state=state, raw_path=str(raw), edited_path=str(raw),
              notebook_id='remote', artifact_status='DELETE_PENDING')
    jobs=MemoryJobs(job); remote=FakeNotebookAdapter({})
    pipeline=coordinator(tmp_path,jobs,remote)
    pipeline.run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert jobs.get(job.id).artifact_status == 'RETAINED'
    assert remote.artifact_delete_calls == []


ID = '143d0121-cd16-4500-84fe-d08df248a17c'
URL = 'https://notebook.google.com/notebook/'+ID


@pytest.mark.parametrize('identity,url', [(ID,None),(None,URL),(ID,URL)])
def test_notebook_id_url_partial_metadata_recovery(identity,url):
    assert notebook_identity(identity,url) == (ID,URL)


def test_unique_identity_requires_source_digest_and_rejects_ambiguity():
    job=Job('source.txt',source_sha256='known')
    candidate=Job('source.txt',source_sha256='known',notebook_id=ID,notebook_url=URL)
    assert recover_unique_identity(job,[candidate])
    other=Job('source.txt',source_sha256='known',notebook_id='dea6e161-b40a-46a1-a0be-c335af718bac')
    missing=Job('source.txt',source_sha256='known')
    assert not recover_unique_identity(missing,[candidate,other])
    assert missing.notebook_id is None
    assert not recover_unique_identity(Job('source.txt'),[candidate])


def test_normal_pipeline_has_no_delete_call():
    import ast, inspect
    from djd_maker.orchestration.pipeline import PipelineCoordinator
    tree=ast.parse(inspect.getsource(PipelineCoordinator))
    callers=[]
    for method in tree.body[0].body:
        if isinstance(method, ast.FunctionDef):
            if any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                   and n.func.attr=='delete_video_artifact' for n in ast.walk(method)):
                callers.append(method.name)
    assert callers == ['retry_remote_artifact_delete']
