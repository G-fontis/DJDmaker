from __future__ import annotations

import threading
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobStateSaveError
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
        self._stop_reason = None
        self._stop_request_reason = None
        self._last_task = None
        self._next_scan_at = None
        self._job_callback = lambda _job: None
        self._job_updates_bound = False
        self._job_snapshots = {job.id:job for job in self.jobs.list()}

    def bind_job(self, callback) -> None:
        self._job_callback = callback
        self._job_updates_bound = True

    def _job_update(self, job: Job) -> None:
        with self._guard:
            self._job_snapshots[job.id] = job
        self._job_callback(job)

    def _runtime_update(self, record: dict) -> None:
        with self._guard:
            if record.get('scheduler'):
                view=record['scheduler']
                self._next_scan_at=view.get('next_scan_at')
                if view.get('priority') != 5:
                    self._last_task=view.get('task')
            previous = self._runtime
            self._runtime = dict(record)
            snapshots = list(self._job_snapshots.values())
        if any(previous.get(k) != record.get(k) for k in ('job_id', 'stage', 'decision', 'attempt')):
            self._status_callback({**self.status(snapshots), 'runtime': dict(record)})
            self._log_callback({'level': record.get('level', 'INFO'), 'stage': record.get('stage', ''),
                                'message': record.get('message', record.get('stage', '')),
                                'runtime': dict(record)})

    def _set_stop_reason(self, code, detail=''):
        from djd_maker.core.stop_reason import StopReason
        self._stop_reason = StopReason.make(code,detail)
        self._runtime_update({**self._runtime, 'stage':'lifecycle.'+code,
            'message':self._stop_reason.message, 'stop_reason':self._stop_reason.to_dict()})

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
        from djd_maker.core.output_ownership import import_lock
        with import_lock(self.jobs,self.app_root/'system/jobs'):
            return self._reload_serialized()

    def _reload_serialized(self) -> list[Job]:
        from djd_maker.core.deferred_state import store_for
        from djd_maker.core.output_ownership import path_identity, source_digest, reconcile_output_ownership
        source_root = self._input_directory()
        source_root.mkdir(parents=True, exist_ok=True)
        existing = {path_identity(job.source_path) for job in self.jobs.list()}
        deferred = store_for(self.jobs)
        existing.update(path_identity(entry.snapshot['source_path'])
                        for entry in deferred.entries.values() if entry.snapshot.get('source_path'))
        deleted = getattr(self.jobs, "deleted_source_paths", None)
        if callable(deleted):
            existing.update(path_identity(p) for p in deleted())
        for source in sorted(source_root.glob("*.txt"), key=lambda item: item.name.casefold()):
            resolved = path_identity(source)
            if resolved not in existing and source.is_file():
                try:
                    digest = source_digest(source)
                except OSError as exc:
                    digest = None
                    self._log_callback({'level':'WARNING','stage':'source.identity',
                        'message':f'台本 {source.name} のハッシュを読めません。対象jobで再確認します。他の台本を継続します。{exc}'})
                job = Job(str(source.resolve()), source_sha256=digest)
                try:
                    self.jobs.save(job)
                except JobStateSaveError as error:
                    deferred.record(job, type(error).__name__)
                    self._runtime_update(dict(stage='save.deferred', job_id=job.id, job=job.script_name,
                        level='WARNING', message='新規jobの状態保存を保留しました。他の台本を続行します。'))
                existing.add(resolved)
        output = Path(self.settings.output_directory)
        output = output if output.is_absolute() else self.app_root/output
        self.ownership_report = reconcile_output_ownership(self.jobs, output)
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
                self.cancellation.resume()
                self.scheduler.resume()
                self._runtime_update({**self._runtime, 'stage': 'resume'})
                return self.status()
            # A completed run owns a pipeline assembled with that run's preset
            # snapshot and browser page. Recompose on every new Start so a GUI
            # selection/edit made between runs cannot reuse stale preset data.
            if self.pipeline_factory is not None:
                self.pipeline = None
            self._paused = False
            self.cancellation.reset()
            self._stop_reason = None
            self._stop_request_reason = None
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
            self._stop_reason = None
            self._stop_request_reason = None
            self._recovery_done.clear()
            self._phase = "recovery"
        try:
            pipeline = factory()
            pipeline.runtime_callback = self._runtime_update
            pipeline.job_callback = self._job_update
            pending_before = [
                job
                for job in self.jobs.list()
                if job.state in PipelineCoordinator.RECOVERY_STATES
            ]
            processed = pipeline.run_recovery_cycle()
            summary = getattr(pipeline, 'deferred_summary', None)
            if callable(summary):
                summary()
            values = self.jobs.list()
            self._jobs_callback(values)
            self._publish_status(values)
            self._set_stop_reason('RECOVERY_CHECK_FINISHED')
            return {
                "pending": len(pending_before),
                "checked": len(processed),
            }
        except RunCancelled:
            self._set_stop_reason(self._stop_request_reason or 'USER_STOP')
            return {'pending': 0, 'checked': 0, 'stopped': True}
        except Exception as exc:
            from djd_maker.core.stop_reason import exception_reason
            self._set_stop_reason(exception_reason(exc),str(exc))
            raise
        finally:
            try:
                with cancellation_scope(None):
                    self.cleanup()
            finally:
                with self._guard:
                    self._recovering = False
                    self._phase = "idle"
                    self._recovery_done.set()
                if self._stop_reason is None:
                    self._set_stop_reason(self._stop_request_reason or 'TERMINAL_GLOBAL_ERROR')
                self._publish_status()

    def refresh_credit(self) -> dict[str, object]:
        """Publish persisted/unknown credit state without polling the DOM."""
        result = self.status()
        self._status_callback(result)
        return result

    def pause(self) -> dict[str, object]:
        self.request_pause()
        with self._guard:
            self._paused = True
            self.scheduler.pause()
        self._runtime_update({**self._runtime, 'stage': 'pause'})
        self._runtime_update({**self._runtime, 'stage':'pause',
            'message':'一時停止しています。開始操作で同じ位置から再開します。'})
        self._publish_status()
        return self.status()

    def request_pause(self) -> None:
        self.cancellation.request_pause()

    def resume(self) -> dict[str, object]:
        return self.start()

    def request_stop(self) -> None:
        if self._stop_request_reason is None:
            self._stop_request_reason = 'USER_STOP'
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
            self._set_stop_reason(self._stop_request_reason or 'USER_STOP')
        return self.status()

    def shutdown(self) -> None:
        self._stop_request_reason = 'APP_CLOSE'
        result = self.stop()
        if result["running"]:
            self._log_callback({'level':'ERROR', 'stage':'shutdown-timeout', 'message':json.dumps(self.cancellation.diagnostic())})
            raise RuntimeError("pipeline worker did not stop safely")

    def retry(self, job_id: str, stage: str) -> Job:
        if self.pipeline is None:
            raise RuntimeError("pipeline must be started before retry")
        if job_id in getattr(self.pipeline, 'deferred_ids', set()):
            # A deferred job cannot enter a manual resend path. Start performs
            # the bounded remote/local reconciliation, not an arbitrary retry.
            return self.jobs.get(job_id)
        from djd_maker.core.deferred_state import SaveDeferred
        try:
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
        except SaveDeferred:
            result = self.jobs.get(job_id)
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
                    from djd_maker.core.stop_reason import exception_reason
                    self._set_stop_reason(exception_reason(exc),str(exc))
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
            self.pipeline.job_callback = self._job_update
            begin = getattr(self.pipeline, 'begin_run', None)
            if callable(begin):
                begin()
            self.scheduler.start()
            self._phase = "processing"
            while not self._stop_event.is_set():
                checkpoint('pipeline.dequeue')
                with self._guard:
                    paused = self._paused
                if not paused:
                    try:
                        self.pipeline.run_cycle()
                    except (BlockingModalError, NoOpJobTransitionError, JobStateSaveError):
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
                        raise
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
                    # Persisted per-job snapshots are emitted during the cycle.
                    if not self._job_updates_bound:
                        self._jobs_callback(values)
                    self._publish_status(values)
                    from .task_discovery import final_discovery
                    from datetime import datetime, UTC
                    discover = getattr(self.pipeline,'final_discovery',None)
                    scan = discover() if callable(discover) else final_discovery(
                        self.jobs.list(),datetime.now(UTC),False,getattr(self.pipeline,'deferred_ids',set()))
                    self.final_discovery_result = scan
                    if scan['complete']:
                        self._set_stop_reason('ALL_TASKS_COMPLETED')
                        break
                    if not scan['runnable']:
                        self._runtime_update({**self._runtime,'stage':'scheduler.wait',
                            'message':'現在実行可能なjobがないため、次回確認まで待機しています。',
                            'wait_reason':'NO_RUNNABLE_TASK_WAIT'})
                interval = getattr(self.pipeline, 'wait_seconds', 0) or self.cycle_interval_seconds
                self.cancellation.wait(interval)
        except (BlockingModalError, NoOpJobTransitionError, JobStateSaveError) as exc:
            from djd_maker.core.stop_reason import exception_reason
            self._set_stop_reason(exception_reason(exc),str(exc))
            self.cancellation.request()
            self._error_callback('modal' if isinstance(exc, BlockingModalError) else 'pipeline', str(exc))
        except RunCancelled:
            self._set_stop_reason(self._stop_request_reason or 'USER_STOP')
            self._log_callback({'level':'INFO', 'stage':'stopped', 'message':json.dumps(self.cancellation.diagnostic())})
        except Exception as exc:
            from djd_maker.core.stop_reason import exception_reason
            self._set_stop_reason(exception_reason(exc),str(exc))
            self._error_callback('pipeline',str(exc))
        finally:
            if self._stop_reason is None:
                self._set_stop_reason(self._stop_request_reason or 'TERMINAL_GLOBAL_ERROR')
            summary = getattr(self.pipeline, 'deferred_summary', None)
            if callable(summary):
                try:
                    summary()
                except Exception as exc:
                    self._set_stop_reason('STORAGE_FATAL',str(exc))
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

    def status(self, values: list[Job] | None = None) -> dict[str, object]:
        values = self.jobs.list() if values is None else values
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
            "active": worker_running or recovering,
            "paused": paused,
            "pause_state": ('PAUSED' if self.cancellation.paused.is_set() else 'PAUSE_REQUESTED') if paused else None,
            "cloud_limit": self.pipeline.cloud_limit.status() if hasattr(self.pipeline, 'cloud_limit') else self.cloud_limit.status() if hasattr(self, 'cloud_limit') else {},
            "scheduler_mode": self.scheduler.mode.value,
            "scheduler": dict(getattr(self.pipeline, 'scheduler_view', {})),
            "next_check": "－" if remaining is None else f"{max(0, int(remaining))}秒",
            "phase": self._phase,
            "runtime": dict(self._runtime),
            "stop_reason": self._stop_reason.to_dict() if self._stop_reason else None,
            "last_task": self._last_task,
            "next_scan_at": self._next_scan_at,
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
        if values is not None:
            with self._guard:
                self._job_snapshots = {job.id:job for job in values}
        self._status_callback(self.status(values))
