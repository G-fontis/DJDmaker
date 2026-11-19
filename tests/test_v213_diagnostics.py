"""Occurrence timestamps, original error evidence, and bounded readable GUI logs."""
from datetime import datetime,timezone,timedelta
from dataclasses import asdict
import json,time
from pathlib import Path
import pytest
from djd_maker.core.diagnostics import occurrence,normalize,display_time,redact,EventJournal,job_error,error_summary
from djd_maker.core.models import Job,JobState
from djd_maker.gui.dialogs import LogDialog,JobDetailDialog
from djd_maker.gui.viewmodels import LogRecord
from test_gui import _app,_window
from test_pipeline import coordinator,MemoryJobs
from test_v122_sequential_runtime import Remote
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from djd_maker.core.settings import AppSettings

UTC=datetime(2026,11,19,5,44,18,123000,tzinfo=timezone.utc)


@pytest.mark.parametrize('record',[{'stage':'job.start'},{'stage':'scheduler.task'},{'stage':'download.transfer'}, {'stage':'system'}])
def test_new_event_has_aware_utc_and_local_timestamp(record):
    value=normalize(record,new=True)
    assert value['event_id'] and datetime.fromisoformat(value['timestamp_utc']).utcoffset()==timedelta(0)
    assert datetime.fromisoformat(value['timestamp_local']).utcoffset()==timedelta(hours=9)
    assert value['timestamp'] and value['job_id'] is None


@pytest.mark.parametrize('value,expected',[
    ({'timestamp_utc':UTC.isoformat()},'2026-11-19 14:44:18'),
    ({'timestamp':UTC.isoformat()},'2026-11-19 14:44:18'),
    ({'timestamp':UTC.timestamp()},'2026-11-19 14:44:18'),
    ({'timestamp':'12:01'},'12:01'),
    ({},'時刻不明（旧記録）'),({'timestamp':'invalid'},'時刻不明（旧記録）')])
def test_legacy_and_jst_mapping_never_invents_time(value,expected):
    assert display_time(value)==expected
    result=normalize(value)
    assert 'timestamp_utc' not in result or result['timestamp_utc']==value.get('timestamp_utc')


def test_naive_timestamp_not_silently_assigned_timezone():
    assert 'TZ不明' in display_time({'timestamp':'2026-11-19T14:44:18'})
    with pytest.raises(ValueError):occurrence(UTC.replace(tzinfo=None))


def test_restart_log_timestamp_and_order_preserved(tmp_path):
    journal=EventJournal(tmp_path)
    values=[normalize({**occurrence(UTC+timedelta(seconds=i)),'message':str(i)},new=True) for i in range(8)]
    for value in values:journal.append(value)
    assert EventJournal(tmp_path).recent()==values
    assert EventJournal(tmp_path).recent(2)==values[-2:]


def controller(tmp_path,job):
    jobs=MemoryJobs(job)
    service=GuiPipelineController(jobs=jobs,settings=AppSettings(),app_root=tmp_path,pipeline=None,
        pipeline_factory=lambda:coordinator(tmp_path,jobs,Remote([])),
        scheduler=PersistentPollScheduler(jobs))
    records=[];service.bind(jobs=lambda _:None,status=lambda _:None,log=records.append,error=lambda *_:None)
    return service,records


def test_runtime_job_fields_and_correlation_persisted(tmp_path):
    job=Job('lesson.txt',job_no=834);service,records=controller(tmp_path,job)
    for stage in ('job.start','job.result','job.next'):
        service._runtime_update({'stage':stage,'job_id':job.id,'operation_id':'one-operation','outcome':'WAITING'})
    rows=EventJournal(tmp_path/'logs').recent()
    assert all(r['job_id']==job.id and r['job_no']==834 and r['job_title']=='lesson' for r in rows)
    assert {r['correlation_id'] for r in rows}=={'one-operation'}
    assert len({r['event_id'] for r in rows})==len(rows)
    assert all(r['timestamp'] for r in rows)


def test_reused_runtime_dict_gets_new_occurrence(tmp_path):
    job=Job('a.txt');s,records=controller(tmp_path,job)
    s._runtime_update({'stage':'capability.refresh'})
    first=dict(s._runtime);s._runtime_update({**first,'stage':'scheduler.task'})
    assert s._runtime['event_id']!=first['event_id']


@pytest.mark.parametrize('code,category',[
    ('BROWSER_CONNECTION_ERROR','BROWSER_CONNECTION_ERROR'),('BROWSER_PROFILE_LOCKED','BROWSER_PROFILE_LOCKED'),
    ('NOTEBOOK_RESUME_METADATA_MISSING','NOTEBOOK_STATE_UNKNOWN'),('SOURCE_UPLOAD_FAILED','SOURCE_UPLOAD_ERROR'),
    ('GENERATION_FAILED','GENERATION_ERROR'),('CREDIT_LIMIT','CREDIT_LIMIT'),('DOWNLOAD_FAILED','DOWNLOAD_ERROR'),
    ('DOWNLOAD_QUALITY_FAILED','DOWNLOAD_QUALITY_ERROR'),('FFMPEG_RETRY_EXHAUSTED','FFMPEG_ERROR'),
    ('JOB_STATE_SAVE_ERROR','JOB_STATE_SAVE_ERROR'),('UNKNOWN_INTERNAL_ERROR','UNKNOWN_INTERNAL_ERROR')])
def test_error_details_categories_and_required_fields(code,category):
    job=Job('a.txt',error_code=code,error_message='explicit cause')
    details=job_error(job,'current-stage')
    assert details['error_category']==category and details['stage']=='current-stage'
    assert details['diagnostic_id'] and details['safe_error_message']
    assert details['recovery_block_reason'] and details['next_action']


@pytest.mark.parametrize('payload,secret',[
    ('Cookie: session=hidden-value; auth=another-secret','hidden-value'),
    ('Authorization: Bearer hidden-value','hidden-value'),
    ('password="hidden value"','hidden value'),
    ('{"api_key":"hidden-value"}','hidden-value'),
    ('https://example.test/video?X-Goog-Signature=hidden-value','hidden-value'),
    ({'refresh_token':'hidden-value'},'hidden-value'),
    ({'nested':{'Cookie':'hidden-value'}},'hidden-value'),
    ("{'Cookie': 'hidden-value'}",'hidden-value'),
    ('{"Authorization":"Bearer hidden-value"}','hidden-value')])
def test_credentials_and_signed_urls_redacted(payload,secret):
    assert secret not in json.dumps(redact(payload))


def test_exception_traceback_function_type_and_job_preserved(tmp_path):
    job=Job('a.txt',error_code='GENERATION_ERROR')
    def failing_function():raise ValueError('password=hidden-value')
    try:failing_function()
    except ValueError as exc:details=EventJournal(tmp_path).exception(exc,job,'studio.inspect')
    record=json.loads((tmp_path/details['traceback_reference']).read_text(encoding='utf8'))
    assert record['job_id']==job.id and record['exception_type']=='ValueError'
    assert 'failing_function' in record['traceback'] and record['call_path'][-1]['function']=='failing_function'
    assert 'hidden-value' not in json.dumps(record)
    job.error_details=details
    value=normalize({'stage':'job.result','outcome':'FAILED_WITH_REASON','job_id':job.id},job=job)
    assert value['error_details']['stage']=='studio.inspect'


def test_saved_error_without_original_exception_is_honest():
    job=Job('INVST038.txt',state=JobState.FAILED,error_code='NOTEBOOK_RESUME_METADATA_MISSING',
        failure_class='FATAL_FAILED',resume_checkpoint='STOPPED:UPLOADING')
    value=job_error(job,'Notebook状態確認')
    assert value['exception_type'] is None and value['traceback_reference'] is None
    assert 'IDまたはURL' in value['safe_error_message'] and '二重作成' in value['recovery_block_reason']
    assert 'Chrome' not in error_summary(job,value) and not value['retryable']


def test_retryable_and_exhausted_conditions_not_changed():
    job=Job('a.txt',error_code='NOTEBOOK_STATE_UNKNOWN',attempt_by_stage={'scheduler.recovery':1})
    assert job_error(job,'state')['retryable']
    job.failure_class='TERMINAL_FAILED';job.attempt_by_stage['scheduler.recovery']=3
    value=job_error(job,'state')
    assert value['retry_exhausted'] and not value['retryable'] and value['attempt_count']==3


@pytest.mark.parametrize('phase',['PHASE1','PHASE2'])
def test_gui_timestamp_job_engine_mapping_and_detail_switch(tmp_path,phase):
    job=Job('lesson.txt',job_no=834);window,*_= _window(tmp_path,[job]);window.switch_gui(phase)
    value=normalize({**occurrence(UTC),'job_id':job.id,'stage':'source.upload','engine':'Notebook','message':'Source確認開始'},job=job)
    window._append_log_record(value);dialog=window._log_dialog
    assert dialog.model.item(0,0).text()=='2026-11-19 14:44:18'
    assert '834' in dialog.model.item(0,1).text() and job.id in dialog.model.item(0,1).text()
    assert [dialog.model.item(0,i).text() for i in (2,3,4,5)]==['Notebook','source.upload','INFO','Source確認開始']
    dialog.append_record(normalize({'stage':'capability.refresh'},new=True))
    assert dialog.proxy.rowCount()==1
    dialog.details_checkbox.setChecked(True);assert dialog.proxy.rowCount()==2
    dialog.filter_edits[1].setText('Notebook');assert dialog.proxy.rowCount()==1
    dialog.table.setCurrentIndex(dialog.proxy.index(0,0));_app().processEvents()
    assert dialog.details.toPlainText().startswith('Source確認開始')
    window.close()


def test_global_log_has_system_label_and_timestamp(tmp_path):
    _app();dialog=LogDialog();dialog.append_record(normalize({'message':'Chrome接続確認'},new=True))
    assert dialog.model.item(0,1).text()=='システム' and dialog.model.item(0,0).text()
    dialog.close()
    from djd_maker.gui.app import build_desktop
    from djd_maker.packaging.acceptance_assets import initialize_fixture_assets
    initialize_fixture_assets(tmp_path)
    app,window,service=build_desktop(tmp_path)
    records=[];service._log_sink=records.append
    service._runtime_update({'stage':'job.result','job_id':'previous-job','outcome':'FAILED_WITH_REASON'})
    service.download_lifecycle.logger({'event':'BROWSER_CLOSED','timestamp':UTC.isoformat(),'job_id':None})
    transfer=next(r for r in records if r['stage']=='download.transfer')
    assert transfer['job_id'] is None and transfer['level']=='DEBUG' and not transfer.get('outcome')
    assert transfer['timestamp']=='2026-11-19 14:44:18'
    service.shutdown();window.close()


def test_large_log_model_is_bounded_and_responsive():
    _app();dialog=LogDialog();start=time.monotonic()
    records=[normalize({'stage':'capability.refresh','message':str(i)},new=True) for i in range(20000)]
    dialog.append_records(records)
    assert dialog.model.rowCount()==5000 and dialog.proxy.rowCount()==0
    dialog.details_checkbox.setChecked(True);assert dialog.proxy.rowCount()==5000
    assert time.monotonic()-start<8
    dialog.close()


def test_recovery_error_isolated_and_checkpoint_preserved(tmp_path):
    failed=Job('old.txt',state=JobState.FAILED,error_code='NOTEBOOK_RESUME_METADATA_MISSING',
        failure_class='FATAL_FAILED',resume_checkpoint='STOPPED:UPLOADING')
    pending=Job('new.txt');jobs=MemoryJobs(failed,pending);remote=Remote([]);pipe=coordinator(tmp_path,jobs,remote)
    pipe.run_cycle()
    assert jobs.get(failed.id).resume_checkpoint=='STOPPED:UPLOADING'
    assert ('submit',pending.id) in remote.events
    assert jobs.get(failed.id).error_code=='NOTEBOOK_RESUME_METADATA_MISSING'


def test_unknown_exception_has_diagnostic_id_and_original_type(tmp_path):
    job=Job('a.txt');pipe=coordinator(tmp_path,MemoryJobs(job),Remote([]))
    try:raise LookupError('unclassified failure')
    except LookupError as exc:pipe._diagnose(job,exc)
    assert job.error_details['exception_type']=='LookupError' and job.error_details['diagnostic_id']
    assert '原因を特定できませんでした' in job.error_details['safe_error_message']
    assert (tmp_path/'logs'/job.error_details['traceback_reference']).exists()
    from djd_maker.core.repositories import JobStateSaveError
    from djd_maker.core.deferred_state import SaveDeferred
    before=job.state;events=[];pipe.runtime_callback=events.append
    pipe.jobs.save=lambda value:(_ for _ in ()).throw(JobStateSaveError('sharing conflict'))
    with pytest.raises(SaveDeferred):pipe._save(job)
    details=next(e['error_details'] for e in events if e.get('stage')=='save.diagnostic')
    assert details['exception_type']=='JobStateSaveError' and details['error_code']=='JOB_STATE_SAVE_ERROR'
    assert job.state is before and (tmp_path/'logs'/details['traceback_reference']).exists()


def test_user_stop_reason_not_replaced_by_previous_job_error(tmp_path):
    job=Job('a.txt',state=JobState.FAILED,error_code='DOWNLOAD_ERROR')
    service,records=controller(tmp_path,job)
    service._runtime_update({'job_id':job.id,'stage':'job.result','outcome':'FAILED_WITH_REASON'})
    service._set_stop_reason('USER_STOP')
    assert service._stop_reason.code=='USER_STOP'
    assert job.error_code=='DOWNLOAD_ERROR'
    last=records[-1]
    assert last['level']=='INFO' and last['job_id'] is None and 'STOP' in last['stage']
    from djd_maker.core.cancellation import RunCancelled
    errors=[];service._error_sink=lambda *args:errors.append(args)
    service.pipeline_factory=lambda:(_ for _ in ()).throw(RunCancelled('user stop'))
    service._stop_request_reason='USER_STOP'
    service._run_loop()
    assert not errors and service._stop_reason.code=='USER_STOP'


def test_repeated_exceptions_keep_both_tracebacks(tmp_path):
    journal=EventJournal(tmp_path);job=Job('a.txt');references=[]
    for _ in range(2):
        try:raise RuntimeError('same failure')
        except RuntimeError as exc:
            job.error_details=journal.exception(exc,job,'inspect');references.append(job.error_details['traceback_reference'])
    assert len(set(references))==2 and all((tmp_path/p).exists() for p in references)


def test_legacy_epoch_zero_is_not_missing():
    assert display_time({'timestamp':0})=='1970-01-01 09:00:00'


def test_repeated_internal_events_retained_as_debug(tmp_path):
    job=Job('a.txt');service,records=controller(tmp_path,job)
    for _ in range(5):service._runtime_update({'stage':'capability.refresh'})
    assert len(EventJournal(tmp_path/'logs').recent())==5
    assert all(r['level']=='DEBUG' for r in records)


def test_supplied_aware_occurrence_canonicalized_without_replacing_time():
    value=normalize({'timestamp':UTC.isoformat()},new=True)
    assert datetime.fromisoformat(value['timestamp_utc'])==UTC
    assert value['timestamp']=='2026-11-19 14:44:18'


def test_redaction_is_idempotent():
    value='password=secret-value Cookie: session=hidden'
    assert redact(redact(value))==redact(value)


def test_normal_gui_does_not_render_debug_flood(tmp_path):
    window,*_=_window(tmp_path,[]);start=time.monotonic()
    for i in range(20000):window._append_log_record({'level':'DEBUG','stage':'capability.refresh','message':str(i)})
    assert window._log_dialog.model.rowCount()==0 and time.monotonic()-start<2
    window.close()


def test_debug_toggle_reloads_persisted_internal_events(tmp_path):
    window,*_=_window(tmp_path,[])
    record=normalize({'stage':'capability.refresh'},new=True)
    EventJournal(tmp_path/'logs').append(record);window._append_log_record(record)
    assert window._log_dialog.model.rowCount()==0
    window._log_dialog.details_checkbox.setChecked(True)
    assert window._log_dialog.proxy.rowCount()==1
    window.close()
