from __future__ import annotations

import threading
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.core.cancellation import CancellationToken, RunCancelled, cancellation_scope, checkpoint
from djd_maker.adapters.notebook_modal import BlockingModalError

from .pipeline import PipelineCoordinator, NoOpJobTransitionError
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
        recovery_pipeline_factory: Callable[[], PipelineCoordinator] | None = None,
        cleanup: Callable[[], None] | None = None,
        abort_owned_external: Callable[[], None] | None = None,
        shutdown_diagnostic: Callable[[], dict] | None = None,
        settings_provider: Callable[[], AppSettings] | None = None,
        manual_login: Callable[[], Any] | None = None,
        browser_status_provider: Callable[[], dict[str, object]] | None = None,
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
        self.recovery_pipeline_factory = recovery_pipeline_factory or pipeline_factory
        self.cleanup = cleanup or (lambda: None)
        self.abort_owned_external = abort_owned_external or (lambda: None)
        self.shutdown_diagnostic = shutdown_diagnostic or (lambda: {})
        self.settings_provider = settings_provider
        self.manual_login = manual_login
        self.browser_status_provider = browser_status_provider
        self.scheduler = scheduler
        if self.pipeline is not None:
            self.pipeline.scheduler = scheduler
        self.cycle_interval_seconds = cycle_interval_seconds
        self.cancellation = CancellationToken()
        self._stop_event = self.cancellation.event
        self._guard = threading.RLock()
        self._worker: threading.Thread | None = None
        self._retiring_worker: threading.Thread | None = None
        self._recovering = False
        self._recovery_done = threading.Event()
        self._recovery_done.set()
        self._paused = False
        self._phase = "idle"
        self._jobs_callback: Callable[[object], None] = lambda _value: None
        self._status_callback: Callable[[object], None] = lambda _value: None
        self._log_callback: Callable[[object], None] = lambda _value: None
        self._error_callback: Callable[[str, str], None] = lambda _op, _message: None
        self._last_states: dict[str, JobState] = {}
        self._runtime: dict[str, object] = {}

    def _runtime_update(self, record: dict) -> None:
        with self._guard:
            previous = self._runtime
            self._runtime = dict(record)
        if any(previous.get(k) != record.get(k) for k in ('job_id', 'stage', 'decision', 'attempt')):
            self._status_callback({**self.status(), 'runtime': dict(record)})
            self._log_callback({'level': 'INFO', 'stage': record.get('stage', ''),
                                'message': record.get('message', record.get('stage', '')),
                                'runtime': dict(record)})

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
        deleted = getattr(self.jobs, "deleted_source_paths", None)
        if callable(deleted):
            existing.update(deleted())
        for source in sorted(source_root.glob("*.txt"), key=lambda item: item.name.casefold()):
            resolved = str(source.resolve())
            if resolved not in existing and source.is_file():
                self.jobs.save(Job(resolved))
                existing.add(resolved)
        values = self.jobs.list()
        self._jobs_callback(values)
        self._publish_status(values)
        return values

    def delete_completed(self, job_ids: list[str]) -> None:
        with self._guard:
            if self._recovering or (self._worker is not None and self._worker.is_alive()):
                raise RuntimeError("処理停止後にジョブを削除してください")
            self.jobs.delete_completed(job_ids)
        self._jobs_callback(self.jobs.list())

    def login(self) -> Any:
        if self.status()["running"]:
            raise RuntimeError("処理中はGoogleログイン画面へ移動できません")
        if self.manual_login is None:
            raise RuntimeError("Googleログイン処理が構成されていません")
        result = self.manual_login()
        self._publish_browser_status("login")
        return result

    def _publish_browser_status(self, stage: str) -> None:
        if self.browser_status_provider is None:
            return
        status = self.browser_status_provider()
        summary = ", ".join(f"{key}={value}" for key, value in status.items())
        self._log_callback(
            {"level": "INFO", "stage": f"browser-{stage}", "message": summary}
        )

    def start(self) -> dict[str, object]:
        with self._guard:
            if self._recovering:
                raise RuntimeError("未回収動画の確認処理が実行中です")
            if self._worker is not None and self._worker.is_alive():
                self._paused = False
                self.scheduler.resume()
                return self.status()
            # A completed run owns a pipeline assembled with that run's preset
            # snapshot and browser page. Recompose on every new Start so a GUI
            # selection/edit made between runs cannot reuse stale preset data.
            if self.pipeline_factory is not None:
                self.pipeline = None
            self._paused = False
            self.cancellation.reset()
            self._phase = "preflight" if self.pipeline_factory is not None else "processing"
            self._worker = threading.Thread(
                target=self._run_loop,
                name="djd-pipeline-controller",
                daemon=False,
            )
            self._worker.start()
        return self.status()

    def recover_pending(self) -> dict[str, object]:
        with cancellation_scope(self.cancellation):
            return self._recover_pending_cancellable()

    def _recover_pending_cancellable(self) -> dict[str, object]:
        """Check persisted recovery jobs once without creating new remote work."""
        with self._guard:
            if self._recovering or (
                self._worker is not None and self._worker.is_alive()
            ):
                raise RuntimeError("別の処理が実行中です")
            factory = self.recovery_pipeline_factory
            if factory is None:
                raise RuntimeError("未回収動画の回収処理が構成されていません")
            self._recovering = True
            self.cancellation.reset()
            self._recovery_done.clear()
            self._phase = "recovery"
        try:
            pipeline = factory()
            pipeline.runtime_callback = self._runtime_update
            pending_before = [
                job
                for job in self.jobs.list()
                if job.state in PipelineCoordinator.RECOVERY_STATES
            ]
            processed = pipeline.run_recovery_cycle()
            values = self.jobs.list()
            self._jobs_callback(values)
            self._publish_status(values)
            return {
                "pending": len(pending_before),
                "checked": len(processed),
            }
        except RunCancelled:
            return {'pending': 0, 'checked': 0, 'stopped': True}
        finally:
            try:
                with cancellation_scope(None):
                    self.cleanup()
            finally:
                with self._guard:
                    self._recovering = False
                    self._phase = "idle"
                    self._recovery_done.set()
                self._publish_status()

    def refresh_credit(self) -> dict[str, object]:
        """Publish persisted/unknown credit state without polling the DOM."""
        result = self.status()
        self._status_callback(result)
        return result

    def pause(self) -> dict[str, object]:
        with self._guard:
            self._paused = True
            self.scheduler.pause()
        self._publish_status()
        return self.status()

    def request_stop(self) -> None:
        self.scheduler.stop()
        self.cancellation.request()
        self._phase = 'STOP_REQUESTED'
        self._runtime_update({**self._runtime, 'stage': 'stop.requested'})

    def stop(self) -> dict[str, object]:
        self.request_stop()
        self._runtime_update({**self._runtime, 'stage': 'stop.wait'})
        with self._guard:
            worker = self._worker or self._retiring_worker
        if (worker is None or not worker.is_alive()) and not self._recovering:
            self.cleanup()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=6)
            if worker.is_alive():
                self._log_callback({'level':'WARNING', 'stage':'shutdown-fallback',
                    'message':json.dumps({**self.cancellation.diagnostic(), **self.shutdown_diagnostic()})})
                self.abort_owned_external()
                worker.join(timeout=4)
        if self._recovering:
            if not self._recovery_done.wait(timeout=6):
                self.abort_owned_external()
                self._recovery_done.wait(timeout=4)
        with self._guard:
            if worker is None or not worker.is_alive():
                self._worker = None
                self._phase = 'STOPPED'
            self._paused = False
        self._publish_status()
        if self._phase == 'STOPPED':
            self._runtime_update({**self._runtime, 'stage': 'stop.complete'})
        return self.status()

    def shutdown(self) -> None:
        result = self.stop()
        if result["running"]:
            self._log_callback({'level':'ERROR', 'stage':'shutdown-timeout', 'message':json.dumps(self.cancellation.diagnostic())})
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
        with cancellation_scope(self.cancellation):
            self._run_loop_cancellable()

    def _run_loop_cancellable(self) -> None:
        cleanup_browser = True
        try:
            checkpoint('pipeline.start')
            self._runtime_update({'stage': 'preflight', 'phase': '開始前確認', 'job': '－'})
            if self.pipeline is None:
                assert self.pipeline_factory is not None
                try:
                    self.pipeline = self.pipeline_factory()
                except Exception as exc:
                    cleanup_browser = not bool(
                        getattr(exc, "preserve_browser", False)
                    )
                    self._log_callback(
                        {"level": "ERROR", "stage": "startup", "message": str(exc)}
                    )
                    self._publish_browser_status("preflight-failed")
                    self._error_callback("startup", str(exc))
                    return
                self._publish_browser_status("start")
                self.pipeline.scheduler = self.scheduler
                # Normal operation must remain Login -> Start only. Discover
                # pre-existing input TXT internally after the side-effect-free
                # browser gate succeeds, before the first pipeline cycle.
                self.reload()
            reconcile = getattr(self.pipeline, "reconcile_completed_txt", None)
            if callable(reconcile):
                reconcile()
            self.pipeline.runtime_callback = self._runtime_update
            begin = getattr(self.pipeline, 'begin_run', None)
            if callable(begin):
                begin()
            self.scheduler.start()
            self._phase = "processing"
            while not self._stop_event.is_set():
                with self._guard:
                    paused = self._paused
                if not paused:
                    try:
                        self.pipeline.run_cycle()
                    except (BlockingModalError, NoOpJobTransitionError):
                        raise
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
                        job.state
                        in {
                            JobState.COMPLETED,
                            JobState.FAILED,
                            JobState.DOWNLOAD_VERIFY_FAILED,
                        }
                        for job in values
                    ):
                        break
                self._stop_event.wait(self.cycle_interval_seconds)
        except (BlockingModalError, NoOpJobTransitionError) as exc:
            self.cancellation.request()
            self._error_callback('modal' if isinstance(exc, BlockingModalError) else 'pipeline', str(exc))
        except RunCancelled:
            self._log_callback({'level':'INFO', 'stage':'stopped', 'message':json.dumps(self.cancellation.diagnostic())})
        finally:
            # No terminal job can require another Notebook poll. Keep scheduler
            # state aligned with the stopped worker after natural completion too.
            self.scheduler.stop()
            if cleanup_browser:
                try:
                    with cancellation_scope(None):
                        self.cleanup()
                except Exception as exc:
                    self._log_callback(
                        {"level": "WARNING", "stage": "shutdown", "message": str(exc)}
                    )
            if self.pipeline_factory is not None:
                self.pipeline = None
            with self._guard:
                self._retiring_worker = self._worker
                self._worker = None
                self._phase = "STOPPED" if self._stop_event.is_set() else "idle"
            self._publish_status()

    def status(self) -> dict[str, object]:
        values = self.jobs.list()
        with self._guard:
            worker_running = self._worker is not None and self._worker.is_alive()
            recovering = self._recovering
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
            "running": (worker_running or recovering) and not paused,
            "paused": paused,
            "scheduler_mode": self.scheduler.mode.value,
            "next_check": "－" if remaining is None else f"{max(0, int(remaining))}秒",
            "phase": self._phase,
            "runtime": dict(self._runtime),
            **self._credit_status(values),
        }

    @staticmethod
    def _credit_status(values: list[Job]) -> dict[str, object]:
        observed = [
            job
            for job in values
            if job.credit_state != "CREDIT_UNKNOWN"
            or job.credit_percent is not None
            or job.credit_reset_at is not None
        ]
        latest = max(observed, key=lambda job: job.updated_at) if observed else None
        return {
            "credit_state": latest.credit_state if latest else "CREDIT_UNKNOWN",
            "credit_percent": latest.credit_percent if latest else None,
            "credit_reset_at": latest.credit_reset_at if latest else None,
        }

    def _publish_status(self, values: list[Job] | None = None) -> None:
        self._status_callback(self.status())
