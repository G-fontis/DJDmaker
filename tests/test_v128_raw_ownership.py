import pytest

from djd_maker.core.models import Job, JobState
from test_pipeline import MemoryJobs, coordinator
from djd_maker.testing.fake_notebook import FakeNotebookAdapter


@pytest.mark.parametrize('entrypoint', ['run_cycle', 'run_recovery_cycle'])
@pytest.mark.parametrize('shared_field', ['raw_path', 'edited_path'])
def test_shared_raw_is_not_processed_as_two_different_jobs(tmp_path, entrypoint, shared_field):
    raw = tmp_path/'shared.mp4'
    raw.write_bytes(b'one remote video')
    a = Job('a.txt', state=JobState.RAW_READY, raw_path=str(raw))
    b = Job('b.txt', state=JobState.HLS_ENCODING, raw_path=str(raw), edited_path=str(raw))
    if shared_field == 'edited_path':
        own_raw = tmp_path/'b-raw.mp4'
        own_raw.write_bytes(b'different raw')
        b.raw_path = str(own_raw)
    jobs = MemoryJobs(a, b)
    remote = FakeNotebookAdapter({})
    pipe = coordinator(tmp_path, jobs, remote)
    getattr(pipe, entrypoint)()
    assert all(j.failure_class == 'OUTPUT_BLOCKED' for j in jobs.list())
    assert not pipe.ending.calls
    assert not remote.submit_calls and not remote.download_calls
    assert not list((tmp_path/'output').glob('*.zip'))
    assert raw.read_bytes() == b'one remote video'
