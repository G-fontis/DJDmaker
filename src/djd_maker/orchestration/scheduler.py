from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from djd_maker.core.models import Job, JobState


class JobRepositoryPort(Protocol):
    def save(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(self) -> list[Job]: ...


class SchedulerMode(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class InvalidTimestamp(ValueError):
    pass


def system_utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _as_utc(parsed)
    except (TypeError, ValueError) as error:
        raise InvalidTimestamp(f"invalid {field_name}: {value!r}") from error


class PersistentPollScheduler:
    """Deadline scheduler for Notebook polling with no GUI/event-loop dependency.

    The caller drives :meth:`poll_due` from a GUI timer, service loop, or test.
    This class never sleeps and persists a poll claim before invoking remote I/O.
    """

    POLLABLE_STATES = frozenset({JobState.GENERATING, JobState.WAITING_VIDEO})

    def __init__(
        self,
        jobs: JobRepositoryPort,
        *,
        first_poll_seconds: int = 600,
        subsequent_poll_seconds: int = 120,
        clock: Callable[[], datetime] = system_utc_now,
    ) -> None:
        if first_poll_seconds < 1:
            raise ValueError("first_poll_seconds must be positive")
        if subsequent_poll_seconds < 1:
            raise ValueError("subsequent_poll_seconds must be positive")
        self.jobs = jobs
        self.first_poll_seconds = first_poll_seconds
        self.subsequent_poll_seconds = subsequent_poll_seconds
        self._clock = clock
        self._mode = SchedulerMode.RUNNING
        self._guard = threading.RLock()
        self._tick_guard = threading.Lock()
        self._in_flight: set[str] = set()

    @property
    def mode(self) -> SchedulerMode:
        with self._guard:
            return self._mode

    def pause(self) -> None:
        with self._guard:
            if self._mode is SchedulerMode.RUNNING:
                self._mode = SchedulerMode.PAUSED

    def resume(self) -> None:
        with self._guard:
            if self._mode is SchedulerMode.PAUSED:
                self._mode = SchedulerMode.RUNNING

    def stop(self) -> None:
        with self._guard:
            self._mode = SchedulerMode.STOPPED

    def start(self) -> None:
        """Explicitly start again after stop; persisted deadlines stay unchanged."""

        with self._guard:
            self._mode = SchedulerMode.RUNNING

    def schedule_generation(self, job: Job, *, force: bool = False) -> Job:
        """Persist the generation origin and its first 600-second deadline.

        Repeated notifications are idempotent by default so they cannot postpone
        the first inspection deadline.
        """

        now = _as_utc(self._clock())
        if force or job.generation_started_at is None:
            job.generation_started_at = _format_timestamp(now)
        started = _parse_timestamp(job.generation_started_at, "generation_started_at")
        if force or job.next_poll_at is None:
            job.next_poll_at = _format_timestamp(
                started + timedelta(seconds=self.first_poll_seconds)
            )
        self.jobs.save(job)
        return job

    start_generation = schedule_generation

    def ensure_scheduled(self, job: Job) -> Job:
        """Backfill old persisted jobs without changing an existing deadline."""

        if job.generation_started_at is None:
            return self.schedule_generation(job)
        _parse_timestamp(job.generation_started_at, "generation_started_at")
        if job.next_poll_at is None:
            started = _parse_timestamp(job.generation_started_at, "generation_started_at")
            job.next_poll_at = _format_timestamp(
                started + timedelta(seconds=self.first_poll_seconds)
            )
            self.jobs.save(job)
        else:
            _parse_timestamp(job.next_poll_at, "next_poll_at")
        return job

    def remaining_seconds(self, job: Job, *, now: datetime | None = None) -> float:
        if job.next_poll_at is None:
            self.ensure_scheduled(job)
        deadline = _parse_timestamp(job.next_poll_at or "", "next_poll_at")
        current = _as_utc(now if now is not None else self._clock())
        # A late wake-up or forward clock correction must never leak a negative
        # countdown into the UI.
        return max(0.0, (deadline - current).total_seconds())

    def is_due(self, job: Job, *, now: datetime | None = None) -> bool:
        return self.remaining_seconds(job, now=now) == 0.0

    def poll_due(self, poll: Callable[[Job], None]) -> list[str]:
        """Poll each due job at most once during a scheduler tick.

        A non-blocking tick lock drops overlapping GUI timer callbacks. The next
        deadline and last poll time are saved before external I/O, preventing a
        second timer callback from issuing the same poll.
        """

        with self._guard:
            if self._mode is not SchedulerMode.RUNNING:
                return []
        if not self._tick_guard.acquire(blocking=False):
            return []
        polled: list[str] = []
        try:
            for job in self.jobs.list():
                with self._guard:
                    if self._mode is not SchedulerMode.RUNNING:
                        break
                    if job.id in self._in_flight:
                        continue
                if job.state not in self.POLLABLE_STATES:
                    continue
                self.ensure_scheduled(job)
                now = _as_utc(self._clock())
                if not self.is_due(job, now=now):
                    continue
                with self._guard:
                    if job.id in self._in_flight:
                        continue
                    self._in_flight.add(job.id)
                try:
                    job.last_polled_at = _format_timestamp(now)
                    # Schedule from actual wake time. This avoids a burst of catch-up
                    # polls after sleep, suspend, or a forward clock correction.
                    job.next_poll_at = _format_timestamp(
                        now + timedelta(seconds=self.subsequent_poll_seconds)
                    )
                    self.jobs.save(job)
                    polled.append(job.id)
                    poll(job)
                finally:
                    with self._guard:
                        self._in_flight.discard(job.id)
        finally:
            self._tick_guard.release()
        return polled

    tick = poll_due


NotebookPollScheduler = PersistentPollScheduler
