from dataclasses import replace
from djd_maker.core.models import Job, JobState
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from test_pipeline import MemoryJobs, coordinator, SchedulerClock
from test_v122_sequential_runtime import Remote


def test_generating_deadline_due_reschedule_then_ready_same_job(tmp_path):
    job = Job('lesson.txt', state=JobState.WAITING_VIDEO, notebook_id='lesson',
              notebook_url='https://notebook.google.com/notebook/lesson')
    source = tmp_path / 'lesson.txt'
    source.write_text('source', encoding='utf-8')
    job.source_path = str(source)
    jobs, calls, records = MemoryJobs(job), [], []
    remote = Remote(calls, {job.id:'GENERATING'})
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline.paths = replace(pipeline.paths, ending_video=None)
    clock = SchedulerClock()
    scheduler = PersistentPollScheduler(jobs, first_poll_seconds=10, subsequent_poll_seconds=20, clock=clock)
    pipeline.scheduler = scheduler
    pipeline.runtime_callback = records.append
    pipeline.run_cycle()
    assert calls == []
    clock.advance(9)
    pipeline.run_cycle()
    assert calls == []
    clock.advance(1)
    pipeline.run_cycle()
    assert calls == [('poll', job.id)]
    waiting = jobs.get(job.id)
    assert waiting.state is JobState.WAITING_VIDEO
    assert waiting.artifact_status == 'GENERATING'
    assert waiting.last_checked_at
    assert scheduler.remaining_seconds(waiting) == 20
    clock.advance(19)
    pipeline.run_cycle()
    assert len(calls) == 1
    remote.statuses[job.id] = 'READY'
    clock.advance(1)
    pipeline.run_cycle()
    assert calls[-3:] == [('poll', job.id), ('download', job.id), ('delete', job.id)]
    completed = jobs.get(job.id)
    assert completed.state is JobState.COMPLETED
    assert completed.safety_gate.remote_deletion_allowed
    assert completed.ending_result == 'SKIPPED (not configured)'
    assert completed.hls_result == 'PASS'
    assert completed.txt_move_status == 'MOVED'
    ready = next(r for r in records if r['stage'] == 'artifact.ready')
    assert ready['decision'] == 'READY' and ready['next_action'] == 'Download開始'
    assert any(r['stage'] == 'raw.saved' for r in records)
    assert any(r['stage'] == 'ending.skip' for r in records)
    before = list(calls)
    pipeline.begin_run()
    pipeline.run_cycle()
    assert calls == before


def test_runtime_recovery_ready_decision_before_download(tmp_path):
    job = Job('recovery.txt', state=JobState.RECOVERY_PENDING, notebook_id='r',
              notebook_url='https://notebook.google.com/notebook/r')
    jobs, calls, records = MemoryJobs(job), [], []
    pipeline = coordinator(tmp_path, jobs, Remote(calls, {job.id:'READY'}))
    pipeline.paths = replace(pipeline.paths, ending_video=None)
    pipeline.runtime_callback = records.append
    assert pipeline.run_recovery_cycle() == [job.id]
    stages = [r['stage'] for r in records]
    assert stages.index('artifact.ready') < stages.index('download.start') < stages.index('raw.saved')
    assert jobs.get(job.id).state is JobState.COMPLETED
