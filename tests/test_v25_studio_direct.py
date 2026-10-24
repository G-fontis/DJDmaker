"""Studio primary flow: real Chromium, observed DOM, no Google side effects."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_v12_chat_dom import browser
from djd_maker.adapters.notebook import NotebookDomAdapter,NotebookEngineAdapter,RemoteVideoStatus
from djd_maker.adapters.studio_generation import StudioGeneration,ConfigurationUIFailed,StudioOutcomePending
from djd_maker.core.models import Job,Preset,JobState
from djd_maker.core.repositories import JobRepository,PresetRepository
from djd_maker.core.generation_settings import GenerationSettings,STYLE_LABELS,map_legacy_preset,UnsupportedGenerationPreset
from djd_maker.testing.studio_fixture import install_studio_fixture,BODY,settings_html

IDENTITY='00000000-0000-4000-8000-000000000125'
URL='https://notebook.google.com/notebook/'+IDENTITY
HTML=settings_html()

@pytest.fixture
def studio(browser,tmp_path):
    context=browser.new_context()
    context.route('https://notebook.google.com/**',lambda route:route.fulfill(body='<main></main>'))
    page=context.new_page();page.goto(URL)
    install_studio_fixture(page,HTML)
    source=tmp_path/'lesson.txt';source.write_text('fixture',encoding='utf8')
    job=Job(str(source),notebook_id=IDENTITY,notebook_url=URL)
    job.snapshot_preset(Preset('p','paper',BODY,'created','updated'))
    repo=JobRepository(tmp_path/'jobs');repo.save(job)
    dom=NotebookDomAdapter(page);dom.studio_settings_owned=True
    flow=StudioGeneration(dom,job,repo.save,timeout_seconds=.4,poll_seconds=.02)
    yield SimpleNamespace(page=page,job=job,repo=repo,dom=dom,flow=flow)
    context.close()

@pytest.mark.parametrize('opened',[False,True])
@pytest.mark.parametrize('state',['GENERATING','READY'])
def test_source_ready_enters_studio_and_verifies_generation_without_chat(studio,opened,state):
    s=studio
    if opened:s.page.evaluate('openStudioFixture()')
    s.page.evaluate('(state)=>window.finishMode=state',state)
    result=s.flow.run()
    assert result.remote_status.value==state
    assert s.page.evaluate('window.generateClicks')==1
    assert s.page.evaluate('window.badClicks')==0 and s.page.evaluate('window.chatSends')==0
    assert s.page.evaluate('window.studioOpens')==int(not opened)
    saved=s.repo.require(s.job.id)
    assert saved.studio_generation_stage=='GENERATION_STARTED'
    assert saved.studio_generate_claimed_at and saved.studio_generation_attempt==1
    assert saved.require_generation_settings()==GenerationSettings()

def test_delayed_settings_panel_is_polled(studio):
    s=studio;install_studio_fixture(s.page,HTML,delay_ms=80)
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING

@pytest.mark.parametrize('missing',['style','language','generate','type'])
def test_missing_configuration_prevents_any_generate(studio,missing):
    s=studio;s.page.evaluate('openStudioFixture()')
    selectors={'style':'mat-radio-group.carousel-group','language':'[role=combobox]',
               'generate':'mat-dialog-actions','type':'mat-radio-group.tile-group'}
    s.page.locator(selectors[missing]).evaluate('e=>e.remove()')
    if missing in {'language','generate','type'}:
        # This is no longer the positively recognized owned settings form.
        # Isolate configuration polling from the separately tested human wait.
        s.flow.guard=lambda:None
    with pytest.raises(ConfigurationUIFailed):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0 and not s.job.studio_generate_claimed_at

def test_selection_readback_mismatch_never_clicks_generate(studio):
    s=studio;s.page.evaluate('openStudioFixture();window.ignoreSelection=true')
    with pytest.raises(ConfigurationUIFailed):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0

@pytest.mark.parametrize('field',['language','producer'])
def test_language_and_optional_producer_note_are_corrected(studio,field):
    s=studio;s.page.evaluate('openStudioFixture()')
    if field=='language':s.page.get_by_role('combobox').evaluate("e=>e.textContent='English'")
    else:s.page.get_by_role('switch').evaluate("e=>e.setAttribute('aria-checked','true')")
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING

def test_source_not_ready_prevents_studio_generation(studio):
    s=studio;s.page.locator('.source-panel').evaluate("e=>e.insertAdjacentHTML('beforeend','<div role=progressbar>loading</div>')")
    with pytest.raises(ConfigurationUIFailed,match='SOURCE_NOT_READY'):s.flow.run()
    assert s.page.evaluate('window.studioOpens')==0 and s.page.evaluate('window.generateClicks')==0

def test_required_source_is_selected_before_settings(studio):
    s=studio;s.page.get_by_role('checkbox').uncheck()
    s.flow.run();assert s.page.get_by_role('checkbox').is_checked()

def test_durable_claim_is_persisted_before_click(studio):
    s=studio;events=[]
    def save(job):
        if job.studio_generation_stage=='GENERATE_CLICKED':
            assert s.page.evaluate('window.generateClicks')==0
            events.append(job.studio_generate_claimed_at)
        s.repo.save(job)
    s.flow.persist=save;s.flow.run();assert len(events)==1

def test_restart_after_claim_does_not_generate_again(studio):
    s=studio;s.job.studio_generate_claimed_at='durable prior intent';s.repo.save(s.job)
    with pytest.raises(StudioOutcomePending):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0

def test_unresponsive_generate_never_reclicked(studio):
    s=studio;s.page.evaluate('window.suppressGeneration=true')
    with pytest.raises(StudioOutcomePending):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==1
    with pytest.raises(StudioOutcomePending):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==1

@pytest.mark.parametrize('state',['GENERATING','READY'])
def test_restart_remote_progress_skips_settings_and_generate(studio,state):
    s=studio;s.dom.inspect_status=lambda:RemoteVideoStatus(state)
    assert s.flow.run().remote_status.value==state
    assert s.page.evaluate('window.studioOpens')==0 and s.page.evaluate('window.generateClicks')==0

@pytest.mark.parametrize('style',list(STYLE_LABELS))
def test_existing_four_presets_map_losslessly_with_snapshot(style,tmp_path):
    body=BODY.rsplit('・',1)[0]+'・'+STYLE_LABELS[style]
    repository=PresetRepository(tmp_path/'presets.json')
    original=repository.create(style,body);before=(tmp_path/'presets.json').read_bytes()
    loaded=repository.get(original.id)
    assert loaded.prompt_text==body and loaded.generation_settings==GenerationSettings(visual_style=style).to_dict()
    assert (tmp_path/'presets.json').read_bytes()==before
    job=Job('source');job.snapshot_preset(loaded)
    assert job.require_generation_settings().visual_style==style
    assert Job.from_dict(job.to_dict()).require_generation_settings().visual_style==style

@pytest.mark.parametrize('extra',['\n・追加の構成指示','\nDo something else','\nproducer note'])
def test_extra_instructions_never_silently_discarded(extra):
    with pytest.raises(UnsupportedGenerationPreset):map_legacy_preset(BODY+extra)
    job=Job('source');job.snapshot_preset(Preset('p','p',BODY+extra,'c','u'))
    with pytest.raises(UnsupportedGenerationPreset):job.require_generation_settings()

def test_settings_snapshot_hash_prevents_mutation(studio):
    s=studio;s.job.generation_settings_snapshot['visual_style']='anime'
    with pytest.raises(ValueError,match='HASH_MISMATCH'):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0

def test_engine_primary_does_not_call_legacy_chat(studio):
    s=studio
    s.dom.start_video_generation_from_chat=lambda *_:pytest.fail('Primary must not send Chat')
    result=NotebookEngineAdapter(s.dom,persist_identity=s.repo.save).submit(s.job)
    assert result.remote_status=='GENERATING' and s.page.evaluate('window.generateClicks')==1

def test_failed_generation_retry_uses_studio_and_preserves_failed_item(studio):
    s=studio;s.page.locator('artifact-library').evaluate("e=>e.innerHTML='<artifact-library-item>動画解説を生成できませんでした。</artifact-library-item>'")
    s.dom.start_video_generation_from_chat=lambda *_:pytest.fail('Retry must not send Chat')
    result=NotebookEngineAdapter(s.dom,persist_identity=s.repo.save).retry_failed_generation(s.job)
    assert result.remote_status=='GENERATING' and s.page.evaluate('window.generateClicks')==1
    assert s.page.locator('artifact-library-item').count()==2 and s.page.evaluate('window.badClicks')==0

def test_modal_human_review_resumes_owned_studio_without_duplicate(studio):
    s=studio;s.page.evaluate('''()=>{openStudioFixture();document.body.insertAdjacentHTML('beforeend',
      '<div role="dialog" id="unknown">Unclassified introduction<button onclick="window.badClicks++">Close</button></div>');}''')
    reviewed=[]
    def human_actor(message):
        if message=='MODAL_STILL_VISIBLE':
            reviewed.append(message)
            s.page.evaluate("document.querySelector('#unknown').remove()")
    s.dom.diagnostic=human_actor
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING
    assert reviewed
    assert s.page.evaluate('window.badClicks')==0 and s.page.evaluate('window.generateClicks')==1

def test_actual_limit_blocks_generate_but_almost_warning_does_not(studio):
    from djd_maker.core.cloud_limit import CloudLimitReached
    from djd_maker.adapters.usage_limit import parse_limit_text
    from datetime import datetime
    s=studio
    observation=parse_limit_text('チャットは 23:59 まで無効',now=datetime.now().astimezone())
    def block():raise CloudLimitReached(observation)
    original=s.dom.check_usage_limit;s.dom.check_usage_limit=block
    with pytest.raises(CloudLimitReached):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0
    s.dom.check_usage_limit=original
    s.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=alert>You are near your AI usage limit</div>')")
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING

def test_source_ready_pending_is_runnable_and_not_completed(tmp_path):
    from test_pipeline import coordinator,MemoryJobs
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    source=tmp_path/'lesson.txt';source.write_text('ready source',encoding='utf8')
    job=Job(str(source),state=JobState.UPLOADING,source_status='READY',notebook_id=IDENTITY,notebook_url=URL)
    job.snapshot_preset(Preset('p','paper',BODY,'c','u'))
    pipeline=coordinator(tmp_path,MemoryJobs(job),FakeNotebookAdapter({}))
    assert pipeline._needs_dispatch(job) and not pipeline.all_tasks_completed()

def test_pending_claim_is_not_failed_and_other_jobs_remain_runnable(tmp_path):
    from test_pipeline import coordinator,MemoryJobs
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    first,second=Job('first'),Job('second')
    class Remote(FakeNotebookAdapter):
        def submit(self,job):raise StudioOutcomePending('unknown outcome')
    jobs=MemoryJobs(first,second);pipeline=coordinator(tmp_path,jobs,Remote({}))
    pipeline._run_notebook_job(first)
    assert jobs.get(first.id).state is JobState.RECOVERY_PENDING
    assert not jobs.get(first.id).error_code and pipeline._needs_dispatch(second)


def test_failed_durable_claim_save_prevents_click(studio):
    s=studio
    def save(job):
        if job.studio_generation_stage=='GENERATE_CLICKED':
            raise OSError('isolated save failure')
        s.repo.save(job)
    s.flow.persist=save
    with pytest.raises(OSError,match='save failure'):s.flow.run()
    assert s.page.evaluate('window.generateClicks')==0


def test_unknown_modal_after_generate_resumes_remote_readback_without_reclick(studio):
    s=studio;s.page.evaluate('openStudioFixture()')
    s.page.get_by_role('button',name='生成',exact=True).last.evaluate('''e=>e.addEventListener('click',()=>{
      document.body.insertAdjacentHTML('beforeend','<div role=dialog id=unknown>Review required<button onclick="window.badClicks++">Close</button></div>');
      setTimeout(()=>document.querySelector('#unknown').remove(),150)})''')
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING
    assert s.page.evaluate('window.generateClicks')==1 and s.page.evaluate('window.badClicks')==0


def test_extra_overlay_above_owned_settings_waits_for_human(studio):
    s=studio;s.page.evaluate('''()=>{openStudioFixture();
      document.body.insertAdjacentHTML('beforeend','<div data-testid=blocking-overlay id=unknown style="position:fixed;inset:0;z-index:9999">Review</div>');
      setTimeout(()=>document.querySelector('#unknown').remove(),150)}''')
    assert s.flow.run().remote_status is RemoteVideoStatus.GENERATING
    assert s.page.evaluate('window.generateClicks')==1


def test_preset_edit_does_not_change_started_job_snapshot(tmp_path):
    repository=PresetRepository(tmp_path/'presets.json')
    preset=repository.create('original',BODY)
    job=Job('source');job.snapshot_preset(repository.get(preset.id))
    repository.update(preset.id,'changed',BODY.replace('ペーパークラフト','アニメ'))
    assert job.require_generation_settings()==GenerationSettings()
    assert job.preset_body_snapshot==BODY


def test_explicit_structured_settings_cannot_discard_extra_prose(tmp_path):
    repository=PresetRepository(tmp_path/'presets.json')
    with pytest.raises(UnsupportedGenerationPreset):
        repository.create('invalid',BODY+'\nextra instruction',generation_settings=GenerationSettings().to_dict())


def test_preselected_settings_restart_skips_selection_and_generates_once(studio):
    s=studio;s.page.evaluate('openStudioFixture()')
    s.flow.select_radio('説明動画','VIDEO_TYPE_SELECTED')
    s.flow.select_radio('ペーパー クラフト','STYLE_SELECTED')
    assert s.flow.verify(GenerationSettings())
    saved=s.repo.require(s.job.id)
    assert StudioGeneration(s.dom,saved,s.repo.save,timeout_seconds=.4,poll_seconds=.02).run().remote_status is RemoteVideoStatus.GENERATING
    assert s.page.evaluate('window.generateClicks')==1 and s.page.evaluate('window.studioOpens')==0
