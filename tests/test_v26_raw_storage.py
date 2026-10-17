"""Single user RAW ownership, atomic publication and explicit legacy migration."""
from pathlib import Path
import pytest
from djd_maker.core.models import Job, JobState
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from test_pipeline import MemoryJobs, coordinator


def pipeline(tmp_path, *, manual=False):
    source=tmp_path/'lesson.txt';source.write_text('fixture',encoding='utf8')
    fixture=tmp_path/'remote-fixture.mp4'
    with fixture.open('wb') as stream:stream.truncate(11*1024**2)
    job=Job(str(source),state=JobState.DOWNLOADING,manual_retry_requested=manual)
    job.ending_enabled=job.tail_cut_enabled=job.encode_enabled=False
    job.hls_zip_enabled=False
    jobs=MemoryJobs(job)
    notebook=FakeNotebookAdapter({str(source):fixture})
    return coordinator(tmp_path,jobs,notebook),job,notebook


def test_raw_saved_only_in_user_directory_and_temp_consumed(tmp_path):
    pipe,job,notebook=pipeline(tmp_path)
    pipe.run_cycle()
    saved=pipe.jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.raw_path)==pipe._raw_path(job)
    assert len(list(pipe.paths.raw_directory.rglob('*.mp4')))==1
    assert not list(pipe.paths.work_directory.rglob('*.mp4'))
    assert len(notebook.download_calls)==1


def test_restart_canonical_raw_never_redownloads_or_creates_work_raw(tmp_path):
    pipe,job,notebook=pipeline(tmp_path)
    raw=pipe._raw_path(job);raw.parent.mkdir();raw.write_bytes(b'owned-valid')
    job.raw_path=str(raw);pipe.jobs.save(job)
    pipe.run_cycle()
    assert pipe.jobs.get(job.id).state is JobState.COMPLETED
    assert notebook.download_calls==[] and not list(pipe.paths.work_directory.rglob('*.mp4'))


def test_completed_does_not_duplicate_raw(tmp_path):
    pipe,job,notebook=pipeline(tmp_path);pipe.run_cycle()
    before={p:p.stat().st_size for p in pipe.paths.raw_directory.rglob('*') if p.is_file()}
    pipe.run_cycle()
    assert before=={p:p.stat().st_size for p in pipe.paths.raw_directory.rglob('*') if p.is_file()}
    assert len(notebook.download_calls)==1


@pytest.mark.parametrize('flags',[(True,False,False),(False,True,False),(False,False,True)])
def test_tail_ending_encode_receive_canonical_raw(tmp_path,flags):
    pipe,job,notebook=pipeline(tmp_path)
    from types import SimpleNamespace
    calls=[]
    class Processor:
        def process_options(self,raw,ending,output,**options):
            calls.append(raw);output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(b'processed');return SimpleNamespace(path=output)
    pipe.ending=Processor()
    job.ending_enabled,job.tail_cut_enabled,job.encode_enabled=flags
    pipe.jobs.save(job);pipe.run_cycle()
    assert calls==[pipe._raw_path(job)]
    assert pipe.jobs.get(job.id).state is JobState.COMPLETED


def test_manual_retry_raw_and_backups_stay_on_raw_volume(tmp_path):
    pipe,job,notebook=pipeline(tmp_path,manual=True)
    pipe.run_cycle()
    saved=pipe.jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.raw_path)==pipe._raw_path(job)
    assert not list(pipe.paths.work_directory.rglob('*.mp4'))
    assert list(pipe.paths.raw_directory.rglob('*.mp4'))==[pipe._raw_path(job)]


def legacy(tmp_path):
    pipe,job,notebook=pipeline(tmp_path)
    old=pipe.paths.work_directory/job.id/'download'/'lesson.mp4'
    old.parent.mkdir(parents=True);old.write_bytes(b'old-verified-raw')
    job.raw_path=str(old);job.raw_storage_version=1;job.download_status='DOWNLOADED'
    job.state=JobState.RAW_READY;pipe.jobs.save(job)
    return pipe,job,old


def test_legacy_work_raw_migrates_once_and_is_never_deleted(tmp_path):
    pipe,job,old=legacy(tmp_path)
    before=old.read_bytes()
    assert pipe._canonical_raw(job)==pipe._raw_path(job)
    assert old.read_bytes()==before and pipe._raw_path(job).read_bytes()==before
    pipe.jobs.save(job)
    assert pipe._canonical_raw(job)==pipe._raw_path(job)
    assert old.read_bytes()==before


def test_user_raw_wins_without_reading_or_copying_legacy(tmp_path):
    pipe,job,old=legacy(tmp_path)
    canonical=pipe._raw_path(job);canonical.parent.mkdir();canonical.write_bytes(b'user-valid')
    assert pipe._canonical_raw(job)==canonical
    assert canonical.read_bytes()==b'user-valid' and old.read_bytes()==b'old-verified-raw'


@pytest.mark.parametrize('unbound',['wrong_job','wrong_name','outside_work','new_schema','no_checkpoint'])
def test_unrelated_work_mp4_never_migrates(tmp_path,unbound):
    pipe,job,old=legacy(tmp_path)
    if unbound=='wrong_job':job.id='another-job'
    elif unbound=='wrong_name':job.source_path='other.txt'
    elif unbound=='outside_work':job.raw_path=str(tmp_path/'unrelated.mp4');Path(job.raw_path).write_bytes(b'other')
    elif unbound=='new_schema':job.raw_storage_version=2
    else:job.raw_path=None;job.download_status='PENDING'
    assert pipe._canonical_raw(job) is None
    assert old.is_file() and not pipe._raw_path(job).exists()


def test_legacy_raw_shared_by_another_job_is_not_migrated(tmp_path):
    pipe,job,old=legacy(tmp_path)
    other=Job('other.txt',raw_path=str(old));pipe.jobs.save(other)
    assert pipe._canonical_raw(job) is None and old.is_file()


def test_new_schema_never_falls_back_to_old_work_download(tmp_path):
    pipe,job,old=legacy(tmp_path);job.raw_storage_version=2
    assert pipe._canonical_raw(job) is None
    assert old.is_file()


def test_raw_directory_under_work_is_rejected_before_download(tmp_path):
    pipe,job,notebook=pipeline(tmp_path)
    from dataclasses import replace
    pipe.paths=replace(pipe.paths,raw_directory=pipe.paths.work_directory/'raw')
    with pytest.raises(ValueError,match='UNDER_WORK'):pipe._download_path(job)
    assert notebook.download_calls==[]


def test_old_json_gets_legacy_version_but_new_jobs_use_single_storage():
    job=Job('source');data=job.to_dict();data.pop('raw_storage_version')
    assert job.raw_storage_version==2 and Job.from_dict(data).raw_storage_version==1


@pytest.fixture
def real_raw_store(monkeypatch):
    import subprocess,json,shutil
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    payload={'format':{'duration':'2'},'streams':[{'codec_type':'video','width':320,'height':240}]}
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a[0],0,json.dumps(payload),''))
    return RawSafeStore(VideoValidator(shutil.which('python'),stability_interval_seconds=0))


def test_atomic_raw_publish_consumes_candidate_without_copy(tmp_path,monkeypatch,real_raw_store):
    import shutil
    source=tmp_path/'.djd_download_tmp'/'candidate.mp4';source.parent.mkdir()
    source.write_bytes(b'new-video');inode=source.stat().st_ino
    target=tmp_path/'formal.mp4'
    monkeypatch.setattr(shutil,'copy2',lambda *a:pytest.fail('RAW publish must not copy'))
    result=real_raw_store.publish(source,target)
    assert not source.exists() and target.stat().st_ino==inode
    assert not result.safety_gate.failed_checks


def test_raw_publish_race_preserves_other_destination_and_candidate(tmp_path,monkeypatch,real_raw_store):
    import os
    source=tmp_path/'candidate.mp4';source.write_bytes(b'new-video')
    target=tmp_path/'formal.mp4'
    original=os.link
    def race(a,b):
        target.write_bytes(b'old-video')
        return original(a,b)
    monkeypatch.setattr(os,'link',race)
    with pytest.raises(FileExistsError):real_raw_store.publish(source,target)
    assert source.read_bytes()==b'new-video' and target.read_bytes()==b'old-video'


def test_existing_raw_collision_never_deletes_good_raw(tmp_path,real_raw_store):
    source=tmp_path/'candidate.mp4';source.write_bytes(b'new-video')
    target=tmp_path/'formal.mp4';target.write_bytes(b'old-video')
    with pytest.raises(FileExistsError):real_raw_store.publish(source,target)
    assert source.read_bytes()==b'new-video' and target.read_bytes()==b'old-video'


def test_crash_after_publish_recovers_only_matching_durable_sha(tmp_path):
    from djd_maker.core.artifact_ownership import _digest
    pipe,job,notebook=pipeline(tmp_path)
    raw=pipe._raw_path(job);raw.parent.mkdir();raw.write_bytes(b'published')
    job.raw_publish_sha256=_digest(raw)
    assert pipe._canonical_raw(job,recover=True)==raw
    job.raw_path=None;job.raw_publish_sha256='0'*64
    assert pipe._canonical_raw(job,recover=True) is None


def test_legacy_path_is_not_displayed_as_canonical_raw(tmp_path,qtbot):
    from djd_maker.gui.dialogs import JobDetailDialog
    from PySide6.QtWidgets import QLabel
    pipe,job,old=legacy(tmp_path)
    dialog=JobDetailDialog(job);qtbot.addWidget(dialog)
    texts=[label.text() for label in dialog.findChildren(QLabel)]
    assert str(old) not in texts and '旧RAWの移行待ち' in texts


def test_hls_from_owned_completed_mp4_does_not_require_second_raw(tmp_path):
    from djd_maker.core.final_mp4 import queue_hls_from_mp4
    pipe,job,notebook=pipeline(tmp_path);pipe.run_cycle()
    saved=pipe.jobs.get(job.id)
    Path(saved.raw_path).unlink()  # Isolated fixture: simulate an unavailable RAW.
    queued=queue_hls_from_mp4(saved,pipe.paths.output_directory,pipe.validator,pipe.jobs)
    pipe._run_hls_zip_enabled=True
    pipe._run_media_job(queued)
    assert pipe.jobs.get(job.id).state is JobState.COMPLETED
    assert len(notebook.download_calls)==1
    assert not list(pipe.paths.raw_directory.rglob('*.mp4'))


def test_fresh_manual_retry_cleans_only_previous_owned_temporary_candidates(tmp_path):
    pipe,job,remote=pipeline(tmp_path,manual=True)
    job.notebook_id='test';job.notebook_url='https://notebook.google.com/notebook/test'
    remote.status_by_job[job.id]='READY'
    staged=pipe._download_path(job);staged.parent.mkdir(parents=True)
    staged.write_bytes(b'previous-new-candidate')
    attempt=staged.with_name('.lesson.attempt-1.mp4');attempt.write_bytes(b'incomplete')
    job.download_candidate_path=str(attempt)
    canonical=pipe._raw_path(job);canonical.write_bytes(b'existing-good-raw')
    unrelated=staged.parent/'backup-kept.mp4';unrelated.write_bytes(b'rollback-good-raw')
    pipe._prepare_manual_save_retry(job,(False,False,False,False))
    assert not staged.exists() and not attempt.exists()
    assert canonical.read_bytes()==b'existing-good-raw' and unrelated.read_bytes()==b'rollback-good-raw'


@pytest.mark.parametrize('claim',['path','sha'])
def test_quota_local_drain_recovers_canonical_without_work_download(tmp_path,claim):
    from djd_maker.core.cloud_limit import parse_limit_text
    from djd_maker.core.artifact_ownership import _digest
    from test_v125_limit_pause_contract import NOW
    pipe,job,remote=pipeline(tmp_path)
    raw=pipe._raw_path(job);raw.parent.mkdir();raw.write_bytes(b'owned-valid-raw')
    if claim=='path':job.raw_path=str(raw)
    else:job.raw_publish_sha256=_digest(raw)
    pipe.jobs.save(job)
    pipe.cloud_limit.clock=lambda:NOW
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効',now=NOW))
    pipe.run_cycle()
    assert pipe.jobs.get(job.id).state is JobState.COMPLETED
    assert not remote.download_calls and pipe.cloud_limit.blocked
