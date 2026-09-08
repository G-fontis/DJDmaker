from types import SimpleNamespace
import pytest
from djd_maker.adapters.notebook import (
    NotebookDomAdapter, NotebookEngineAdapter, RemoteVideoStatus,
    GenerationOutcome, GenerationRetryExhausted, SourceRetryExhausted,
)
from djd_maker.adapters.credit import CreditSnapshot
from djd_maker.core.models import Job, JobState, preset_body_sha256
from djd_maker.core.cloud_limit import parse_limit_text
from test_v12_chat_dom import browser, page
from test_pipeline import MemoryJobs, coordinator
from test_v122_sequential_runtime import Remote
from test_v125_limit_pause_contract import NOW


def failed_engine(page, tmp_path):
    page.evaluate('''()=>{
      const card=document.createElement('artifact-library-item');
      card.innerHTML='動画解説を生成できませんでした。<button>再試行</button>';
      window.studioRetries=0;card.querySelector('button').onclick=()=>window.studioRetries++;
      document.body.append(card);
    }''')
    source = tmp_path/'source.txt'
    source.write_text('source', encoding='utf-8')
    job = Job(str(source), notebook_id='existing', notebook_url='https://notebook.google.com/notebook/existing',
              preset_body_snapshot='snapshot A', preset_body_sha256=preset_body_sha256('snapshot A'))
    dom = NotebookDomAdapter(page)
    dom.ensure_source = lambda path: None
    dom._wait_for_generation_chat_ready = lambda: None
    repo = MemoryJobs(job)
    engine = NotebookEngineAdapter(dom, persist_identity=repo.save)
    engine._open_job = lambda job: None
    engine.inspect_status = lambda job: dom.inspect_status().value
    return engine, job, repo


def test_failed_artifact_resends_snapshot_not_studio_retry(page, tmp_path, monkeypatch):
    from djd_maker.adapters.replies import ReplyKind, ReplyResult
    engine, job, repo = failed_engine(page, tmp_path)
    sent = []
    def send(flow, prompt, *, max_attempts):
        assert max_attempts == 1
        assert repo.get(job.id).attempt_by_stage['generation.failed_retry'] == 1
        sent.append(prompt)
        page.locator('artifact-library-item').evaluate("e=>e.textContent='動画を生成しています'")
        return ReplyResult(ReplyKind.GENERATION_ACCEPTED)
    monkeypatch.setattr('djd_maker.adapters.chat_flow.ChatFlow.send', send)
    result = engine.submit(job)
    assert result.remote_status is RemoteVideoStatus.GENERATING
    assert sent == ['snapshot A']
    assert page.evaluate('window.studioRetries') == 0


def test_failed_retry_exhausted_is_persisted_max_three(page, tmp_path):
    engine, job, repo = failed_engine(page, tmp_path)
    job.attempt_by_stage['generation.failed_retry'] = 3
    job = Job.from_dict(job.to_dict())
    with pytest.raises(GenerationRetryExhausted):
        engine.retry_failed_generation(job)
    assert page.evaluate('window.sends') == 0
    assert page.evaluate('window.studioRetries') == 0


@pytest.mark.parametrize('state', ['READY', 'GENERATING'])
def test_retry_rechecks_remote_no_duplicate_when_already_started(page, tmp_path, state):
    engine, job, repo = failed_engine(page, tmp_path)
    engine.inspect_status = lambda job: state
    assert engine.retry_failed_generation(job).remote_status.value == state
    assert page.evaluate('window.sends') == 0


def test_crash_after_retry_send_uses_correlated_reply_without_resend(page, tmp_path):
    engine, job, repo = failed_engine(page, tmp_path)
    job.generation_retry_turn_count = 0
    job.attempt_by_stage['generation.failed_retry'] = 1
    page.locator('#history').evaluate('''e=>e.innerHTML=`<div data-message-author-role="user">snapshot A</div>
      <div data-message-author-role="assistant">説明動画の作成を開始しました。</div>`''')
    from djd_maker.adapters.notebook import NotebookAdapterError
    with pytest.raises(NotebookAdapterError, match='GENERATION_STATE_UNCERTAIN'):
        engine.retry_failed_generation(job)
    assert page.evaluate('window.sends') == 0


@pytest.mark.parametrize('blocked', [False, True])
def test_failed_artifact_preserved_as_generation_retry_pending(tmp_path, blocked):
    job = Job('failed.txt', state=JobState.WAITING_VIDEO, notebook_id='existing',
              notebook_url='https://notebook.google.com/notebook/existing')
    remote = Remote([], {job.id:'FAILED'})
    repo = MemoryJobs(job)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.cloud_limit.clock = lambda: NOW
    if blocked:
        pipe.cloud_limit.block(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    pipe.run_cycle()
    saved = repo.get(job.id)
    assert saved.state is JobState.WAITING
    assert saved.resume_checkpoint == 'FAILED_ARTIFACT_RETRY'
    assert not saved.error_code
    assert not pipe.all_tasks_completed()
    assert not any(e[0] == 'submit' for e in remote.events)


@pytest.mark.parametrize('error', [SourceRetryExhausted, GenerationRetryExhausted])
def test_retry_exhaustion_isolates_one_job(tmp_path, error):
    bad, good = Job('bad.txt'), Job('good.txt')
    remote = Remote([])
    original = remote.submit
    def submit(job):
        if job.id == bad.id:
            raise error('RETRY_EXHAUSTED: fixture')
        return original(job)
    remote.submit = submit
    repo = MemoryJobs(bad, good)
    pipe = coordinator(tmp_path, repo, remote)
    pipe.run_cycle()
    assert repo.get(bad.id).failure_class == 'TERMINAL_FAILED'
    assert ('submit', good.id) in remote.events


def test_failed_card_does_not_hide_new_generating_card(page, tmp_path):
    engine, job, repo = failed_engine(page, tmp_path)
    page.evaluate("()=>{const card=document.createElement('artifact-library-item');card.textContent='動画を生成しています';document.body.append(card)}")
    assert engine.dom.inspect_status() is RemoteVideoStatus.GENERATING


def test_current_quota_retry_defers_without_exhausting_generation_budget(page, tmp_path, monkeypatch):
    from djd_maker.adapters.replies import ReplyKind, ReplyResult
    from djd_maker.core.cloud_limit import CloudLimitReached
    engine, job, repo = failed_engine(page, tmp_path)
    monkeypatch.setattr('djd_maker.adapters.chat_flow.ChatFlow.send',
                        lambda *args, **kwargs: ReplyResult(ReplyKind.QUOTA_EXHAUSTED))
    with pytest.raises(CloudLimitReached):
        engine.retry_failed_generation(job)
    saved = repo.get(job.id)
    assert saved.generation_retry_turn_count is None
    assert saved.attempt_by_stage['generation.failed_retry'] == 0
    assert page.evaluate('window.studioRetries') == 0


def test_three_failed_generations_then_terminal_without_fourth_send(page, tmp_path, monkeypatch):
    from djd_maker.adapters.replies import ReplyKind, ReplyResult
    engine, job, repo = failed_engine(page, tmp_path)
    sends = []
    def send(flow, prompt, **kwargs):
        sends.append(prompt)
        page.locator('artifact-library-item').evaluate("e=>e.textContent='動画を生成しています'")
        return ReplyResult(ReplyKind.GENERATION_ACCEPTED)
    monkeypatch.setattr('djd_maker.adapters.chat_flow.ChatFlow.send', send)
    for attempt in range(1, 4):
        page.locator('artifact-library-item').evaluate("e=>e.textContent='動画解説を生成できませんでした。'")
        engine.retry_failed_generation(job)
        job = repo.get(job.id)
        assert job.attempt_by_stage['generation.failed_retry'] == attempt
    with pytest.raises(GenerationRetryExhausted):
        page.locator('artifact-library-item').evaluate("e=>e.textContent='動画解説を生成できませんでした。'")
        engine.retry_failed_generation(job)
    assert sends == ['snapshot A']*3


def test_real_limit_blocks_failed_retry_before_claim_or_send(page, tmp_path):
    from djd_maker.core.cloud_limit import CloudLimitReached
    engine, job, repo = failed_engine(page, tmp_path)
    def limited():
        raise CloudLimitReached(parse_limit_text('チャットは 6:21 まで無効', now=NOW))
    engine.dom.check_usage_limit = limited
    with pytest.raises(CloudLimitReached):
        engine.retry_failed_generation(job)
    assert not job.attempt_by_stage
    assert page.evaluate('window.sends') == 0


def test_failed_retry_rejects_changed_snapshot(page, tmp_path):
    engine, job, repo = failed_engine(page, tmp_path)
    job.preset_body_snapshot = 'edited GUI content, invalid job hash'
    with pytest.raises(ValueError, match='PRESET_SNAPSHOT_HASH_MISMATCH'):
        engine.retry_failed_generation(job)
    assert page.evaluate('window.sends') == 0
