from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QApplication

from djd_maker.adapters.browser import BrowserManager
from djd_maker.adapters.ending import EndingEngineAdapter
from djd_maker.adapters.hls import HlsAdapter
from djd_maker.adapters.notebook import (
    NotebookDomAdapter,
    NotebookEngineAdapter,
    PlaywrightArtifactDownload,
)
from djd_maker.core.repositories import JobRepository, PresetRepository, SettingsRepository
from djd_maker.media.raw_store import RawSafeStore
from djd_maker.media.validator import VideoValidator
from djd_maker.orchestration.gui_controller import GuiPipelineController
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.orchestration.scheduler import PersistentPollScheduler
from djd_maker.packaging.preflight import application_root

from .controller import AsyncControllerBridge
from .main_window import MainWindow


def _resolved(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def build_desktop(
    app_root: Path,
    *,
    qt_app: QApplication | None = None,
    browser_manager: BrowserManager | None = None,
) -> tuple[QApplication, MainWindow, GuiPipelineController]:
    """Compose the real JSON, browser, media, scheduler, pipeline, and GUI layers."""

    root = app_root.resolve()
    application = qt_app or QApplication.instance() or QApplication(sys.argv)
    settings_repository = SettingsRepository(root / "system" / "settings.json")
    preset_repository = PresetRepository(root / "system" / "presets.json")
    job_repository = JobRepository(root / "system" / "jobs")
    from djd_maker.core.job_migration import migrate_jobs
    from djd_maker.core.completed_txt import reconcile_completed_txt
    migrate_jobs(root / "system")
    settings = settings_repository.load()
    from djd_maker.core.cloud_limit import CloudLimitGate
    cloud_limit = CloudLimitGate(root / 'system' / 'cloud-limit.json')
    from djd_maker.core.output_ownership import reconcile_output_ownership
    reconcile_output_ownership(job_repository, _resolved(root, settings.output_directory))
    reconcile_completed_txt(job_repository, _resolved(root, settings.raw_directory))
    browser = browser_manager or BrowserManager(
        root / "browser" / "chrome-profile",
        selector_probe=NotebookDomAdapter.preflight_home_page,
    )
    scheduler = PersistentPollScheduler(
        job_repository,
        first_poll_seconds=settings.first_notebook_check_seconds,
        subsequent_poll_seconds=settings.notebook_poll_seconds,
    )

    def make_pipeline(*, require_preset: bool) -> PipelineCoordinator:
        from djd_maker.core.cloud_limit import CloudLimitGate
        from djd_maker.adapters.notebook_modal import ensure_notebook_interactable
        current = settings_repository.load()
        current.validate()
        selected_preset = (
            preset_repository.require_selected() if require_preset else None
        )
        ending_path = _resolved(root, current.ending_video) if current.ending_video else None
        if ending_path is not None and not ending_path.is_file():
            raise FileNotFoundError("選択したEnding動画が存在しません。再選択または設定を空にしてください")
        scheduler.first_poll_seconds = current.first_notebook_check_seconds
        scheduler.subsequent_poll_seconds = current.notebook_poll_seconds
        validator = VideoValidator()
        gate = cloud_limit
        class DeferredPage:
            def __init__(self):
                self.page = None
            def __getattr__(self, name):
                if self.page is None:
                    self.page = browser.prepare_for_processing()
                return getattr(self.page, name)
        page = DeferredPage() if gate.blocked else browser.prepare_for_processing()
        notebook = NotebookEngineAdapter(
            NotebookDomAdapter(
                page,
                download_handoff=PlaywrightArtifactDownload(validator),
                interactable_guard=ensure_notebook_interactable,
            ),
            recover_page=browser.restart,
            persist_identity=job_repository.save,
        )
        raw_directory = _resolved(root, current.raw_directory)
        return PipelineCoordinator(
            jobs=job_repository,
            notebook=notebook,
            raw_store=RawSafeStore(validator, raw_directory),
            ending=EndingEngineAdapter(validator=validator),
            hls=HlsAdapter(),
            validator=validator,
            paths=PipelinePaths(
                raw_directory=raw_directory,
                output_directory=_resolved(root, current.output_directory),
                work_directory=(root / "work").resolve(),
                ending_video=ending_path,
            ),
            ffmpeg_concurrency=current.ffmpeg_concurrency,
            scheduler=scheduler,
            generation_preset=selected_preset,
            cloud_limit=gate,
        )

    def pipeline_factory() -> PipelineCoordinator:
        return make_pipeline(require_preset=True)

    def recovery_pipeline_factory() -> PipelineCoordinator:
        return make_pipeline(require_preset=False)

    service = GuiPipelineController(
        jobs=job_repository,
        settings=settings,
        settings_provider=settings_repository.load,
        app_root=root,
        pipeline=None,
        pipeline_factory=pipeline_factory,
        recovery_pipeline_factory=recovery_pipeline_factory,
        cleanup=browser.stop,
        abort_owned_external=getattr(browser, 'abort_owned_automation', None),
        shutdown_diagnostic=getattr(browser, 'shutdown_diagnostic', None),
        scheduler=scheduler,
        manual_login=browser.open_login,
        browser_status_provider=browser.runtime_status,
    )
    bridge = AsyncControllerBridge(service)
    service.cloud_limit = cloud_limit
    window = MainWindow(
        app_root=root,
        settings_repository=settings_repository,
        job_repository=job_repository,
        controller=bridge,
        preset_repository=preset_repository,
    )
    return application, window, service


def main() -> int:
    application, window, _service = build_desktop(application_root())
    window.show()
    return application.exec()
