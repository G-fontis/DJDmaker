"""Opt-in v127 EXE acceptance. Synthetic DOM/clock; no Google side effects."""
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


FIXTURE = '''<section class="source-panel"><div id="sources">
<div class="single-source-container single-source-error-container"><button class="source-stretched-button" aria-label="lesson.txt"></button>
<button class="source-item-more-button" onclick="openSourceMenu()">もっと見る</button><span aria-label="エラー情報">info</span></div>
</div><input type="file" onchange="upload(this)"></section>
<chat-panel><div id="history"></div><textarea class="query-box-input" placeholder="質問をするか、何かを作成してみましょう"></textarea>
<button aria-label="送信" onclick="send()">送信</button><span id="count">0 個のソース</span></chat-panel>
<studio-panel><button aria-label="動画解説" id="video" disabled>動画解説</button><artifact-library></artifact-library></studio-panel>
<script>
window.uploads=0;window.sends=0;window.retries=0;window.bulkDeletes=0;window.deleted=0;
function openSourceMenu(){
 const menu=document.createElement('div');menu.setAttribute('role','menu');
 menu.innerHTML='<button role="menuitem" id="deleteOne">ソースを削除</button><button role="menuitem" id="deleteAll">失敗したソースをすべて削除</button>';
 document.body.append(menu);
 menu.querySelector('#deleteOne').onclick=()=>{document.querySelector('#sources').innerHTML='';window.deleted++;menu.remove()};
 menu.querySelector('#deleteAll').onclick=()=>window.bulkDeletes++;
}
function upload(input){
 if(document.querySelector('.single-source-container'))throw Error('duplicate source');
 window.uploads++;window.uploadedName=input.files[0].name;
 document.querySelector('#sources').innerHTML='<div class="single-source-container"><button class="source-stretched-button" aria-label="lesson.txt">lesson.txt</button></div>';
 document.querySelector('#count').textContent='1 個のソース';document.querySelector('#video').disabled=false;
}
function send(){
 const input=document.querySelector('chat-panel textarea');window.sends++;
 const user=document.createElement('div');user.dataset.messageAuthorRole='user';user.textContent=input.value;
 document.querySelector('#history').append(user);input.value='';
 const reply=document.createElement('div');reply.dataset.messageAuthorRole='assistant';reply.textContent='説明動画の作成を開始しました。';
 document.querySelector('#history').append(reply);
 const card=document.createElement('artifact-library-item');card.textContent='動画を生成しています';document.querySelector('artifact-library').append(card);
}
function failedVideo(){
 document.querySelector('artifact-library').innerHTML='<artifact-library-item>動画解説を生成できませんでした。<button onclick="window.retries++">再試行</button></artifact-library-item>';
}
</script>'''


def run_recovery_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.notebook import NotebookDomAdapter, NotebookEngineAdapter, SourceState
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.core.models import Job, JobState, Preset, preset_body_sha256
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.cloud_limit import CloudLimitGate, LimitObservation
    from djd_maker.media.validator import VideoValidator, resolve_executable
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.packaging.preflight import application_root
    from djd_maker.packaging.portable_e2e import _make_fixture
    root, report = Path(root).resolve(), Path(report).resolve()
    root.mkdir(parents=True, exist_ok=False)
    data = dict(mode='SYNTHETIC_DOM_CLOCK_REAL_MEDIA',
                execution='FROZEN_EXE' if getattr(sys, 'frozen', False) else 'SOURCE',
                passed=False, events=[])
    browser = BrowserManager(root/'fixture-profile', headless=True)
    try:
        page = browser.start()
        page.set_content(FIXTURE)
        dom = NotebookDomAdapter(page)
        source = root/'lesson.txt'
        source.write_text('isolated portable acceptance source', encoding='utf-8')
        repo = JobRepository(root/'system/jobs')
        job = Job(str(source), notebook_id='fixture', notebook_url='https://notebook.google.com/notebook/fixture',
                  preset_body_snapshot='saved package snapshot', preset_body_sha256=preset_body_sha256('saved package snapshot'))
        repo.save(job)
        dom.source_recovery_job = job
        dom.source_recovery_persist = repo.save
        assert dom.classify_source(source.name) is SourceState.FAILED
        dom.ensure_source(source)
        assert dom.classify_source(source.name) is SourceState.READY
        assert page.evaluate('window.uploads') == 1
        assert page.evaluate('window.deleted') == 1
        assert page.evaluate('window.bulkDeletes') == 0
        data['source_recovery'] = 'PASS'
        page.evaluate("""()=>{const warning=document.createElement('div');warning.setAttribute('role','alert');
          warning.textContent=\"You're almost at your AI usage limit. Limit resets at 16:21.\";document.body.append(warning)}""")
        page.evaluate('failedVideo()')
        class FixtureEngine(NotebookEngineAdapter):
            def _open_job(self, job):
                # Synthetic content remains in this isolated fixture tab.
                assert job.notebook_id == 'fixture'
            def inspect_status(self, job):
                return self.dom.inspect_status().value
        engine = FixtureEngine(dom, persist_identity=repo.save)
        outcome = engine.retry_failed_generation(job)
        assert outcome.remote_status.value == 'GENERATING'
        assert dom.inspect_status().value == 'GENERATING'
        assert page.evaluate('window.sends') == 1
        assert page.evaluate('window.retries') == 0
        assert page.locator('[data-message-author-role="user"]').inner_text() == job.preset_body_snapshot
        data['failed_recovery'] = 'PASS'
        data['almost_continues_generation'] = True
        data['studio_retry_clicks'] = page.evaluate('window.retries')
        data['preset_sends'] = page.evaluate('window.sends')
        page.screenshot(path=str(root/'recovery.png'))

        tools = application_root()/'runtime/ffmpeg'
        ffmpeg = resolve_executable('ffmpeg', tools/'ffmpeg.exe' if (tools/'ffmpeg.exe').exists() else None)
        ffprobe = resolve_executable('ffprobe', tools/'ffprobe.exe' if (tools/'ffprobe.exe').exists() else None)
        raw = root/'fixture.mp4'
        _make_fixture(ffmpeg, raw, 'blue', 2)
        raw_a, raw_b = root/'a.mp4', root/'b.mp4'
        for owned_raw in (raw_a, raw_b):
            shutil.copy2(raw, owned_raw)
        local_repo = JobRepository(root/'priority/jobs')
        a = Job(str(root/'a.txt'), id='priority-a', state=JobState.HLS_ENCODING, raw_path=str(raw_a), edited_path=str(raw_a))
        b = Job(str(root/'b.txt'), id='priority-b', state=JobState.HLS_ENCODING, raw_path=str(raw_b), edited_path=str(raw_b))
        c = Job(str(root/'c.txt'), id='priority-c')
        for item in (a,b,c): local_repo.save(item)
        clock = [datetime.now().astimezone()]
        gate = CloudLimitGate(root/'priority/cloud-limit.json', clock=lambda:clock[0])
        gate.block(LimitObservation('fixture hard limit', clock[0]+timedelta(hours=1), True))
        calls = []
        class CloudFixture:
            def recheck_cloud_limit(self, url):
                calls.append('capability.refresh')
                return True
            def submit(self, item):
                calls.append('generate:'+item.id)
                return 'fixture', 'https://notebook.google.com/notebook/fixture'
            def inspect_status(self, item):
                return 'GENERATING'
        validator = VideoValidator(ffprobe)
        pipe = PipelineCoordinator(jobs=local_repo, notebook=CloudFixture(), raw_store=RawSafeStore(validator, root/'raw'),
            ending=EndingEngineAdapter(ffmpeg,ffprobe,validator=validator), hls=HlsAdapter(ffmpeg,ffprobe), validator=validator,
            paths=PipelinePaths(root/'raw',root/'output',root/'work',None), cloud_limit=gate, ffmpeg_concurrency=1,
            generation_preset=Preset('fixture','fixture','fixture','created','updated'))
        def record(event):
            data['events'].append(event.get('stage'))
            if event.get('stage') == 'hls.complete':
                clock[0] += timedelta(hours=2)
        pipe.runtime_callback = record
        pipe.run_cycle()
        assert pipe.capabilities.can_generate
        assert local_repo.get(a.id).hls_checkpoint_directory
        assert not (root/'output/a.zip').exists()
        assert not local_repo.get(b.id).hls_checkpoint_directory
        pipe.run_cycle()
        assert calls[:2] == ['capability.refresh','generate:'+c.id]
        assert local_repo.get(a.id).state is JobState.COMPLETED
        assert local_repo.get(b.id).state is JobState.COMPLETED
        data['quota_recovery_priority'] = 'PASS'
        data['hls_zip_resume'] = 'PASS'
        data['ready_failed_retention'] = _ready_failed_retention_smoke(
            root/'ready-failed-retention', raw, ffmpeg, ffprobe)
        data['passed'] = True
    except Exception as exc:
        import traceback
        data['error'] = type(exc).__name__+': '+str(exc)
        data['traceback'] = traceback.format_exc()
    finally:
        browser.stop()
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if data['passed'] else 9


def _ready_failed_retention_smoke(root, media_fixture, ffmpeg, ffprobe):
    """Synthetic remote READY; real download copy, RAW validation and HLS/ZIP.

    This does not contact Google and must never be reported as live acceptance.
    All runtime state and generated media stay below the opt-in smoke root.
    """
    from dataclasses import asdict
    from hashlib import sha256
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.core.models import Job, JobState
    from djd_maker.core.repositories import JobRepository
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter

    root.mkdir(parents=True, exist_ok=False)
    source = root/'missing-input'/'ready lesson.txt'
    identity = '00000000-0000-4000-8000-000000000128'
    job = Job(str(source), id='ready-failed-retention', state=JobState.FAILED,
              error_code='NOTEBOOK_STAGE_FAILED', failure_class='FATAL_FAILED',
              notebook_id=identity, notebook_url='https://notebook.google.com/notebook/'+identity,
              source_sha256=sha256(b'isolated missing source fixture').hexdigest())
    repository = JobRepository(root/'system/jobs')
    repository.save(job)
    calls = []
    class RetentionFixture(FakeNotebookAdapter):
        def submit(self, item):
            calls.append('submit')
            raise AssertionError('READY must not submit a generation request')
        def upload_txt(self, source):
            calls.append('upload')
            raise AssertionError('READY must not upload the missing TXT')
        def inspect_status(self, item):
            calls.append('inspect')
            return super().inspect_status(item)
        def download_artifact(self, item, destination):
            calls.append('download')
            return super().download_artifact(item, destination)
        def delete_video_artifact(self, item, gate):
            calls.append('delete')
            raise AssertionError('Normal recovery must retain remote artifacts')
    notebook = RetentionFixture({str(source): media_fixture}, {job.id: 'READY'})
    validator = VideoValidator(ffprobe)
    pipeline = PipelineCoordinator(jobs=repository, notebook=notebook,
        raw_store=RawSafeStore(validator, root/'raw'),
        ending=EndingEngineAdapter(ffmpeg, ffprobe, validator=validator),
        hls=HlsAdapter(ffmpeg, ffprobe), validator=validator,
        paths=PipelinePaths(root/'raw', root/'output', root/'work', None),
        ffmpeg_concurrency=1)
    stages = []
    observations = {}
    def record(event):
        stage = event.get('stage')
        stages.append(stage)
        if stage == 'raw.saved':
            observations['after_download'] = notebook.inspect_status(repository.require(job.id))
    pipeline.runtime_callback = record
    pipeline.run_cycle()
    result = repository.require(job.id)
    assert result.state is JobState.COMPLETED
    assert not source.exists()
    assert result.raw_path
    validator.validate(Path(result.raw_path))
    assert result.zip_path and Path(result.zip_path).is_file()
    from djd_maker.core.completed_reconciliation import validated_output
    assert validated_output(result, root/'output')
    assert len(asdict(result.safety_gate)) == 12
    assert not result.safety_gate.failed_checks
    assert result.artifact_status == 'RETAINED'
    assert observations.get('after_download') == 'READY'
    observations['after_completed'] = notebook.inspect_status(result)
    assert observations['after_completed'] == 'READY'
    assert 'ending.skip' in stages
    assert notebook.download_calls == [job.id]
    assert not any(action in calls for action in ('submit', 'upload', 'delete'))
    before_repeat = list(calls)
    pipeline.begin_run()
    pipeline.run_cycle()
    assert calls == before_repeat
    assert repository.require(job.id).state is JobState.COMPLETED
    return dict(passed=True, mode='SYNTHETIC_REMOTE_READY_REAL_MEDIA', live=False,
        source_missing=True, raw_12_gate='PASS', ending='SKIPPED', hls_zip='PASS',
        completed=True, artifact_status=result.artifact_status,
        remote_observations=observations, submit_calls=0, upload_calls=0,
        delete_calls=0, download_calls=len(notebook.download_calls),
        repeat_run_new_actions=0, stages=stages)
