import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from djd_maker.core.models import Job
from djd_maker.core.repositories import JobRepository
from djd_maker.core.sequence_lock import sequence_lock,SequenceUnavailable
from djd_maker.core.job_numbers import RETRY_DELAYS


def test_sequence_lock_released_on_success_and_exception(tmp_path):
    p=tmp_path/'sequence.json'
    with sequence_lock(p):
        with sequence_lock(p):pass
    with pytest.raises(RuntimeError):
        with sequence_lock(p):raise RuntimeError('injected')
    with ThreadPoolExecutor() as pool:
        assert pool.submit(lambda: acquired(p)).result(timeout=2)


def acquired(path):
    with sequence_lock(path):return True


def test_live_legacy_owner_never_deleted_and_bounded_retry(tmp_path):
    p=tmp_path/'sequence.json';marker=tmp_path/'.sequence.json.lock'
    original=f'pid={os.getpid()} time={time.time()}\n'.encode();marker.write_bytes(original)
    os.utime(marker,(time.time()-10000,time.time()-10000))
    sleeps=[]
    with pytest.raises(SequenceUnavailable):
        with sequence_lock(p,delays=(.01,.02),sleeper=sleeps.append):pass
    assert sleeps==[.01,.02] and marker.read_bytes()==original


def test_dead_owner_is_reclaimed_without_age_only_rule(tmp_path,monkeypatch):
    p=tmp_path/'sequence.json';marker=tmp_path/'.sequence.json.lock'
    marker.write_text('pid=123456 time=1\n')
    monkeypatch.setattr('djd_maker.core.sequence_lock.process_identity',lambda pid:(False,None))
    with sequence_lock(p):pass
    assert marker.read_bytes().startswith(b'\0')


def test_unknown_owner_is_not_reclaimed(tmp_path,monkeypatch):
    p=tmp_path/'sequence.json';marker=tmp_path/'.sequence.json.lock';marker.write_text('pid=123 time=1\n')
    monkeypatch.setattr('djd_maker.core.sequence_lock.process_identity',lambda pid:(None,None))
    with pytest.raises(SequenceUnavailable):
        with sequence_lock(p,delays=()):pass
    assert marker.read_text()=='pid=123 time=1\n'


def test_legacy_pid_reuse_or_clock_ambiguity_is_not_stolen(tmp_path,monkeypatch):
    p=tmp_path/'sequence.json';marker=tmp_path/'.sequence.json.lock';marker.write_text('pid=123 time=1\n')
    monkeypatch.setattr('djd_maker.core.sequence_lock.process_identity',lambda pid:(True,100))
    with pytest.raises(SequenceUnavailable):
        with sequence_lock(p,delays=()):pass
    assert marker.read_text()=='pid=123 time=1\n'


@pytest.mark.parametrize('bad',[None,'broken','[]','{}','{"schema_version":1,"kind":"job_number_sequence","next_job_no":0}'])
def test_missing_corrupted_sequence_rebuild_preserves_jobs(tmp_path,bad):
    repo=JobRepository(tmp_path/'jobs');jobs=[Job(f'{i}.txt') for i in range(3)]
    for j in jobs:repo.save(j)
    before={p:p.read_bytes() for p in repo.directory.glob('*.json')}
    doc=repo._number_document();doc.path.unlink();doc.backup_path.unlink()
    if bad is not None:doc.path.write_text(bad)
    assert repo.ensure_job_numbers()=={}
    assert doc.load()['next_job_no']==4
    assert all(p.read_bytes()==value for p,value in before.items())


def test_sequence_backup_keeps_deleted_high_water(tmp_path):
    repo=JobRepository(tmp_path/'jobs');j=Job('a.txt');repo.save(j)
    value=repo._number_document().load();value['next_job_no']=50
    repo._write_sequence(value)
    repo._number_path.write_text('broken')
    repo.ensure_job_numbers()
    assert repo._number_document().load()['next_job_no']==50


def test_healthy_reload_migration_does_not_take_sequence_write_lock(tmp_path,monkeypatch):
    repo=JobRepository(tmp_path/'jobs');repo.save(Job('a.txt'))
    before={p:p.read_bytes() for p in repo.directory.glob('*.json')}
    def forbidden(*a,**k):raise AssertionError('read only operation acquired sequence lock')
    monkeypatch.setattr('djd_maker.core.job_numbers.sequence_lock',forbidden)
    assert repo.ensure_job_numbers()=={}
    repo.list()
    assert all(p.read_bytes()==v for p,v in before.items())


def test_job_scan_and_publish_are_outside_lock(tmp_path,monkeypatch):
    import djd_maker.core.sequence_lock as mod
    repo=JobRepository(tmp_path/'jobs');original=repo._number_snapshot
    def scan():assert not getattr(mod._held,'paths',{});return original()
    monkeypatch.setattr(repo,'_number_snapshot',scan)
    original_doc=repo._document
    def document(jid):
        assert not getattr(mod._held,'paths',{})
        return original_doc(jid)
    monkeypatch.setattr(repo,'_document',document)
    repo.save(Job('a.txt'))


def test_transient_replace_waits_outside_sequence_lock(tmp_path,monkeypatch):
    import djd_maker.core.sequence_lock as locks
    from djd_maker.core import storage
    repo=JobRepository(tmp_path/'jobs');original=storage.os.replace;calls=[];sleeps=[]
    def busy(source,destination):
        if Path(destination)==repo._number_path:
            calls.append(1)
            if len(calls)<3:raise PermissionError('sync busy')
        return original(source,destination)
    def sleep(delay):assert not getattr(locks._held,'paths',{});sleeps.append(delay)
    monkeypatch.setattr(storage.os,'replace',busy)
    monkeypatch.setattr('djd_maker.core.cancellation.interruptible_sleep',sleep)
    j=Job('a.txt');repo.save(j)
    assert sleeps==[.05,.1] and repo.require(j.id).job_no==1


def test_retry_is_bounded_and_not_job_failed(tmp_path,monkeypatch):
    repo=JobRepository(tmp_path/'jobs');marker=repo._number_path.with_name('.job-number-sequence.json.lock')
    marker.parent.mkdir(exist_ok=True);marker.write_text(f'pid={os.getpid()} time={time.time()}\n');sleeps=[]
    monkeypatch.setattr('djd_maker.core.cancellation.interruptible_sleep',sleeps.append)
    j=Job('a.txt')
    with pytest.raises(SequenceUnavailable):repo.save(j)
    assert sleeps==list(RETRY_DELAYS) and j.state.value=='WAITING' and not list(repo.directory.glob('*.json'))


def test_concurrent_processes_allocate_unique_and_crash_releases_lock(tmp_path):
    script="from pathlib import Path;from djd_maker.core.models import Job;from djd_maker.core.repositories import JobRepository;import sys;r=JobRepository(Path(sys.argv[1]));[r.save(Job(sys.argv[2]+str(i)+'.txt')) for i in range(8)]"
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    children=[subprocess.Popen([sys.executable,'-c',script,str(tmp_path/'jobs'),str(i)],creationflags=flags) for i in range(3)]
    assert [p.wait(timeout=30) for p in children]==[0,0,0]
    numbers=[j.job_no for j in JobRepository(tmp_path/'jobs').list()]
    assert len(numbers)==len(set(numbers))==24
    crash="import os,sys;from pathlib import Path;from djd_maker.core.sequence_lock import sequence_lock\nwith sequence_lock(Path(sys.argv[1])):os._exit(7)"
    p=subprocess.run([sys.executable,'-c',crash,str(tmp_path/'crash.json')],creationflags=flags)
    assert p.returncode==7 and acquired(tmp_path/'crash.json')


def test_live_os_owner_waits_then_auto_acquire(tmp_path):
    p=tmp_path/'sequence.json';ready=threading.Event();release=threading.Event()
    def owner():
        with sequence_lock(p):ready.set();release.wait(5)
    t=threading.Thread(target=owner);t.start();assert ready.wait(2)
    try:
        with pytest.raises(SequenceUnavailable):
            with sequence_lock(p,delays=()):pass
    finally:release.set();t.join()
    assert acquired(p)


def test_old_release_failure_reproduces_live_marker_timeout(tmp_path,monkeypatch):
    from djd_maker.core.repositories import _document_lock,RepositoryLockTimeout
    p=tmp_path/'sequence.json';marker=tmp_path/'.sequence.json.lock';original=Path.unlink
    def busy(path,*args,**kwargs):
        if path==marker:raise PermissionError('controlled sync sharing failure')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',busy)
        with pytest.raises(PermissionError):
            with _document_lock(p):pass
    assert marker.is_file()
    with pytest.raises(RepositoryLockTimeout):
        with _document_lock(p,timeout=.02):pass


def test_new_release_metadata_failure_still_releases_os_handle(tmp_path,monkeypatch):
    import djd_maker.core.sequence_lock as mod
    original=mod.os.fdopen;failed=[]
    class Proxy:
        def __init__(self,stream):self.stream=stream
        def __getattr__(self,name):return getattr(self.stream,name)
        def write(self,data):
            if b'"released": true' in data and not failed:
                failed.append(1);raise PermissionError('controlled release metadata failure')
            return self.stream.write(data)
    monkeypatch.setattr(mod.os,'fdopen',lambda *a,**k:Proxy(original(*a,**k)))
    p=tmp_path/'sequence.json'
    with pytest.raises(PermissionError):
        with sequence_lock(p):pass
    with ThreadPoolExecutor() as pool:assert pool.submit(acquired,p).result(timeout=2)


def test_future_sequence_never_downgraded(tmp_path):
    from djd_maker.core.repositories import UnsupportedSchemaError
    r=JobRepository(tmp_path/'jobs');r.save(Job('a.txt'))
    value={'schema_version':999,'kind':'job_number_sequence','next_job_no':500}
    r._number_path.write_text(json.dumps(value));before=r._number_path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):r.ensure_job_numbers()
    assert r._number_path.read_bytes()==before


def test_busy_sequence_temp_and_backup_do_not_proliferate(tmp_path,monkeypatch):
    from djd_maker.core import storage
    r=JobRepository(tmp_path/'jobs');original=storage.os.replace
    def busy(source,destination):
        if Path(destination)==r._number_path:raise PermissionError('controlled sync conflict')
        return original(source,destination)
    monkeypatch.setattr(storage.os,'replace',busy)
    monkeypatch.setattr('djd_maker.core.cancellation.interruptible_sleep',lambda n:None)
    for _ in range(2):
        with pytest.raises(SequenceUnavailable):r.save(Job('a.txt'))
    assert not list(tmp_path.glob('*.tmp')) and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.skipif(os.name!='nt',reason='Windows sharing semantics')
def test_real_windows_sharing_conflict_retries_without_corrupting_sequence(tmp_path):
    import ctypes
    from ctypes import wintypes as w
    r=JobRepository(tmp_path/'jobs');r.save(Job('a.txt'))
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype=w.HANDLE;k.CloseHandle.argtypes=[w.HANDLE]
    h=k.CreateFileW(str(r._number_path),0x80000000,1,None,3,0,None)
    assert h!=ctypes.c_void_p(-1).value
    with ThreadPoolExecutor() as pool:
        job=Job('b.txt');future=pool.submit(r.save,job)
        try:
            time.sleep(.2)
            assert json.loads(r._number_path.read_text())['next_job_no']==2
        finally:k.CloseHandle(h)
        future.result(timeout=5)
    assert job.job_no==2 and r._number_document().load()['next_job_no']==3


def test_backup_publish_retry_and_pending_journal_keep_high_water(tmp_path,monkeypatch):
    from djd_maker.core.storage import JsonStore
    r=JobRepository(tmp_path/'jobs');original=JsonStore.save;failures=[]
    def sharing(store,value):
        if store.path==r._number_document().backup_path and not failures:
            failures.append(1);raise PermissionError('controlled backup sharing failure')
        return original(store,value)
    monkeypatch.setattr(JsonStore,'save',sharing)
    j=Job('a.txt');r.save(j)
    doc=r._number_document()
    assert j.job_no==1 and json.loads(doc.path.read_text())==json.loads(doc.backup_path.read_text())
    r._deferred_state.record(j,'controlled pending first publish')
    r._document(j.id).path.unlink();doc.path.unlink();doc.backup_path.unlink()
    r.ensure_job_numbers()
    assert doc.load()['next_job_no']==2
