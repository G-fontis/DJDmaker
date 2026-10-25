import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from djd_maker.core.models import Job, JobState
from djd_maker.core.media_process import (MediaProcessError, process_error, run_media_process,
    media_retry_scope, media_spawn_options, process_audit_snapshot, classify_media_failure)
from djd_maker.core.repositories import JobRepository
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.orchestration.task_discovery import terminal
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from test_pipeline import coordinator,MemoryJobs,Ending


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr('djd_maker.core.cancellation.interruptible_sleep',lambda seconds:None)


@pytest.mark.parametrize('code',[0xc0000142,-1073741502])
def test_ffmpeg_0xc0000142_is_retryable(code):
    e=process_error(result=SimpleNamespace(returncode=code,stderr=''))
    assert e.code=='FFMPEG_PROCESS_START_FAILED' and e.retryable and e.windows_error=='0xc0000142'


@pytest.mark.parametrize('failures',[1,2,3,4])
def test_ffmpeg_process_start_max_three_retries(tmp_path,failures,monkeypatch):
    job=Job('a.txt',state=JobState.ENDING);repo=JobRepository(tmp_path/'jobs');repo.save(job)
    calls=[];sleeps=[];events=[]
    monkeypatch.setattr('djd_maker.core.cancellation.interruptible_sleep',sleeps.append)
    def runner(*args,**kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=0xc0000142 if len(calls)<=failures else 0,stderr='init failed')
    with media_retry_scope(job,repo.save),operation_scope(lambda s,f:events.append((s,f))):
        if failures==4:
            with pytest.raises(MediaProcessError,match='FFMPEG_RETRY_EXHAUSTED'):
                run_media_process(['ffmpeg','out'],runner=runner,purpose='Encode')
        else:assert run_media_process(['ffmpeg','out'],runner=runner,purpose='Encode').returncode==0
    assert len(calls)==min(failures+1,4)
    assert sleeps==[2,5,10][:min(failures,3)]
    assert repo.require(job.id).attempt_by_stage['ffmpeg.ENDING.Encode']==failures
    assert len([e for e in events if e[0]=='ffmpeg.retry'])==min(failures,3)


def test_ffmpeg_retry_budget_survives_restart_no_fourth_retry(tmp_path):
    repo=JobRepository(tmp_path/'jobs');job=Job('a.txt',state=JobState.HLS_ENCODING)
    job.attempt_by_stage['ffmpeg.HLS_ENCODING.HLS']=3;repo.save(job)
    loaded=JobRepository(repo.directory).require(job.id);calls=[]
    def fail(*a,**k):calls.append(1);return SimpleNamespace(returncode=0xc0000142,stderr='')
    with media_retry_scope(loaded,repo.save),pytest.raises(MediaProcessError,match='EXHAUSTED'):
        run_media_process(['ffmpeg','out'],runner=fail,purpose='HLS')
    assert len(calls)==1
    with media_retry_scope(repo.require(job.id),repo.save),pytest.raises(MediaProcessError,match='EXHAUSTED'):
        run_media_process(['ffmpeg','out'],runner=fail,purpose='HLS')
    assert len(calls)==1


def test_ffmpeg_retry_cleans_failed_temp_only_preserves_good_output(tmp_path):
    old=tmp_path/'good.mp4';old.write_bytes(b'good')
    temp=tmp_path/'.new.123.staging.mp4';job=Job('a.txt');calls=[]
    def runner(*a,**k):
        calls.append(1)
        if len(calls)==1:temp.write_bytes(b'bad');return SimpleNamespace(returncode=0xc0000142,stderr='')
        assert not temp.exists();temp.write_bytes(b'valid');return SimpleNamespace(returncode=0,stderr='')
    with media_retry_scope(job,lambda j:None):run_media_process(['ffmpeg',str(temp)],runner=runner,purpose='Encode')
    assert old.read_bytes()==b'good' and temp.read_bytes()==b'valid'


@pytest.mark.parametrize('exhausted',[False,True])
def test_one_ffmpeg_failure_does_not_stop_other_jobs_or_set_user_stop(tmp_path,exhausted):
    class InjectEnding(Ending):
        def process(self,raw,ending,out,padding_seconds):
            def runner(*a,**k):
                attempts.append(raw.name)
                failed=raw.name=='a.mp4' and (exhausted or attempts.count(raw.name)==1)
                return SimpleNamespace(returncode=0xc0000142 if failed else 0,stderr='')
            run_media_process(['ffmpeg',str(out)],runner=runner,purpose='Encode')
            return super().process(raw,ending,out,padding_seconds)
    jobs=[Job(f'{n}.txt',state=JobState.RAW_READY,raw_path=str(tmp_path/'raw_files'/f'{n}.mp4')) for n in ('a','b')]
    for j in jobs:Path(j.raw_path).parent.mkdir(exist_ok=True);Path(j.raw_path).write_bytes(b'valid')
    attempts=[];repo=MemoryJobs(*jobs);nb=FakeNotebookAdapter({});pipe=coordinator(tmp_path,repo,nb,InjectEnding())
    pipe._run_local_tasks(repo.list())
    a,b=[repo.get(j.id) for j in jobs]
    assert b.state==JobState.COMPLETED
    assert a.state==(JobState.FAILED if exhausted else JobState.COMPLETED)
    if exhausted:assert a.error_code=='FFMPEG_RETRY_EXHAUSTED' and terminal(a)
    assert attempts.count('a.mp4')==(4 if exhausted else 2)
    assert not nb.download_calls and not nb.submit_calls


def test_ffmpeg_missing_executable_global_not_retryable():
    e=process_error(error=FileNotFoundError('missing'))
    assert e.code=='FFMPEG_FATAL_GLOBAL' and not e.retryable


def test_ffmpeg_timeout_retryable():
    assert process_error(error=subprocess.TimeoutExpired('ffmpeg',1)).code=='FFMPEG_TIMEOUT'


@pytest.mark.parametrize('detail,code,retryable',[
    ('Invalid data found when processing input','FFMPEG_INPUT_INVALID',False),
    ('Permission denied opening output','FFMPEG_OUTPUT_IO_ERROR',True),
    ('unsupported encoder','FFMPEG_FATAL_JOB',False),
])
def test_media_error_classification(detail,code,retryable):
    e=classify_media_failure(SimpleNamespace(returncode=1,stderr=detail))
    assert (e.code,e.retryable)==(code,retryable)


@pytest.mark.parametrize('stage,purpose',[(JobState.ENDING,'tail cut stream copy'),(JobState.ENDING,'ending stream copy'),
    (JobState.ENDING,'local processing'),(JobState.HLS_ENCODING,'HLS'),(JobState.HLS_ENCODING,'HLS probe')])
def test_failed_command_only_retries_preserves_stage_checkpoints(stage,purpose):
    job=Job('fixture.txt',state=stage,raw_path='raw.mp4',edited_path='ending.mp4',
            ending_result='PASS',tail_cut_result='PASS',remote_checkpoint='ARTIFACT_READY')
    before={k:getattr(job,k) for k in ('raw_path','edited_path','ending_result','tail_cut_result','remote_checkpoint','state')}
    calls=[]
    def runner(*a,**k):calls.append(1);return SimpleNamespace(returncode=0xc0000142 if len(calls)==1 else 0,stderr='')
    with media_retry_scope(job,lambda j:None):run_media_process(['ffmpeg','out'],runner=runner,purpose=purpose)
    assert len(calls)==2 and before=={k:getattr(job,k) for k in before}


@pytest.mark.skipif(os.name!='nt',reason='Windows error mode')
def test_ffmpeg_popup_suppression_preserves_error_mode():
    import ctypes
    k=ctypes.WinDLL('kernel32');before=k.GetErrorMode();kwargs={}
    with media_spawn_options(['ffmpeg.exe'],kwargs):
        assert k.GetErrorMode() & 3 ==3
        assert kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert k.GetErrorMode()==before
