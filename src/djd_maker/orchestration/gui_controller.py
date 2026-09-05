from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings

from .pipeline import PipelineCoordinator
from .scheduler import PersistentPollScheduler, SchedulerMode


class GuiPipelineController:
    """Composition boundary that drives Pipeline outside the Qt UI thread."""

    def __init__(
        self,
        *,
        jobs: Any,
        settings: AppSettings,
        app_root: Path,
        pipeline: PipelineCoordinator | None,
        scheduler: PersistentPollScheduler,
        pipeline_factory: Callable[[], PipelineCoordinator] | None = None,
        cleanup: Callable[[], None] | None = None,
        settings_provider: Callable[[], AppSettings] | None = None,
        manual_login: Callable[[], int] | None = None,
        cycle_interval_seconds: float = 0.25,
    ) -> None:
        if cycle_interval_seconds <= 0:
            raise ValueError("cycle_interval_seconds must be positive")
        self.jobs = jobs
        self.settings = settings
        self.app_root = app_root.resolve()
        if pipeline is None and pipeline_factory is None:
            raise ValueError("pipeline or pipeline_factory is required")
        self.pipeline = pipeline
        self.pipeline_factory = pipeline_factory
        self.cleanup = cleanup or (lambda: None)
        self.settings_provider = settings_provider
        self.manual_login = manual_login
        self.scheduler = scheduler
        if self.pipeline is not None:
            self.pipeline.scheduler = scheduler
        self.cycle_interval_seconds = cycle_interval_seconds
        self._stop_event = threading.Event()
        self._guard = threading.RLock()
        self._worker: threading.Thread | None = None
        self._paused = False
        self._jobs_callback: Callable[[object], None] = lambda _value: None
        self._status_callback: Callable[[object], None] = lambda _value: None
        self._log_callback: Callable[[object], None] = lambda _value: None
        self._error_callback: Callable[[str, str], None] = lambda _op, _message: None
        self._last_states: dict[str, JobState] = {}

    def bind(
        self,
        *,
        jobs: Callable[[object], None],
        status: Callable[[object], None],
        log: Callable[[object], None],
        error: Callable[[str, str], None],
    ) -> None:
        self._jobs_callback = jobs
        self._status_callback = status
        self._log_callback = log
        self._error_callback = error

    def _input_directory(self) -> Path:
        settings = self.settings_provider() if self.settings_provider else self.settings
        self.settings = settings
        value = Path(settings.input_directory)
        return value.resolve() if value.is_absolute() else (self.app_root / value).resolve()

    def reload(self) -> list[Job]:
        source_root = self._input_directory()
        source_root.mkdir(parents=True, exist_ok=True)
        existing = {str(Path(job.source_path).resolve()) for job in self.jobs.list()}
        for source in sorted(source_root.glob("*.txt"), key=lambda item: item.name.casefold()):
            resolved = str(source.resolve())
            if resolved not in existing and source.is_file():
                self.jobs.save(Job(resolved))
                existing.add(resolved)
        values = self.jobs.list()
        self._jobs_callback(values)
        self._publish_status(values)
        return values

    def login(self) -> int:
        if self.status()["running"]:
            raise RuntimeError("処理中はGoogleログイン用Chromeを別起動できません")
        if self.manual_login is None:
            raise RuntimeError("Googleログイン処理が構成されていません")
        return self.manual_login()

    def start(self) -> dict[str, object]:
        with self._guard:
            if self._worker is not None and self._worker.is_alive():
                self._paused = False
                self.scheduler.resume()
                return self.status()
            self._paused = False
            self._stop_event.clear()
            self.scheduler.start()
            self._worker = threading.Thread(
                target=self._run_loop,
                name="djd-pipeline-controller",
                daemon=False,
            )
            self._worker.start()
        return self.status()

    def pause(self) -> dict[str, object]:
        with self._guard:
            self._paused = True
            self.scheduler.pause()
        self._publish_status()
        return self.status()

    def stop(self) -> dict[str, object]:
        self.scheduler.stop()
        self._stop_event.set()
        with self._guard:
            worker = self._worker
        if worker is None:
            self.cleanup()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)
        with self._guard:
            if worker is None or not worker.is_alive():
                self._worker = None
            self._paused = False
        self._publish_status()
        return self.status()

    def shutdown(self) -> None:
        result = self.stop()
        if result["running"]:
            raise RuntimeError("pipeline worker did not stop safely")

    def retry(self, job_id: str, stage: str) -> Job:
        if self.pipeline is None:
            raise RuntimeError("pipeline must be started before retry")
        if stage == "download":
            result = self.pipeline.retry_download(job_id)
        else:
            restart = {
                "job": JobState.WAITING,
                "ending": JobState.RAW_READY,
                "hls": JobState.HLS_ENCODING,
            }.get(stage)
            if restart is None:
                raise ValueError(f"unsupported retry stage: {stage}")
            result = self.pipeline.create_retry(job_id, restart)
        self._jobs_callback(self.jobs.list())
        return result

    def _run_loop(self) -> None:
        try:
            if self.pipeline is None:
                assert self.pipeline_factory is not None
                try:
                    self.pipeline = self.pipeline_factory()
                except Exception as exc:
                    self._log_callback(
                        {"level": "ERROR", "stage": "startup", "message": str(exc)}
                    )
                    self._error_callback("startup", str(exc))
                    return
                self.pipeline.scheduler = self.scheduler
            while not self._stop_event.is_set():
                with self._guard:
                    paused = self._paused
                if not paused:
                    try:
                        self.pipeline.run_cycle()
                    except Exception as exc:
                        self._log_callback(
                            {
                                "level": "ERROR",
                                "stage": "pipeline",
                                "message": str(exc),
                            }
                        )
                        self._error_callback("pipeline", str(exc))
                    values = self.jobs.list()
                    for job in values:
                        previous = self._last_states.get(job.id)
                        if previous is not job.state:
                            self._log_callback(
                                {
                                    "job_id": job.id,
                                    "script_name": job.script_name,
                                    "engine": "DJDmaker",
                                    "stage": job.state.value,
                                    "level": "ERROR" if job.state is JobState.FAILED else "INFO",
                                    "message": (
                                        f"[{job.script_name}] state: "
                                        f"{previous.value if previous else 'NEW'} -> {job.state.value}"
                                    ),
                                }
                            )
                            self._last_states[job.id] = job.state
                    self._jobs_callback(values)
                    self._publish_status(values)
                    if values and all(
                        job.state in {JobState.COMPLETED, JobState.FAILED}
                        for job in values
                    ):
                        break
                self._stop_event.wait(self.cycle_interval_seconds)
        finally:
            # No terminal job can require another Notebook poll. Keep scheduler
            # state aligned with the stopped worker after natural completion too.
            self.scheduler.stop()
            try:
                self.cleanup()
            except Exception as exc:
                self._log_callback(
                    {"level": "WARNING", "stage": "shutdown", "message": str(exc)}
                )
            if self.pipeline_factory is not None:
                self.pipeline = None
            with self._guard:
                self._worker = None
            self._publish_status()

    def status(self) -> dict[str, object]:
        values = self.jobs.list()
        with self._guard:
            worker_running = self._worker is not None and self._worker.is_alive()
            paused = self._paused
        pollable = [
            job
            for job in values
            if job.state in self.scheduler.POLLABLE_STATES and job.next_poll_at
        ]
        remaining = (
            min(self.scheduler.remaining_seconds(job) for job in pollable)
            if pollable
            else None
        )
        return {
            "running": worker_running and not paused,
            "paused": paused,
            "scheduler_mode": self.scheduler.mode.value,
            "next_check": "－" if remaining is None else f"{max(0, int(remaining))}秒",
        }

    def _publish_status(self, values: list[Job] | None = None) -> None:
        self._status_callback(self.status())
