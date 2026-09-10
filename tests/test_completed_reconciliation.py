import json
from hashlib import sha256
from zipfile import ZipFile
import pytest
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobRepository
from djd_maker.core.output_ownership import reconcile_output_ownership


def setup_jobs(tmp_path):
    output=tmp_path/'output';output.mkdir()
    package=output/'lesson.zip'
    with ZipFile(package,'w') as archive:
        archive.writestr('playlist.m3u8','#EXTM3U\n#EXTINF:6,\nsegment000.ts\n#EXT-X-ENDLIST\n')
        archive.writestr('segment000.ts',b'test segment')
    source=tmp_path/'lesson.txt'
    completed=Job(str(source),state=JobState.COMPLETED,zip_path=str(package),
                  source_sha256=sha256(b'original source').hexdigest(),
                  output_zip_sha256=sha256(package.read_bytes()).hexdigest())
    redundant=Job(str(source),state=JobState.FAILED,error_code='NOTEBOOK_STAGE_FAILED')
    repo=JobRepository(tmp_path/'system/jobs')
    repo.save(completed);repo.save(redundant)
    return repo,output,completed,redundant,package


def test_valid_output_archives_only_redundant_job_and_prevents_restart_resurrection(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    original=package.read_bytes()
    report=reconcile_output_ownership(repo,output)
    assert report['completed_reconciliation']['removed']==[redundant.id]
    assert repo.get(redundant.id) is None
    assert repo.get(completed.id).state is JobState.COMPLETED
    assert package.read_bytes()==original
    archived=repo.directory.parent/'removed-job-records'/f'{redundant.id}.json'
    assert json.loads(archived.read_text(encoding='utf-8'))['job']['id']==redundant.id
    restarted=JobRepository(repo.directory)
    assert redundant.source_path in restarted.deleted_source_paths()
    assert restarted.get(redundant.id) is None


def test_missing_completed_output_reenters_recovery_not_false_completion(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    package.unlink()
    reconcile_output_ownership(repo,output)
    recovered=repo.get(completed.id)
    assert recovered.state is JobState.WAITING
    assert recovered.zip_path is None
    assert recovered.runtime_reason=='OUTPUT_MISSING_RECOVER_CHECKPOINT'
    assert repo.get(redundant.id) is not None


@pytest.mark.parametrize('valid',[True,False])
def test_existing_unowned_output_never_regenerates_or_overwrites(tmp_path,valid):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    from test_pipeline import MemoryJobs
    jobs=MemoryJobs(redundant)
    if not valid:package.write_bytes(b'broken')
    original=package.read_bytes()
    reconcile_output_ownership(jobs,output)
    blocked=jobs.get(redundant.id)
    assert blocked.failure_class=='OUTPUT_BLOCKED'
    assert blocked.error_code==('OUTPUT_ALREADY_EXISTS' if valid else 'OUTPUT_EXISTING_UNVERIFIED')
    assert package.read_bytes()==original
    reconcile_output_ownership(jobs,output)
    assert jobs.get(redundant.id).failure_class=='OUTPUT_BLOCKED'
    package.unlink()
    reconcile_output_ownership(jobs,output)
    assert jobs.get(redundant.id).state is JobState.WAITING
    assert jobs.get(redundant.id).error_code is None


def test_verified_owned_zip_clears_error_without_generation(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    from test_pipeline import MemoryJobs
    completed.state=JobState.FAILED
    completed.error_code='NOTEBOOK_STAGE_FAILED'
    completed.hls_result='PASS'
    jobs=MemoryJobs(completed)
    reconcile_output_ownership(jobs,output)
    result=jobs.get(completed.id)
    assert result.state is JobState.COMPLETED
    assert result.error_code is None
    assert result.remote_checkpoint=='LOCAL_ZIP_VALIDATED'


def test_existing_output_blocks_human_resume_and_scheduler_submission(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    from test_pipeline import MemoryJobs, coordinator
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    jobs=MemoryJobs(redundant)
    notebook=FakeNotebookAdapter({})
    pipeline=coordinator(tmp_path,jobs,notebook)
    original=package.read_bytes()
    assert pipeline.resume_failed_jobs()==[]
    pipeline.run_cycle()
    assert notebook.submit_calls==[]
    assert jobs.get(redundant.id).failure_class=='OUTPUT_BLOCKED'
    assert package.read_bytes()==original


@pytest.mark.parametrize('broken',['absent','bad-zip','missing-segment','unfinished-playlist','different-content','remote-work'])
def test_unverified_or_independent_job_is_not_deleted(tmp_path,broken):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    if broken=='absent':package.unlink()
    elif broken=='bad-zip':package.write_bytes(b'broken')
    elif broken=='missing-segment':
        with ZipFile(package,'w') as archive:archive.writestr('playlist.m3u8','#EXTM3U\n#EXTINF:6,\nmissing.ts\n#EXT-X-ENDLIST\n')
    elif broken=='unfinished-playlist':
        with ZipFile(package,'w') as archive:
            archive.writestr('playlist.m3u8','#EXTM3U\n#EXTINF:6,\ns.ts\n');archive.writestr('s.ts',b'content')
    elif broken=='different-content':redundant.source_sha256='different';repo.save(redundant)
    elif broken=='remote-work':redundant.notebook_id='own-notebook';repo.save(redundant)
    report=reconcile_output_ownership(repo,output)
    assert not report['completed_reconciliation']['removed']
    assert repo.get(redundant.id) is not None


def test_deleted_completed_checkpoint_can_verify_later_duplicate(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    repo.delete_completed([completed.id])
    assert repo.get(completed.id) is None
    report=reconcile_output_ownership(repo,output)
    assert report['completed_reconciliation']['removed']==[redundant.id]
    assert package.is_file()


def test_changed_existing_source_is_not_deleted(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    from pathlib import Path
    Path(redundant.source_path).write_text('new content',encoding='utf-8')
    report=reconcile_output_ownership(repo,output)
    assert not report['completed_reconciliation']['removed']
    assert repo.get(redundant.id) is not None


def test_deferred_remote_identity_prevents_duplicate_removal(tmp_path):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    from djd_maker.core.deferred_state import store_for
    pending=Job.from_dict(redundant.to_dict());pending.notebook_id='remote-not-yet-durable'
    store_for(repo).record(pending,'test save failure')
    report=reconcile_output_ownership(repo,output)
    assert not report['completed_reconciliation']['removed']
    assert repo.get(redundant.id) is not None


@pytest.mark.parametrize('fresh_source',[False,True])
def test_legacy_completed_tombstone_without_digest_never_removes_duplicate(tmp_path,fresh_source):
    repo,output,completed,redundant,package=setup_jobs(tmp_path)
    repo.delete_completed([completed.id])
    from djd_maker.core.repositories import _VersionedDocument
    document=_VersionedDocument(repo.directory.parent/'deleted_jobs.json','deleted_jobs',use_file_lock=False)
    value=document.load();value.pop('completed_checkpoints');document.save(value)
    if fresh_source:
        from pathlib import Path
        Path(redundant.source_path).write_text('new input',encoding='utf-8')
    report=reconcile_output_ownership(repo,output)
    assert not report['completed_reconciliation']['removed']
    assert package.is_file()
