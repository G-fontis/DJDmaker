from __future__ import annotations

import json
import threading

from djd_maker.core.download_guard import (
    DeferredTaskType,
    DownloadLifecycleGuard,
)


def test_close_is_deferred_until_download_finishes(tmp_path):
    guard = DownloadLifecycleGuard(tmp_path / "queue.json")
    events = []
    with guard.transfer("job-1"):
        assert guard.state == "RUNNING_DOWNLOAD"
        assert guard.run_or_defer(
            DeferredTaskType.BROWSER_CLOSE, lambda: events.append("closed"),
            command_id="close-1",
        ) is False
        assert events == []
        assert guard.active_download_count == 1
    assert events == ["closed"]
    assert guard.active_download_count == 0


def test_stop_has_priority_and_prevents_next_job_from_running_first(tmp_path):
    guard = DownloadLifecycleGuard(tmp_path / "queue.json")
    events = []
    with guard.transfer("job-1"):
        guard.defer(DeferredTaskType.NEXT_JOB, command_id="next", callback=lambda: events.append("next"))
        guard.defer(DeferredTaskType.USER_STOP, command_id="stop", callback=lambda: events.append("stop"))
    assert events == ["stop", "next"]


def test_queue_deduplicates_command_identity(tmp_path):
    guard = DownloadLifecycleGuard(tmp_path / "queue.json")
    with guard.transfer("job-1"):
        first = guard.defer(DeferredTaskType.NEXT_JOB, job_id="job-1", command_id="same")
        second = guard.defer(DeferredTaskType.NEXT_JOB, job_id="job-1", command_id="same")
        assert first == second
        assert len(guard.pending) == 1


def test_restart_drops_runtime_close_but_keeps_recovery_work(tmp_path):
    path = tmp_path / "queue.json"
    guard = DownloadLifecycleGuard(path)
    with guard.transfer("job-1"):
        guard.defer(DeferredTaskType.BROWSER_CLOSE, command_id="runtime")
        guard.defer(DeferredTaskType.MANUAL_SAVE_RETRY, job_id="job-1", command_id="durable")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [value["task_type"] for value in saved] == ["MANUAL_SAVE_RETRY"]
    restored = DownloadLifecycleGuard(path)
    assert [task.task_type for task in restored.pending] == ["MANUAL_SAVE_RETRY"]


def test_only_one_download_can_be_active(tmp_path):
    guard = DownloadLifecycleGuard(tmp_path / "queue.json")
    entered = threading.Event()
    release = threading.Event()
    counts = []

    def first():
        with guard.transfer("one"):
            counts.append(guard.active_download_count)
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(2)
    assert guard.active_download_count == 1
    release.set()
    thread.join(2)
    assert counts == [1]
    assert guard.active_download_count == 0
