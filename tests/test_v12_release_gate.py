"""Release gate fixtures: no Google calls or production runtime writes."""
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import pytest

from djd_maker.adapters.credit import CreditSnapshot, CreditState
from djd_maker.adapters.notebook import GenerationOutcome, NotebookSubmissionResult, RemoteVideoStatus
from djd_maker.core.models import Job, JobState, Preset
from test_pipeline import MemoryJobs, coordinator
from test_gui import _window


@pytest.mark.parametrize("quota_still_exhausted", [False, True])
def test_175_resume_93_unsent_7_old_quota_completed_75_untouched(tmp_path, quota_still_exhausted):
    preset = Preset("original", "original", "original snapshot", "created", "updated")
    jobs = []
    for i in range(175):
        job = Job(f"TAX{i}.txt", id=f"job{i}", state=JobState.COMPLETED if i < 75 else JobState.FAILED,
                  notebook_id=f"nb{i}", notebook_url=f"https://notebook.google.com/notebook/nb{i}",
                  error_code=None if i < 75 else "PRESET_SEND_FAILED" if i < 168 else "QUOTA_ERROR")
        job.snapshot_preset(preset)
        jobs.append(job)
    completed_before = [j.to_dict() for j in jobs[:75]]
    calls = Counter()

    class Notebook:
        def diagnose_resume(self, job):
            assert job.state is JobState.FAILED
            calls["diagnose"] += 1
            return {"artifact": "NOT_STARTED", "source": "READY", "preset": "NOT_SENT" if int(job.id[3:]) < 168 else "SENT",
                    "reply": "NO_RESPONSE" if int(job.id[3:]) < 168 else "QUOTA_EXHAUSTED"}

        def submit(self, job):
            assert job.notebook_id == f"nb{job.id[3:]}"
            assert job.require_preset_body_snapshot() == "original snapshot"
            calls[job.id] += 1
            reserved = quota_still_exhausted and int(job.id[3:]) >= 168
            return NotebookSubmissionResult(job.notebook_id, job.notebook_url,
                GenerationOutcome(RemoteVideoStatus.WAITING if reserved else RemoteVideoStatus.GENERATING,
                    reserved, CreditSnapshot(CreditState.EXHAUSTED if reserved else CreditState.UNKNOWN),
                    datetime.now(timezone.utc) if reserved else None))

        def inspect_status(self, job):
            assert int(job.id[3:]) >= 75
            return "GENERATING"

    repository = MemoryJobs(*jobs)
    pipeline = coordinator(tmp_path, repository, Notebook())
    started = time.perf_counter()
    assert len(pipeline.resume_failed_jobs()) == 100
    pipeline.run_cycle()
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"175-job fixture took {elapsed:.3f}s"
    assert [repository.get(j.id).to_dict() for j in jobs[:75]] == completed_before
    assert calls["diagnose"] == 100
    assert all(calls[j.id] == 1 for j in jobs[75:])
    assert {j.id for j in repository.list()} == {j.id for j in jobs}
    counts = Counter(j.state for j in repository.list())
    assert counts[JobState.COMPLETED] == 75
    assert counts[JobState.WAITING_VIDEO] == (93 if quota_still_exhausted else 100)
    assert counts[JobState.RESERVED_WAITING_CREDIT_RESET] == (7 if quota_still_exhausted else 0)
    for j in jobs[168:]:
        if quota_still_exhausted:
            assert repository.get(j.id).artifact_status == "SCHEDULED_REMOTE"


@pytest.mark.parametrize("state", [JobState.RESERVED_WAITING_CREDIT_RESET, JobState.RECOVERY_PENDING,
                                    JobState.WAITING_VIDEO, JobState.DOWNLOAD_PENDING])
def test_recovery_button_and_cycle_never_submit(state, tmp_path):
    job = Job("pending.txt", state=state, notebook_id="existing", notebook_url="https://notebook.google.com/notebook/existing")
    window, *_ = _window(tmp_path / "gui", [job], ending=False)
    try:
        assert window.recover_button.isEnabled()
    finally:
        window.close()
    class Notebook:
        def inspect_status(self, current):
            assert current.id == job.id
            return "GENERATING"
        def submit(self, _job):
            pytest.fail("Recovery must not submit/create/upload/generate")
    assert coordinator(tmp_path, MemoryJobs(job), Notebook()).run_recovery_cycle() == [job.id]


def test_preset_timeout_keeps_specific_error_code(tmp_path):
    from djd_maker.adapters.chat_flow import ChatFlowError
    class Notebook:
        def submit(self, job):
            raise ChatFlowError("PRESET_RESPONSE_TIMEOUT: 3回の送信試行")
    job = Job("lesson.txt")
    jobs = MemoryJobs(job)
    coordinator(tmp_path, jobs, Notebook()).run_cycle()
    assert jobs.get(job.id).state is JobState.FAILED
    assert jobs.get(job.id).error_code == "PRESET_RESPONSE_TIMEOUT"
