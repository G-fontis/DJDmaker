"""Dynamic priority at safe task boundaries, with deterministic quota clocks."""
from datetime import timedelta
import pytest
from djd_maker.core.cloud_limit import parse_limit_text
from djd_maker.core.models import Job, JobState
from test_pipeline import coordinator, MemoryJobs, Ending, Hls
from test_v122_sequential_runtime import Remote
from test_v125_limit_pause_contract import NOW


@pytest.mark.parametrize('stage', ['ENDING', 'HLS_ENCODING'])
def test_quota_recovery_preempts_next_local_after_running_task_finishes(tmp_path, stage):
    raw = tmp_path/'raw.mp4'
    raw.write_bytes(b'raw')
    a = Job('a.txt', state=JobState(stage), raw_path=str(raw), edited_path=str(raw))
    b = Job('b.txt', state=JobState.HLS_ENCODING, raw_path=str(raw), edited_path=str(raw))
    c = Job('c.txt')
    repo = MemoryJobs(a, b, c)
    remote = Remote([])
    pipe = coordinator(tmp_path, repo, remote)
    pipe.ffmpeg_concurrency = 1
    clock = [NOW]
    pipe.cloud_limit.clock = lambda: clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    events = []
    remote.recheck_cloud_limit = lambda url: events.append('refresh') or True
    def complete_task():
        events.append('local_finished')
        clock[0] += timedelta(days=1)
    if stage == 'ENDING':
        original = pipe.ending.process
        def ending(*args, **kwargs):
            result = original(*args, **kwargs)
            complete_task()
            return result
        pipe.ending.process = ending
    else:
        original = pipe.hls.convert_validate_and_zip
        def hls(*args, **kwargs):
            result = original(*args, **kwargs)
            complete_task()
            return result
        pipe.hls.convert_validate_and_zip = hls
    pipe.run_cycle()
    assert events == ['local_finished', 'refresh']
    assert repo.get(a.id).state is (JobState.HLS_ENCODING if stage == 'ENDING' else JobState.COMPLETED)
    assert repo.get(b.id).state is JobState.HLS_ENCODING
    assert pipe.capabilities.can_generate
    assert not any(event[0] == 'submit' for event in remote.events)
    pipe.run_cycle()
    assert ('submit', c.id) in remote.events
    assert remote.events[0] == ('submit', c.id)


def test_failed_limit_recheck_does_not_release_cloud(tmp_path):
    job = Job('c.txt')
    remote = Remote([])
    pipe = coordinator(tmp_path, MemoryJobs(job), remote)
    clock = [NOW]
    pipe.cloud_limit.clock = lambda: clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    clock[0] += timedelta(days=1)
    remote.recheck_cloud_limit = lambda url: False
    pipe.run_cycle()
    assert not pipe.capabilities.can_generate
    assert pipe.capabilities.can_download
    assert not remote.events
    assert not pipe.all_tasks_completed()


def test_hls_checkpoint_yields_before_zip_and_resumes_without_reencoding(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from djd_maker.adapters.hls import HlsAdapter, ProbeResult
    raw = tmp_path/'raw.mp4'
    raw.write_bytes(b'raw')
    tool = tmp_path/'tool.exe'
    tool.touch()
    a = Job('a.txt', state=JobState.HLS_ENCODING, raw_path=str(raw), edited_path=str(raw))
    b = Job('b.txt', state=JobState.HLS_ENCODING, raw_path=str(raw), edited_path=str(raw))
    c = Job('c.txt')
    repo = MemoryJobs(a, b, c)
    remote = Remote([])
    pipe = coordinator(tmp_path, repo, remote)
    pipe.ffmpeg_concurrency = 1
    pipe.hls = HlsAdapter(tool, tool)
    clock = [NOW]
    pipe.cloud_limit.clock = lambda: clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    remote.recheck_cloud_limit = lambda url: True
    conversions = []
    def convert(command, timeout):
        directory = Path(command[-1]).parent
        conversions.append(directory)
        (directory/'playlist.m3u8').write_text('#EXTM3U\n#EXTINF:6.0,\nsegment00000.ts\n#EXT-X-ENDLIST\n', encoding='utf-8')
        (directory/'segment00000.ts').write_bytes(b'segment')
        clock[0] += timedelta(days=1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('djd_maker.adapters.hls._run', convert)
    monkeypatch.setattr('djd_maker.adapters.hls.probe_media', lambda *a: ProbeResult(6, 'h264', 'aac'))
    pipe.run_cycle()
    saved = repo.get(a.id)
    assert saved.state is JobState.HLS_ENCODING
    assert saved.hls_result == 'PASS'
    assert Path(saved.hls_checkpoint_directory).is_dir()
    assert not (pipe.paths.output_directory/'a.zip').exists()
    assert not repo.get(b.id).hls_checkpoint_directory
    assert len(conversions) == 1
    pipe.run_cycle()
    assert remote.events[0] == ('submit', c.id)
    assert repo.get(a.id).state is JobState.COMPLETED
    assert repo.get(b.id).state is JobState.COMPLETED
    assert len(conversions) == 2


def test_two_running_workers_finish_but_no_queued_third_task_starts(tmp_path):
    import threading
    b_started, b_release = threading.Event(), threading.Event()
    jobs = []
    for name in ('a', 'b', 'c'):
        raw = tmp_path/f'{name}.mp4'
        raw.write_bytes(b'raw')
        jobs.append(Job(f'{name}.txt', state=JobState.ENDING, raw_path=str(raw)))
    cloud = Job('cloud.txt')
    repo = MemoryJobs(*jobs, cloud)
    pipe = coordinator(tmp_path, repo, Remote([]))
    clock = [NOW]
    pipe.cloud_limit.clock = lambda: clock[0]
    pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    original = pipe.ending.process
    started = []
    def ending(raw, *args, **kwargs):
        started.append(raw.name)
        if raw.name == 'a.mp4':
            assert b_started.wait(5)
            clock[0] += timedelta(days=1)
        elif raw.name == 'b.mp4':
            b_started.set()
            assert b_release.wait(5)
        return original(raw, *args, **kwargs)
    pipe.ending.process = ending
    def recheck(url):
        b_release.set()
        return True
    pipe.notebook.recheck_cloud_limit = recheck
    try:
        pipe.run_cycle()
    finally:
        b_release.set()
    assert sorted(started) == ['a.mp4','b.mp4']
    assert repo.get(jobs[0].id).state is JobState.HLS_ENCODING
    assert repo.get(jobs[1].id).state is JobState.HLS_ENCODING
    assert repo.get(jobs[2].id).state is JobState.ENDING


@pytest.mark.parametrize('message', [
    'You are near your AI usage limit. Limit resets at 16:21.',
    'You are approaching your AI usage limit.',
    'AI使用量上限が近い状態ですが、まだ利用可能です。',
    'AI使用量上限に近づいています。',
])
def test_near_warning_transient_disabled_is_not_a_real_limit(message):
    from djd_maker.core.cloud_limit import is_limit_warning
    assert is_limit_warning(message)
    assert parse_limit_text(message, now=NOW, chat_disabled=True) is None


def test_near_warning_does_not_mask_explicit_hard_limit():
    assert parse_limit_text('AI使用量上限が近い。チャットは 6:21 まで無効', now=NOW, chat_disabled=True)


def test_one_job_progress_is_not_counted_again_for_each_local_stage(tmp_path):
    raw = tmp_path/'raw.mp4'
    raw.write_bytes(b'raw')
    job = Job('one.txt', state=JobState.RAW_READY, raw_path=str(raw))
    pipe = coordinator(tmp_path, MemoryJobs(job), Remote([]))
    pipe.run_cycle()
    assert pipe._media_completed == 1
