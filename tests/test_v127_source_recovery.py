from pathlib import Path
import pytest
from djd_maker.adapters.notebook import NotebookDomAdapter, SourceState, SourceProcessingError, SourceRetryExhausted
from djd_maker.core.models import Job
from test_v12_chat_dom import browser, page


def failed_row(page):
    page.locator('.source-panel').evaluate('''p=>{
      p.innerHTML=`<div class="single-source-container single-source-error-container">
        <button class="source-stretched-button" aria-label="lesson.txt"></button>
        <button class="source-item-more-button">もっと見る</button>
        <span aria-label="エラー情報">info</span></div>`;
      window.bulkDeletes=0;
      p.querySelector('.source-item-more-button').onclick=()=>{
        const menu=document.createElement('div');menu.setAttribute('role','menu');
        menu.innerHTML=`<button role="menuitem" id="one">ソースを削除</button><button role="menuitem" id="all">失敗したソースをすべて削除</button>`;
        document.body.append(menu);
        menu.querySelector('#one').onclick=()=>{p.querySelector('.single-source-container').remove();menu.remove()};
        menu.querySelector('#all').onclick=()=>window.bulkDeletes++;
      };
    }''')


def test_live_class_source_failed_not_loading(page):
    failed_row(page)
    dom = NotebookDomAdapter(page)
    assert dom.classify_source('lesson.txt') is SourceState.FAILED
    with pytest.raises(SourceProcessingError):
        dom.wait_for_source_ready('lesson.txt', timeout_ms=1000)


def test_source_failed_reupload_verified_absence_and_success(page, tmp_path):
    failed_row(page)
    dom = NotebookDomAdapter(page)
    uploaded = []
    def upload(source):
        assert not page.locator('.single-source-container').count()
        uploaded.append(source.name)
    dom.upload_txt = upload
    dom.wait_for_source_ready = lambda name: None
    dom.ensure_source(tmp_path/'lesson.txt')
    assert uploaded == ['lesson.txt']
    assert page.evaluate('window.bulkDeletes') == 0


def test_source_retry_budget_survives_restart(page, tmp_path):
    failed_row(page)
    job = Job(str(tmp_path/'lesson.txt'))
    job.attempt_by_stage['source.reupload'] = 3
    dom = NotebookDomAdapter(page)
    dom.source_recovery_job = Job.from_dict(job.to_dict())
    with pytest.raises(SourceRetryExhausted):
        dom.ensure_source(Path(job.source_path))
    assert page.locator('.single-source-container').count() == 1


def test_source_unknown_is_distinct_from_uploading(page):
    page.locator('.source-panel').evaluate('p=>p.innerHTML=`<button class="source-stretched-button" aria-label="lesson.txt">lesson.txt</button>`')
    dom = NotebookDomAdapter(page)
    assert dom.classify_source('lesson.txt') is SourceState.UNKNOWN
    assert dom.classify_source('absent.txt') is SourceState.ABSENT


def test_source_unknown_timeout_reclassified_once(page, tmp_path):
    from djd_maker.adapters.notebook import SourceReadyTimeoutError
    dom = NotebookDomAdapter(page)
    states = []
    dom.source_state = lambda name: states.append(name) or 'UNKNOWN'
    dom.wait_for_source_ready = lambda name: (_ for _ in ()).throw(SourceReadyTimeoutError('timeout'))
    with pytest.raises(SourceProcessingError, match='SOURCE_UNKNOWN'):
        dom.ensure_source(tmp_path/'lesson.txt')
    assert len(states) == 2


def test_source_becomes_ready_while_menu_open_is_not_deleted(page):
    failed_row(page)
    page.locator('.source-item-more-button').evaluate('''e=>{
      const open=e.onclick;
      e.onclick=()=>{open();document.querySelector('.single-source-container').classList.remove('single-source-error-container');
        document.querySelector('[aria-label="エラー情報"]').remove()};
    }''')
    dom = NotebookDomAdapter(page)
    with pytest.raises(SourceProcessingError, match='SOURCE_STATE_CHANGED'):
        dom.remove_failed_source('lesson.txt')
    assert page.locator('.single-source-container').count() == 1


def test_timeout_final_reclassification_catches_failed_source(page, monkeypatch):
    dom = NotebookDomAdapter(page)
    dom.source_state = lambda name: 'ERROR'
    with pytest.raises(SourceProcessingError, match='timeout再診断'):
        dom.wait_for_source_ready('lesson.txt', timeout_ms=0)
