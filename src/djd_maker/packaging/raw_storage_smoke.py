"""Opt-in isolated storage acceptance using real FFmpeg and product Pipeline."""
import json
import os
import time
import traceback
from pathlib import Path


def run_raw_storage_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from dataclasses import replace
    from .preflight import application_root
    from .portable_e2e import _make_fixture
    from .acceptance_assets import initialize_fixture_assets
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.core.models import Job, JobState
    from djd_maker.core.repositories import JobRepository, SettingsRepository
    from djd_maker.core.download_quality import load_quality_profile
    from djd_maker.core.artifact_ownership import _digest
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    root, report = Path(root).resolve(), Path(report).resolve()
    root.mkdir(parents=True, exist_ok=False)
    result = dict(passed=False, mode='REAL_MEDIA_PRODUCT_PIPELINE', runs=[])
    try:
        initialize_fixture_assets(root)
        tools = application_root()/'runtime/ffmpeg'
        ffmpeg, ffprobe = tools/'ffmpeg.exe', tools/'ffprobe.exe'
        if not ffmpeg.is_file():
            from shutil import which
            ffmpeg, ffprobe = Path(which('ffmpeg')), Path(which('ffprobe'))
        validator = VideoValidator(ffprobe)
        fixture, ending = root/'remote-fixture.mp4', root/'ending.mp4'
        _make_fixture(ffmpeg, fixture, 'blue', 3)
        _make_fixture(ffmpeg, ending, 'red', 1)
        # A valid >=10MiB fixture avoids mandatory small-video retries here;
        # their six-attempt/statistical boundary has separate regressions.
        with fixture.open('ab') as stream:
            stream.truncate(11*1024**2)
        settings = SettingsRepository(root/'system/settings.json')
        settings.save(replace(settings.load(), raw_directory=str(root/'user-raw')))
        raw_root = Path(SettingsRepository(root/'system/settings.json').load().raw_directory)
        for enabled in (False, True):
            folder = root/str(enabled)
            folder.mkdir()
            source = folder/'storage.txt'
            source.write_text('isolated source', encoding='utf8')
            job = Job(str(source), state=JobState.DOWNLOADING, source_sha256=_digest(source),
                      notebook_id='00000000-0000-4000-8000-000000000126',
                      notebook_url='https://notebook.google.com/notebook/00000000-0000-4000-8000-000000000126',
                      hls_zip_enabled=enabled, ending_enabled=True, tail_cut_enabled=True, encode_enabled=True)
            repo = JobRepository(folder/'jobs')
            repo.save(job)
            remote = FakeNotebookAdapter({str(source):fixture})
            remote.status_by_job[job.id]='READY'
            raw = raw_root/str(enabled)
            processor = EndingEngineAdapter(ffmpeg, ffprobe, validator=validator)
            inputs = []
            original = processor.process_options
            def process(path, *args, **kwargs):
                inputs.append(str(path))
                return original(path, *args, **kwargs)
            processor.process_options = process
            original_process = processor.process
            def process_legacy(path, *args, **kwargs):
                inputs.append(str(path))
                return original_process(path, *args, **kwargs)
            processor.process = process_legacy
            pipe = PipelineCoordinator(jobs=repo, notebook=remote, raw_store=RawSafeStore(validator),
                ending=processor, hls=HlsAdapter(ffmpeg, ffprobe), validator=validator,
                paths=PipelinePaths(raw, folder/'output', folder/'pipeline-work', ending),
                ending_enabled=True, tail_cut_enabled=True, encode_enabled=True, hls_zip_enabled=enabled,
                download_quality_profile=load_quality_profile(root/'config/download-quality-profile.json'),
                quality_delay_baseline_seconds=0)
            def finish():
                deadline = time.monotonic()+90
                while True:
                    current = repo.require(job.id)
                    if current.state is JobState.COMPLETED:
                        return current
                    assert time.monotonic()<deadline, current.error_message
                    if current.state in {JobState.DOWNLOADING, JobState.DOWNLOAD_PENDING}:
                        pipe._run_notebook_job(current)
                    elif current.state in pipe.MEDIA_STATES:
                        pipe._run_media_job(current)
                    else:
                        raise AssertionError((current.state.value,current.error_message))
                    time.sleep(.02)
            saved = finish()
            canonical = raw/'storage.mp4'
            assert Path(saved.raw_path)==canonical
            assert list(raw.rglob('*.mp4'))==[canonical]
            assert inputs and set(inputs)=={str(canonical)}
            digest = _digest(canonical)
            assert not [p for p in pipe.paths.work_directory.rglob('*.mp4') if _digest(p)==digest]
            # Resume the same completed RAW; no work download is recreated.
            before = len(remote.download_calls)
            saved.state=JobState.DOWNLOADING
            repo.save(saved)
            pipe._run_notebook_job(repo.require(saved.id))
            assert len(remote.download_calls)==before
            # Explicit manual retry uses the identical RAW-volume staging path.
            saved=repo.require(saved.id)
            saved.state=JobState.COMPLETED
            repo.save(saved)
            pipe._prepare_manual_save_retry(saved,(True,True,True,enabled))
            saved=finish()
            assert saved.state is JobState.COMPLETED
            assert list(raw.rglob('*.mp4'))==[canonical]
            assert not [p for p in pipe.paths.work_directory.rglob('*.mp4') if _digest(p)==digest]
            assert not remote.artifact_delete_calls
            result['runs'].append(dict(hls_zip=enabled, passed=True, raw_count=1, work_raw_count=0,
                manual_retry=True, canonical=str(canonical), local_inputs=inputs, chat_sends=0))
        result['passed']=True
    except Exception:
        result['error']=traceback.format_exc()
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
