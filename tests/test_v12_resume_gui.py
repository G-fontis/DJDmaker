import pytest
from PySide6.QtCore import Qt

from djd_maker.core.models import Job, JobState
from test_pipeline import MemoryJobs, coordinator
from test_gui import _window


@pytest.mark.parametrize("status,expected", [
    ("READY", JobState.DOWNLOAD_PENDING),
    ("GENERATING", JobState.WAITING_VIDEO),
    ("WAITING", JobState.RESERVED_WAITING_CREDIT_RESET),
    ("NOT_STARTED", JobState.WAITING),
    ("UNKNOWN", JobState.FAILED),
])
def test_failed_resume_by_remote_checkpoint_without_new_job(tmp_path, status, expected):
    class Notebook:
        def inspect_status(self, job):
            return status
        def submit(self, job):
            pytest.fail("diagnosis must not generate")
    job = Job("TAX1.txt", state=JobState.FAILED, notebook_id="remote", notebook_url="https://notebook.google.com/notebook/remote", error_code="PRESET_SEND_FAILED")
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Notebook())
    pipeline.resume_failed_jobs()
    result = jobs.get(job.id)
    assert result.state is expected
    assert result.notebook_id == "remote"
    assert len(jobs.list()) == 1


def test_completed_not_diagnosed_or_regenerated(tmp_path):
    class Notebook:
        def inspect_status(self, job):
            pytest.fail("completed is immutable")
    job = Job("TAX1.txt", state=JobState.COMPLETED)
    jobs = MemoryJobs(job)
    pipeline = coordinator(tmp_path, jobs, Notebook())
    assert pipeline.resume_failed_jobs() == []
    assert jobs.get(job.id).state is JobState.COMPLETED


@pytest.mark.parametrize("diagnosis,classification", [
    ({"artifact": "NOT_STARTED", "source": "READY", "preset": "SENT", "reply": "QUOTA_EXHAUSTED"}, "QUOTA_RECOVERY_PENDING"),
    ({"artifact": "NOT_STARTED", "source": "READY", "preset": "NOT_SENT", "reply": "NO_RESPONSE"}, "PRESET_SEND_FAILED"),
    ({"artifact": "NOT_STARTED", "source": "ERROR", "preset": "NOT_SENT", "reply": "NO_RESPONSE"}, "SOURCE_UPLOAD_FAILED"),
])
def test_remote_diagnosis_updates_failure_cause_and_restarts_current_attempt(tmp_path, diagnosis, classification):
    class Notebook:
        def diagnose_resume(self, job):
            return diagnosis
    job = Job("TAX1.txt", state=JobState.FAILED, notebook_id="remote", notebook_url="https://notebook.google.com/notebook/remote", error_code="NOTEBOOK_STAGE_FAILED")
    jobs = MemoryJobs(job)
    coordinator(tmp_path, jobs, Notebook()).resume_failed_jobs()
    result = jobs.get(job.id)
    assert result.failure_class == classification
    assert result.state is JobState.WAITING
    assert len(jobs.list()) == 1


def test_full_dataset_natural_sort_and_checkbox_identity(tmp_path):
    jobs = [Job(f"TAX{i}.txt") for i in range(175, 0, -1)]
    window, *_ = _window(tmp_path, jobs)
    try:
        table = window.job_table
        target = jobs[0].id
        row = next(i for i in range(table.rowCount()) if table.item(i, 0).data(Qt.ItemDataRole.UserRole) == target)
        table.item(row, 6).setCheckState(Qt.CheckState.Checked)
        for column in range(table.columnCount()):
            table.sortItems(column, Qt.SortOrder.AscendingOrder)
            table.sortItems(column, Qt.SortOrder.DescendingOrder)
            assert table.rowCount() == 175
            assert target in window._checked_job_ids
        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        assert [table.item(i, 1).text() for i in range(10)] == [f"TAX{i}" for i in range(1, 11)]
        table.selectRow(0)
        assert window._selected_job().script_name == "TAX1"
        window.set_jobs(jobs)
        assert window._checked_job_ids == {target}
        checked = [table.item(i, 6).data(Qt.ItemDataRole.UserRole) for i in range(table.rowCount()) if table.item(i, 6).checkState() is Qt.CheckState.Checked]
        assert checked == [target]
    finally:
        window.close()


def test_recovery_candidate_enabled_even_without_ending(tmp_path):
    window, *_ = _window(tmp_path, [Job("wait.txt", state=JobState.RECOVERY_PENDING)], ending=False)
    try:
        assert window.recover_button.isEnabled()
        window.set_jobs([Job("done.txt", state=JobState.COMPLETED)])
        assert not window.recover_button.isEnabled()
    finally:
        window.close()
