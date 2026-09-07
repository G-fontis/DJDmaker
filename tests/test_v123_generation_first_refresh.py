import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QApplication

from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobStateSaveError
from djd_maker.core.cancellation import CancellationToken, cancellation_scope, RunCancelled
from djd_maker.core.runtime_operation import LABELS
from djd_maker.gui.viewmodels import state_display, job_stage_texts
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from test_pipeline import MemoryJobs, TerminalSaveFailureJobs, coordinator
from test_v122_sequential_runtime import Remote, failed
from test_gui import _window, _drain_until


def test_100_jobs_generation_dispatch_before_any_download_or_local(tmp_path):
    future=(datetime.now(UTC)+timedelta(hours=1)).isoformat()
    jobs=[Job(f'lesson{i}.txt',id=f'job{i}') for i in range(80)]
    for i in range(80,100):
        state=JobState.WAITING_VIDEO if i<90 else JobState.DOWNLOAD_PENDING if i<95 else JobState.RESERVED_WAITING_CREDIT_RESET
        jobs.append(Job(f'lesson{i}.txt',id=f'job{i}',state=state,notebook_id=f'nb{i}',notebook_url=f'https://notebook.google.com/notebook/nb{i}',next_poll_at=future))
    repository=MemoryJobs(*jobs); events=[]; records=[]
    remote=Remote(events)
    pipeline=coordinator(tmp_path,repository,remote)
    pipeline.scheduler=PersistentPollScheduler(repository)
    pipeline.runtime_callback=records.append
    original_download=remote.download_artifact
    def download(job,destination):
        assert len([x for x in events if x[0]=='submit'])==80
        assert pipeline.phase=='COLLECT_LOCAL'
        return original_download(job,destination)
    remote.download_artifact=download
    pipeline.run_cycle()
    assert [x[0] for x in events[:80]]==['submit']*80
    assert all(repository.get(f'job{i}').state is JobState.WAITING_VIDEO for i in range(80))
    assert all(repository.get(f'job{i}').state is JobState.COMPLETED for i in range(90,95))
    assert not any(r['stage'] in {'media.start','ending.start','hls.start','zip.start','download.start'} and r.get('phase')=='動画生成開始フェーズ' for r in records)
    assert any(r['stage']=='phase.b' for r in records)
    assert len(remote.events)==90


@pytest.mark.parametrize('state',[JobState.GENERATING,JobState.WAITING_VIDEO,JobState.RESERVED_WAITING_CREDIT_RESET,JobState.DOWNLOAD_PENDING,JobState.COMPLETED])
def test_started_jobs_do_not_need_dispatch_after_restart(tmp_path,state):
    job=Job('one.txt',state=state,notebook_id='id',notebook_url='https://notebook.google.com/notebook/id')
    pipeline=coordinator(tmp_path,MemoryJobs(job),Remote([]))
    assert not pipeline._needs_dispatch(job)


def test_fatal_does_not_block_cloud_phase_and_retryable_processed(tmp_path):
    fatal=failed('fatal','FATAL_FAILED'); retry=failed('retry')
    jobs=MemoryJobs(fatal,retry);events=[]
    pipeline=coordinator(tmp_path,jobs,Remote(events))
    pipeline.scheduler=PersistentPollScheduler(jobs)
    pipeline.run_cycle()
    assert ('submit',retry.id) in events
    assert not any(j==fatal.id for _,j in events)
    assert pipeline.phase=='COLLECT_LOCAL'


@pytest.mark.parametrize('phase',['GENERATION_DISPATCH','COLLECT_LOCAL'])
def test_stop_in_each_phase_prevents_next_job(tmp_path,phase):
    a,b=Job('a.txt'),Job('b.txt')
    if phase=='COLLECT_LOCAL':
        a.state=b.state=JobState.DOWNLOAD_PENDING
    jobs=MemoryJobs(a,b);events=[];token=CancellationToken()
    remote=Remote(events); pipeline=coordinator(tmp_path,jobs,remote)
    original=remote.submit if phase=='GENERATION_DISPATCH' else remote.download_artifact
    def stop_after(*args):
        result=original(*args);token.request();return result
    if phase=='GENERATION_DISPATCH': remote.submit=stop_after
    else: remote.download_artifact=stop_after
    with cancellation_scope(token),pytest.raises(RunCancelled):pipeline.run_cycle()
    assert not any(j==b.id for _,j in events)


STAGES=['notebook.create','notebook.created','notebook.goto','source.upload','source.uploaded','source.error','source.ready',
        'chat.interactable','chat.send','chat.sent','chat.reply','generation.accepted','quota.detected','reservation.start',
        'reservation.complete','artifact.ready','download.start','download.complete','raw.validate','raw.saved','artifact.deleted',
        'ending.start','ending.complete','ending.skip','hls.start','hls.complete','zip.start','zip.complete','stop.complete']


@pytest.mark.parametrize('stage', ['generation.accepted', 'reservation.complete'])
def test_notebook_column_finishes_on_acceptance_event(stage):
    job = Job('accepted.txt', state=JobState.UPLOADING, presentation_stage=stage)
    assert job_stage_texts(job)[0] == '○ Notebook完了'


@pytest.mark.parametrize('state,stage,label', [
    (JobState.DOWNLOADING, 'raw.validate', '▶ RAW安全検証中'),
    (JobState.DOWNLOADING, 'download.complete', '▶ 動画回収完了'),
    (JobState.CREDIT_EXHAUSTED, 'reservation.start', '▶ 予約処理中'),
    (JobState.CREDIT_EXHAUSTED, 'reservation.complete', '▶ 予約済み'),
])
def test_substage_is_not_hidden_by_coarse_state(state, stage, label):
    assert state_display(Job('stage.txt', state=state, presentation_stage=stage)) == label


def test_runtime_notebook_url_refreshed_after_create(tmp_path):
    from djd_maker.core.runtime_operation import report_operation
    job = Job('new.txt')
    jobs = MemoryJobs(job)
    remote = Remote([])
    submit = remote.submit
    def created(job):
        job.notebook_url = 'https://notebook.google.com/notebook/new-id'
        report_operation('notebook.created')
        return submit(job)
    remote.submit = created
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.scheduler = PersistentPollScheduler(jobs)
    records = []
    pipeline.runtime_callback = records.append
    pipeline.run_cycle()
    created_event = next(r for r in records if r['stage'] == 'notebook.created')
    assert created_event['notebook'] == 'https://notebook.google.com/notebook/new-id'


@pytest.mark.parametrize('stage',STAGES)
def test_persisted_stage_immediately_updates_only_job_row_on_gui_thread(tmp_path,stage,monkeypatch):
    job=Job('one.txt',state=JobState.UPLOADING)
    window,_,_,bridge=_window(tmp_path,[Job.from_dict(job.to_dict())],ending=False)
    jobs=MemoryJobs(job);pipeline=coordinator(tmp_path,jobs,Remote([]))
    persisted=[];draw_threads=[]
    def notify(snapshot):
        assert jobs.get(snapshot.id).presentation_stage==stage
        persisted.append(time.monotonic());bridge.publish_job(snapshot)
    pipeline.job_callback=notify
    refresh=window._refresh_summary
    def record_draw():
        draw_threads.append(QThread.currentThread());refresh()
    monkeypatch.setattr(window,'_refresh_summary',record_draw)
    monkeypatch.setattr(window,'set_jobs',lambda _:pytest.fail('full table rebuild'))
    try:
        worker=threading.Thread(target=lambda:pipeline._stage_event(job,stage));worker.start();worker.join(2)
        assert not worker.is_alive()
        _drain_until(lambda:bool(draw_threads))
        assert draw_threads==[QApplication.instance().thread()]
        assert time.monotonic()-persisted[0]<1
        assert window.job_table.item(0,5).text()==state_display(jobs.get(job.id))
        assert stage in LABELS
    finally:window.close()


def test_sort_checkbox_scroll_and_selected_row_survive_update(tmp_path):
    jobs=[Job(f'lesson{i}.txt',id=f'j{i}') for i in range(175)]
    window,_,_,bridge=_window(tmp_path,jobs,ending=False)
    try:
        window.show();QApplication.processEvents()
        window.job_table.sortItems(1,Qt.SortOrder.DescendingOrder)
        row=next(r for r in range(175) if window.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)=='j20')
        window.job_table.selectRow(row)
        window.job_table.item(row,6).setCheckState(Qt.CheckState.Checked)
        window.job_table.verticalScrollBar().setValue(80)
        scroll=window.job_table.verticalScrollBar().value()
        snapshot=replace(jobs[20],state=JobState.GENERATING,presentation_revision=1)
        bridge.publish_job(snapshot)
        _drain_until(lambda:next(j for j in window.jobs if j.id=='j20').state is JobState.GENERATING)
        assert window._selected_job().id=='j20'
        assert window._checked_job_ids=={'j20'}
        assert window.job_table.verticalScrollBar().value()==scroll
        assert window.job_table.horizontalHeader().sortIndicatorOrder()==Qt.SortOrder.DescendingOrder
        row=next(r for r in range(175) if window.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)=='j20')
        assert window.job_table.item(row,5).text()=='▶ 動画生成中'
        assert window.notebook_complete_label.text()=='Notebook完了: 1/175'
    finally:window.close()


def test_save_failure_emits_no_success_and_keeps_durable_state(tmp_path):
    job=Job('one.txt');jobs=TerminalSaveFailureJobs(job)
    pipeline=coordinator(tmp_path,jobs,Remote([]));events=[];pipeline.job_callback=events.append
    with pytest.raises(JobStateSaveError):pipeline._transition(jobs.get(job.id),JobState.UPLOADING)
    assert not events
    assert jobs.get(job.id).state is JobState.WAITING


def test_generation20_visible_while21_still_in_submit(tmp_path):
    a,b=Job('20.txt'),Job('21.txt');jobs=MemoryJobs(a,b);events=[]
    window,_,_,bridge=_window(tmp_path,[a,b],ending=False)
    pipeline=coordinator(tmp_path,jobs,Remote(events));pipeline.scheduler=PersistentPollScheduler(jobs)
    pipeline.job_callback=bridge.publish_job
    entered=threading.Event();release=threading.Event();submit=pipeline.notebook.submit
    def blocked(job):
        if job.id==b.id:entered.set();release.wait(3)
        return submit(job)
    pipeline.notebook.submit=blocked
    worker=threading.Thread(target=pipeline.run_cycle)
    try:
        worker.start();_drain_until(entered.is_set)
        _drain_until(lambda:next(j for j in window.jobs if j.id==a.id).state is JobState.WAITING_VIDEO)
        assert worker.is_alive() and jobs.get(b.id).state is JobState.UPLOADING
        row=next(r for r in range(2) if window.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)==a.id)
        assert window.job_table.item(row,5).text()=='▶ 動画生成中'
    finally:release.set();worker.join(5);window.close()


def test_runtime_notifications_do_not_reload_175_json_files(tmp_path,monkeypatch):
    from test_gui_pipeline_controller import controller
    service,repository,_,_=controller(tmp_path,Job('one.txt'))
    monkeypatch.setattr(repository,'list',lambda:pytest.fail('per-event full JSON reload'))
    service._runtime_update({'stage':'source.ready','job_id':'test'})


def test_queued_event_after_close_does_not_touch_deleted_widgets(tmp_path):
    job=Job('one.txt');window,_,_,bridge=_window(tmp_path,[job],ending=False)
    worker=threading.Thread(target=lambda:bridge.publish_job(replace(job,state=JobState.GENERATING)))
    worker.start();worker.join();window.close();QApplication.processEvents()
    assert window._closing


def test_current_job_summary_follows_runtime_not_first_active_row(tmp_path):
    a, b = Job('a.txt', state=JobState.GENERATING), Job('b.txt', state=JobState.UPLOADING)
    window, _, _, _ = _window(tmp_path, [a,b], ending=False)
    try:
        window._apply_runtime_status({'runtime': {'job_id': b.id, 'job': b.script_name, 'stage': 'chat.send'}})
        assert window.current_job_label.text() == f'現在ジョブ: {b.script_name}'
        assert window.current_stage_label.text() == '現在工程: プリセット送信中'
        window.update_job(replace(b, presentation_stage='chat.sent', presentation_revision=1))
        assert window.current_job_label.text() == f'現在ジョブ: {b.script_name}'
    finally:
        window.close()
