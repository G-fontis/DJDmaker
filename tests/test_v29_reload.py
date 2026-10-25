import json
from pathlib import Path
import threading
import time
from djd_maker.core.models import Job
from djd_maker.core.repositories import JobRepository
from djd_maker.core.sequence_lock import sequence_lock
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.core.settings import AppSettings
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from test_gui_pipeline_controller import Pipeline


def service(tmp_path):
    repo=JobRepository(tmp_path/'system/jobs')
    instance=GuiPipelineController(jobs=repo,settings=AppSettings(input_directory='input'),app_root=tmp_path,
        pipeline=Pipeline(repo),scheduler=PersistentPollScheduler(repo))
    (tmp_path/'input').mkdir();return instance,repo


def test_read_only_reload_during_write_does_not_lock_or_allocate(tmp_path):
    s,r=service(tmp_path);p=tmp_path/'input/a.txt';p.write_text('fixture');j=Job(str(p));r.save(j)
    with sequence_lock(r._number_path):
        before=r._number_path.read_bytes()
        assert len(s.reload())==1 and r._number_path.read_bytes()==before
    assert not s._number_wait_pending.is_set();s.shutdown()


def test_live_owner_enters_non_error_wait_then_release_auto_resumes(tmp_path):
    s,r=service(tmp_path);r.ensure_job_numbers()
    ready=threading.Event();release=threading.Event();statuses=[];errors=[]
    s.bind(jobs=lambda v:None,status=statuses.append,log=lambda v:None,error=lambda *v:errors.append(v))
    def hold():
        with sequence_lock(r._number_path):ready.set();release.wait(10)
    owner=threading.Thread(target=hold);owner.start();assert ready.wait(2)
    (tmp_path/'input/a.txt').write_text('fixture')
    try:
        assert s.reload()==[]
        assert s._number_wait_pending.is_set() and not errors and s._stop_reason is None
        assert s.status()['runtime']['stage']=='WAITING_FOR_JOBNO_LOCK'
    finally:release.set();owner.join()
    deadline=time.monotonic()+8
    while (not r.list() or s._number_wait_pending.is_set()) and time.monotonic()<deadline:time.sleep(.05)
    jobs=r.list();assert len(jobs)==1 and jobs[0].job_no==1 and jobs[0].state.value=='WAITING'
    assert not s._number_wait_pending.is_set() and not errors
    assert s.status()['runtime']['stage']=='sequence.ready'
    s.shutdown()


def test_startup_migration_idempotent_preserves_job_bytes(tmp_path):
    s,r=service(tmp_path);j=Job('a.txt');r.save(j)
    before={p:p.read_bytes() for p in r.directory.glob('*.json')}
    for _ in range(3):r.ensure_job_numbers()
    assert all(p.read_bytes()==v for p,v in before.items());s.shutdown()


def test_pipeline_does_not_finish_while_import_waits_then_continues(tmp_path):
    s,r=service(tmp_path);r.ensure_job_numbers();ready=threading.Event();release=threading.Event()
    def hold():
        with sequence_lock(r._number_path):ready.set();release.wait(15)
    owner=threading.Thread(target=hold);owner.start();assert ready.wait(2)
    (tmp_path/'input/a.txt').write_text('fixture')
    try:
        s.reload();s.start();time.sleep(.1)
        assert s.status()['running'] and s._stop_reason is None
    finally:release.set();owner.join()
    deadline=time.monotonic()+8
    while (not r.list() or r.list()[0].state.value!='COMPLETED') and time.monotonic()<deadline:time.sleep(.05)
    assert len(r.list())==1 and r.list()[0].state.value=='COMPLETED'
    s.shutdown()
