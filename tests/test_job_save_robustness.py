from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from djd_maker.core.models import InvalidStateTransition, Job, JobState
from djd_maker.core.repositories import JobRepository, JobStateSaveError


def _job(index: int = 0) -> Job:
    job = Job(f"input/授業 {index}.txt", id="job")
    job.progress_percent = index
    return job


def _windows_error(code: int) -> OSError:
    # Keep this as a plain OSError to verify winerror classification independently
    # from Python's errno-to-PermissionError constructor mapping.
    error = OSError("sharing/permission violation")
    error.winerror = code
    return error


def test_job_save_single_uses_versioned_json_without_lock_file(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(_job(1))

    assert repository.require("job").progress_percent == 1
    assert json.loads((tmp_path / "jobs" / "job.json").read_text(encoding="utf-8"))[
        "kind"
    ] == "job"
    assert not list((tmp_path / "jobs").glob("*.lock"))


def test_job_save_rapid_repeated_keeps_latest_valid_json(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs")

    for index in range(100):
        repository.save(_job(index))

    assert repository.require("job").progress_percent == 99
    assert not list((tmp_path / "jobs").glob(".*.tmp"))


def test_job_save_multithread_is_serialized_by_resolved_path(tmp_path: Path) -> None:
    directory = tmp_path / "jobs"
    repositories = [JobRepository(directory) for _ in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: repositories[index % len(repositories)].save(_job(index)),
                range(24),
            )
        )

    restored = JobRepository(directory).require("job")
    assert restored.progress_percent in range(24)
    assert json.loads((directory / "job.json").read_text(encoding="utf-8"))["job"][
        "id"
    ] == "job"
    assert not list(directory.glob("*.lock"))


def test_job_publish_source_handle_is_closed_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    real_replace = storage.os.replace
    reopened: list[bool] = []

    def inspect_then_replace(source: Path, destination: Path) -> None:
        with Path(source).open("r+", encoding="utf-8") as stream:
            reopened.append(not stream.closed)
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", inspect_then_replace)
    JobRepository(tmp_path / "jobs").save(_job())

    assert reopened == [True]


@pytest.mark.parametrize("winerror", [5, 32])
def test_job_transient_windows_publish_error_retries_and_reports_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    winerror: int,
) -> None:
    from djd_maker.core import storage

    path = tmp_path / "jobs" / "job.json"
    real_replace = storage.os.replace
    attempts = 0
    sleeps: list[float] = []
    events: list[tuple[str, int, int, Path, int | None]] = []

    def transient(source: Path, destination: Path) -> None:
        nonlocal attempts
        if Path(destination) == path:
            attempts += 1
            if attempts <= 2:
                raise _windows_error(winerror)
        real_replace(source, destination)

    def observe(event, attempt, total, destination, error) -> None:
        events.append((event, attempt, total, destination, getattr(error, "winerror", None)))

    monkeypatch.setattr(storage.os, "replace", transient)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    with caplog.at_level("INFO", logger="djd_maker.core.storage"):
        JobRepository(tmp_path / "jobs", retry_observer=observe).save(_job(2))

    assert attempts == 3
    assert sleeps == [0.1, 0.2]
    assert [(event, attempt, total) for event, attempt, total, _, _ in events] == [
        ("retry", 1, 7),
        ("retry", 2, 7),
        ("recovered", 3, 7),
    ]
    assert "transient sharing/permission violation" in caplog.text
    assert "recovered successfully" in caplog.text
    assert JobRepository(tmp_path / "jobs").require("job").progress_percent == 2


def test_job_plain_permission_error_is_retried(tmp_path: Path, monkeypatch) -> None:
    from djd_maker.core import storage

    real_replace = storage.os.replace
    calls = 0

    def transient(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(13, "denied", str(destination))
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", transient)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    JobRepository(tmp_path / "jobs").save(_job())
    assert calls == 2


def test_committed_replace_error_republishes_fsynced_payload_when_verification_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    real_replace = storage.os.replace
    replace_calls = 0
    verification_calls = 0
    sleeps: list[float] = []

    def committed_then_error(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        real_replace(source, destination)
        raise _windows_error(5)

    real_match = storage.JsonStore._published_content_matches

    def temporarily_unreadable(source: Path, destination: Path, expected: bytes) -> bool:
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls <= 2:
            return False
        return real_match(source, destination, expected)

    monkeypatch.setattr(storage.os, "replace", committed_then_error)
    monkeypatch.setattr(storage.JsonStore, "_published_content_matches", staticmethod(temporarily_unreadable))
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)

    JobRepository(tmp_path / "jobs").save(_job(5))

    assert replace_calls == 2
    assert sleeps == [0.1, 0.2]
    assert JobRepository(tmp_path / "jobs").require("job").progress_percent == 5


def test_committed_replace_unverifiable_exhaustion_raises_original_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    real_replace = storage.os.replace
    original = _windows_error(32)
    replace_calls = 0

    def committed_then_error(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        real_replace(source, destination)
        raise original

    monkeypatch.setattr(storage.os, "replace", committed_then_error)
    monkeypatch.setattr(
        storage.JsonStore,
        "_published_content_matches",
        staticmethod(lambda _source, _destination, _expected: False),
    )
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)

    with pytest.raises(JobStateSaveError) as raised:
        JobRepository(tmp_path / "jobs").save(_job())

    assert replace_calls == 4
    assert raised.value.__cause__ is original


def test_job_non_transient_os_error_is_not_retried_or_reclassified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    calls = 0
    sleeps: list[float] = []

    def disk_failure(_source: Path, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError(28, "no space left")

    monkeypatch.setattr(storage.os, "replace", disk_failure)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)

    with pytest.raises(OSError, match="no space left") as raised:
        JobRepository(tmp_path / "jobs").save(_job())

    assert not isinstance(raised.value, JobStateSaveError)
    assert calls == 1
    assert sleeps == []


def test_job_retry_observer_failure_does_not_break_recovered_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    real_replace = storage.os.replace
    calls = 0

    def transient(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _windows_error(32)
        real_replace(source, destination)

    def broken_observer(*_args) -> None:
        raise RuntimeError("observer unavailable")

    monkeypatch.setattr(storage.os, "replace", transient)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    JobRepository(tmp_path / "jobs", retry_observer=broken_observer).save(_job(6))
    assert JobRepository(tmp_path / "jobs").require("job").progress_percent == 6


def test_job_retry_exhaustion_preserves_primary_and_ready_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    directory = tmp_path / "jobs"
    path = directory / "job.json"
    repository = JobRepository(directory)
    repository.save(_job(1))
    original = path.read_bytes()
    real_replace = storage.os.replace
    attempts = 0

    def blocked(source: Path, destination: Path) -> None:
        nonlocal attempts
        if Path(destination) == path:
            attempts += 1
            raise _windows_error(5)
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)

    with pytest.raises(JobStateSaveError, match="ジョブ状態を保存できませんでした"):
        repository.save(_job(2))

    assert attempts == 7
    assert path.read_bytes() == original
    temporaries = list(directory.glob(".job.json.*.tmp"))
    assert len(temporaries) == 1
    assert json.loads(temporaries[0].read_text(encoding="utf-8"))["job"][
        "progress_percent"
    ] == 2


def test_job_backup_publish_retries_and_keeps_previous_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    directory = tmp_path / "jobs"
    backup = directory / "job.json.bak"
    repository = JobRepository(directory)
    repository.save(_job(3))
    real_replace = storage.os.replace
    backup_attempts = 0

    def transient_backup(source: Path, destination: Path) -> None:
        nonlocal backup_attempts
        if Path(destination) == backup:
            backup_attempts += 1
            if backup_attempts == 1:
                raise _windows_error(32)
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", transient_backup)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    repository.save(_job(4))

    assert backup_attempts == 2
    assert repository.require("job").progress_percent == 4
    assert json.loads(backup.read_text(encoding="utf-8"))["job"]["progress_percent"] == 3


def test_job_save_dropbox_japanese_space_deep_path(tmp_path: Path) -> None:
    directory = tmp_path / "Dropbox" / "日本語 保存" / "深い" / "階層" / "system" / "jobs"
    JobRepository(directory).save(_job(7))
    assert JobRepository(directory).require("job").progress_percent == 7


def test_simultaneous_job_state_transition_keeps_json_valid(tmp_path: Path) -> None:
    directory = tmp_path / "jobs"
    JobRepository(directory).save(_job())

    def transition(_index: int) -> str:
        try:
            JobRepository(directory).transition("job", JobState.UPLOADING)
        except InvalidStateTransition:
            return "stale"
        return "saved"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(transition, range(8)))

    assert results.count("saved") == 1
    assert results.count("stale") == 7
    assert JobRepository(directory).require("job").state is JobState.UPLOADING
    assert json.loads((directory / "job.json").read_text(encoding="utf-8"))["kind"] == "job"


def test_job_restart_recovers_latest_flushed_temporary_after_publish_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    directory = tmp_path / "jobs"
    path = directory / "job.json"
    repository = JobRepository(directory)
    repository.save(_job(8))
    real_replace = storage.os.replace

    def blocked(source: Path, destination: Path) -> None:
        if Path(destination) == path:
            raise _windows_error(32)
        real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    with pytest.raises(JobStateSaveError):
        repository.save(_job(9))

    monkeypatch.setattr(storage.os, "replace", real_replace)
    assert JobRepository(directory).require("job").progress_percent == 8

    path.unlink()
    assert JobRepository(directory).require("job").progress_percent == 9
    assert path.is_file()
