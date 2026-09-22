"""Opt-in packaged regression: shared setting and real media ON/OFF matrix."""
import json
import os
from pathlib import Path
import traceback


def run_hls_optional_smoke(root: Path, report: Path) -> int:
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from PySide6.QtWidgets import QApplication
    from djd_maker.gui.dialogs import SettingsDialog
    from djd_maker.core.settings import AppSettings
    from djd_maker.core.models import Job, JobState, Preset
    from djd_maker.core.repositories import JobRepository, SettingsRepository
    from djd_maker.core.artifact_ownership import _digest
    from djd_maker.core.final_mp4 import owned_mp4
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    from .portable_e2e import _make_fixture
    from .preflight import application_root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=False, runs=[], settings=[])
    try:
        app = QApplication.instance() or QApplication([])
        settings_repo = SettingsRepository(root/'system/settings.json')
        for enabled in (True, False):
            for phase in ('PHASE1', 'PHASE2'):
                settings_repo.save(AppSettings(gui_type=phase, hls_zip_enabled=enabled))
                restored = SettingsRepository(root/'system/settings.json').load()
                dialog = SettingsDialog(restored, processing=True)
                dialog.show()
                app.processEvents()
                assert dialog.hls_zip_checkbox.isChecked() == enabled
                assert not dialog.hls_zip_checkbox.isEnabled()
                assert dialog.value().gui_type == phase
                dialog.close()
                result['settings'].append(dict(enabled=enabled, phase=phase, passed=True))
        runtime = application_root()/'runtime/ffmpeg'
        ffmpeg = runtime/'ffmpeg.exe'
        ffprobe = runtime/'ffprobe.exe'
        # Source execution uses the exact official packaging assets.
        if not ffmpeg.is_file():
            ffmpeg = Path('build/packaging-assets/ffmpeg.exe').resolve()
            ffprobe = ffmpeg.with_name('ffprobe.exe')
        fixture = root/'fixture.mp4'
        ending = root/'ending.mp4'
        _make_fixture(ffmpeg, fixture, 'blue', 3)
        _make_fixture(ffmpeg, ending, 'red', 1)
        validator = VideoValidator(ffprobe)
        for enabled in (True, False):
            for with_ending in (False, True):
                folder = root/f'{enabled}-{with_ending}'
                folder.mkdir()
                source = folder/'日本語 台本.txt'
                source.write_text('optional HLS test', encoding='utf-8')
                job = Job(str(source))
                jobs = JobRepository(folder/'system/jobs')
                jobs.save(job)
                notebook = FakeNotebookAdapter({job.source_path:fixture})
                run = dict(enabled=enabled, ending=with_ending, hls_calls=0, zip_events=0)
                result['runs'].append(run)
                class CountHls(HlsAdapter):
                    def convert_validate_and_zip(self, *args, **kwargs):
                        run['hls_calls'] += 1
                        assert enabled, 'OFF converter called'
                        return super().convert_validate_and_zip(*args, **kwargs)
                    def resume_validated(self, *args, **kwargs):
                        assert enabled, 'OFF ZIP called'
                        return super().resume_validated(*args, **kwargs)
                pipe = PipelineCoordinator(jobs=jobs, notebook=notebook, raw_store=RawSafeStore(validator),
                    ending=EndingEngineAdapter(ffmpeg, ffprobe, validator=validator),
                    hls=CountHls(ffmpeg, ffprobe), validator=validator,
                    paths=PipelinePaths(folder/'raw',folder/'output',folder/'work',ending if with_ending else None),
                    generation_preset=Preset('smoke','smoke','test body','created','updated'), hls_zip_enabled=enabled)
                def update(event):
                    if event.get('stage') == 'zip.start': run['zip_events'] += 1
                pipe.runtime_callback = update
                for _ in range(12):
                    pipe.run_cycle()
                    saved = jobs.require(job.id)
                    if saved.state in {JobState.COMPLETED, JobState.FAILED}: break
                assert saved.state is JobState.COMPLETED, saved.error_message
                assert saved.safety_gate.remote_deletion_allowed
                assert saved.txt_move_status == 'MOVED'
                assert not notebook.artifact_delete_calls
                assert _digest(saved.raw_path) == _digest(fixture)
                if enabled:
                    assert Path(saved.zip_path).is_file()
                    assert run['hls_calls'] == 1
                else:
                    assert run['hls_calls'] == run['zip_events'] == 0
                    assert owned_mp4(saved, Path(saved.final_mp4_path), jobs)
                    assert not list(folder.rglob('*.zip'))
                    assert not list(folder.rglob('*.m3u8'))
                run.update(passed=True, state=saved.state.value, raw=saved.raw_path,
                           final_mp4=saved.final_mp4_path, zip=saved.zip_path, txt_move=saved.txt_move_status)
        result['passed'] = True
    except Exception:
        result['traceback'] = traceback.format_exc()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1
