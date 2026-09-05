from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest

from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobRepository
from djd_maker.orchestration.scheduler import (
    InvalidTimestamp,
    PersistentPollScheduler,
    SchedulerMode,
)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def waiting_job() -> Job:
    return Job(
        "input/lesson.txt",
        id="job",
        state=JobState.WAITING_VIDEO,
        notebook_id="remote-id",
        notebook_url="https://example.test/notebook/remote-id",
    )


def test_generation_timestamps_round_trip_through_json_repository(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())

    restarted = repository.require("job")
    assert restarted.generation_started_at == "2026-09-06T01:02:03+00:00"
    assert restarted.next_poll_at == "2026-09-06T01:12:03+00:00"
    assert restarted.last_polled_at is None


def test_first_poll_is_600_seconds_then_every_120_seconds(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    calls: list[str] = []

    clock.advance(599)
    assert scheduler.poll_due(lambda job: calls.append(job.id)) == []
    clock.advance(1)
    assert scheduler.poll_due(lambda job: calls.append(job.id)) == ["job"]
    clock.advance(119)
    assert scheduler.poll_due(lambda job: calls.append(job.id)) == []
    clock.advance(1)
    assert scheduler.poll_due(lambda job: calls.append(job.id)) == ["job"]
    assert calls == ["job", "job"]


def test_restart_preserves_original_deadline(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    PersistentPollScheduler(repository, clock=clock).schedule_generation(waiting_job())
    clock.advance(300)

    restarted = PersistentPollScheduler(repository, clock=clock)
    job = repository.require("job")
    restarted.ensure_scheduled(job)
    assert restarted.remaining_seconds(job) == 300
    assert job.next_poll_at == "2026-09-06T00:10:00+00:00"


def test_old_json_without_scheduler_fields_remains_compatible(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    job = waiting_job()
    data = job.to_dict()
    for name in ("generation_started_at", "next_poll_at", "last_polled_at"):
        data.pop(name)
    path = tmp_path / "jobs" / "job.json"
    path.parent.mkdir()
    path.write_text(
        '{"schema_version": 1, "kind": "job", "job": '
        + __import__("json").dumps(data)
        + "}",
        encoding="utf-8",
    )
    loaded = repository.require("job")
    assert loaded.generation_started_at is None
    assert loaded.next_poll_at is None
    assert loaded.last_polled_at is None


def test_late_wakeup_polls_once_and_never_reports_negative_time(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    clock.advance(3600)

    assert scheduler.remaining_seconds(repository.require("job")) == 0
    assert scheduler.poll_due(lambda _job: None) == ["job"]
    persisted = repository.require("job")
    assert scheduler.remaining_seconds(persisted) == 120
    assert persisted.last_polled_at == "2026-09-06T01:00:00+00:00"


def test_backward_clock_drift_does_not_poll_early(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, 1, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    clock.now -= timedelta(hours=2)
    assert scheduler.remaining_seconds(repository.require("job")) == 7800
    assert scheduler.poll_due(lambda _job: None) == []


def test_pause_resume_and_stop_do_not_move_persisted_deadline(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    deadline = repository.require("job").next_poll_at
    scheduler.pause()
    clock.advance(700)
    assert scheduler.poll_due(lambda _job: None) == []
    scheduler.resume()
    assert scheduler.poll_due(lambda _job: None) == ["job"]
    scheduler.stop()
    assert scheduler.mode is SchedulerMode.STOPPED
    clock.advance(120)
    assert scheduler.poll_due(lambda _job: None) == []
    assert deadline == "2026-09-06T00:10:00+00:00"
    scheduler.start()
    assert scheduler.poll_due(lambda _job: None) == ["job"]


def test_overlapping_ticks_cannot_duplicate_a_remote_poll(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    clock.advance(600)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_poll(job: Job) -> None:
        calls.append(job.id)
        entered.set()
        assert release.wait(timeout=2)

    thread = threading.Thread(target=lambda: scheduler.poll_due(slow_poll))
    thread.start()
    assert entered.wait(timeout=2)
    assert scheduler.poll_due(lambda job: calls.append(job.id)) == []
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert calls == ["job"]


def test_poll_claim_is_persisted_before_callback_and_survives_failure(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    scheduler.schedule_generation(waiting_job())
    clock.advance(600)

    def failing_poll(job: Job) -> None:
        persisted = repository.require(job.id)
        assert persisted.last_polled_at == "2026-09-06T00:10:00+00:00"
        assert persisted.next_poll_at == "2026-09-06T00:12:00+00:00"
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        scheduler.poll_due(failing_poll)
    assert scheduler.poll_due(lambda _job: None) == []


def test_non_pollable_jobs_are_ignored(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
    scheduler = PersistentPollScheduler(repository, clock=clock)
    job = waiting_job()
    job.state = JobState.RAW_READY
    scheduler.schedule_generation(job)
    clock.advance(1000)
    assert scheduler.poll_due(lambda _job: pytest.fail("must not poll")) == []


def test_offset_timestamps_are_normalized_and_naive_clock_is_rejected(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    offset = timezone(timedelta(hours=9))
    scheduler = PersistentPollScheduler(
        repository, clock=FakeClock(datetime(2026, 9, 6, 9, tzinfo=offset))
    )
    scheduler.schedule_generation(waiting_job())
    assert repository.require("job").generation_started_at == "2026-09-06T00:00:00+00:00"

    naive = PersistentPollScheduler(
        repository, clock=FakeClock(datetime(2026, 9, 6))
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        naive.schedule_generation(waiting_job())


def test_invalid_persisted_timestamp_fails_explicitly(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    job = waiting_job()
    job.generation_started_at = "not-a-time"
    repository.save(job)
    scheduler = PersistentPollScheduler(repository)
    with pytest.raises(InvalidTimestamp, match="generation_started_at"):
        scheduler.ensure_scheduled(repository.require("job"))
