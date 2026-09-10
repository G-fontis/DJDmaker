from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
from djd_maker.core.models import Job,JobState
from djd_maker.core.output_ownership import reconcile_output_ownership,path_identity
from djd_maker.core.repositories import JobRepository
from test_gui_pipeline_controller import MemoryJobs,controller

def source(root,folder='a',name='lesson.txt',text='lesson'):
    p=root/folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8');return str(p)

def test_output_collision_self_owner_allowed(tmp_path):
    j=Job(source(tmp_path),id='same');repo=MemoryJobs(j)
    result=reconcile_output_ownership(repo,tmp_path/'output')
    assert not result['conflicts'] and repo.get(j.id).state is JobState.WAITING

@pytest.mark.parametrize('kind',['missing','deleted','completed-other-path','completed-missing-file','terminal'])
def test_stale_owner_ignored(tmp_path,kind):
    old=Job(str(tmp_path/'old/lesson.txt'),id='old',created_at='2000')
    new=Job(source(tmp_path),id='new',created_at='2001')
    repo=MemoryJobs(new)
    if kind not in {'missing','deleted'}:
        if kind.startswith('completed'):
            old.state=JobState.COMPLETED;old.zip_path=str(tmp_path/'elsewhere/lesson.zip')
            if kind=='completed-other-path':
                p=Path(old.zip_path);p.parent.mkdir();p.write_bytes(b'preserved')
        else:old.failure_class='TERMINAL_FAILED';old.attempt_by_stage={'scheduler.recovery':3}
        repo.save(old)
    result=reconcile_output_ownership(repo,tmp_path/'output')
    assert not result['conflicts'] and repo.get('new').state is JobState.WAITING

def test_false_collision_recovers_existing_failed_job(tmp_path):
    j=Job(source(tmp_path),id='same',state=JobState.FAILED,error_code='OUTPUT_NAME_COLLISION',failure_class='FATAL_FAILED')
    repo=MemoryJobs(j);reconcile_output_ownership(repo,tmp_path/'output')
    fixed=repo.get(j.id)
    assert fixed.id=='same' and fixed.state is JobState.WAITING and fixed.error_code is None

def test_duplicate_source_references_canonical_without_failure(tmp_path):
    a=Job(source(tmp_path),id='a',created_at='2000')
    b=Job(a.source_path.upper(),id='b',created_at='2001',state=JobState.FAILED,error_code='OUTPUT_NAME_COLLISION',failure_class='FATAL_FAILED')
    repo=MemoryJobs(a,b)
    report=reconcile_output_ownership(repo,tmp_path/'output')
    assert report['duplicate_references']=={'b':'a'}
    assert repo.get('b').state is JobState.WAITING and repo.get('b').error_code is None
    assert len(repo.list())==2
    assert reconcile_output_ownership(repo,tmp_path/'output')['changed']==[]

def test_real_active_conflict_message_has_owner(tmp_path):
    a=Job(source(tmp_path,'a',text='one'),id='a',created_at='2000')
    b=Job(source(tmp_path,'b',text='two'),id='b',created_at='2001')
    other=Job(source(tmp_path,'c','other.txt'),id='c')
    repo=MemoryJobs(a,b,other);reconcile_output_ownership(repo,tmp_path/'output')
    bad=repo.get('b')
    assert bad.error_code=='OUTPUT_NAME_COLLISION' and bad.output_conflict_job_id=='a'
    assert '競合job ID：a' in bad.error_message and '他のjobを継続' in bad.error_message
    assert bad.failure_class=='OUTPUT_BLOCKED'
    assert repo.get('a').state is JobState.WAITING and repo.get('c').state is JobState.WAITING

def test_completed_output_preserved_with_duplicate_reference(tmp_path):
    path=source(tmp_path);output=tmp_path/'output';output.mkdir();z=output/'lesson.zip';z.write_bytes(b'original')
    a=Job(path,id='a',state=JobState.COMPLETED,zip_path=str(z))
    b=Job(path,id='b',state=JobState.FAILED,error_code='OUTPUT_NAME_COLLISION')
    repo=MemoryJobs(a,b);reconcile_output_ownership(repo,output)
    assert z.read_bytes()==b'original' and repo.get('b').duplicate_of_job_id=='a'

def test_startup_reconciles_full_snapshot_before_first_save(tmp_path):
    a=Job(source(tmp_path),id='a');b=Job(a.source_path,id='b')
    repo=MemoryJobs(a,b);observations=[]
    def save(j):
        observations.append(j.to_dict());repo.save(j)
    report=reconcile_output_ownership(repo,tmp_path/'out',save)
    assert not report['conflicts'] and all(j['state']!='FAILED' for j in observations)

def test_concurrent_reload_same_job_id_preserved(tmp_path):
    source(tmp_path,'入力')
    service,repo,_,_=controller(tmp_path)
    barrier=threading.Barrier(8)
    def run(_):barrier.wait();return service.reload()
    with ThreadPoolExecutor(8) as pool:list(pool.map(run,range(8)))
    first=repo.list()[0].id
    assert len(repo.list())==1
    service.reload();assert repo.list()[0].id==first

def test_source_path_windows_case_normalization(tmp_path):
    assert path_identity(tmp_path/'File.txt')==path_identity(str(tmp_path/'File.txt').upper())

def test_same_path_different_known_hash_not_aliased(tmp_path):
    path=source(tmp_path)
    repo=MemoryJobs(Job(path,id='a',source_sha256='a'),Job(path,id='b',source_sha256='b'))
    report=reconcile_output_ownership(repo,tmp_path/'out')
    assert not report['duplicate_references'] and len(report['conflicts'])==1

def test_deleted_repository_job_is_not_owner(tmp_path):
    repo=JobRepository(tmp_path/'system/jobs')
    old=Job(source(tmp_path,'old'),id='old',state=JobState.COMPLETED)
    repo.save(old);repo.delete_completed(['old'])
    new=Job(source(tmp_path,'new'),id='new');repo.save(new)
    assert not reconcile_output_ownership(repo,tmp_path/'out')['conflicts']


def test_terminal_history_does_not_deactivate_new_source_job(tmp_path):
    path=source(tmp_path)
    old=Job(path,id='old',created_at='2000',state=JobState.FAILED,
            failure_class='TERMINAL_FAILED',attempt_by_stage={'scheduler.recovery':3})
    new=Job(path,id='new',created_at='2001')
    repo=MemoryJobs(old,new)
    result=reconcile_output_ownership(repo,tmp_path/'out')
    assert not result['conflicts'] and not result['duplicate_references']
    assert repo.get('new').state is JobState.WAITING


def test_unreadable_source_hash_isolated(tmp_path,monkeypatch):
    import djd_maker.core.output_ownership as ownership
    bad=Job(source(tmp_path),id='bad')
    good=Job(source(tmp_path,'other','other.txt'),id='good')
    original=ownership.source_digest
    def read(path):
        if path==bad.source_path:raise PermissionError('busy source')
        return original(path)
    monkeypatch.setattr(ownership,'source_digest',read)
    repo=MemoryJobs(bad,good)
    result=reconcile_output_ownership(repo,tmp_path/'out')
    assert result['unreadable_sources']==['bad'] and not result['conflicts']
    assert repo.get('good').source_sha256


def test_unproven_terminal_does_not_release_current_output(tmp_path):
    old=Job(source(tmp_path,'old'),id='old',created_at='2000',state=JobState.FAILED,
            failure_class='TERMINAL_FAILED',error_code='SOURCE_UPLOAD_FAILED')
    current=Job(source(tmp_path,'new'),id='current',created_at='2001')
    repo=MemoryJobs(old,current)
    report=reconcile_output_ownership(repo,tmp_path/'output')
    assert report['conflicts']=={'current':'old'}
