"""Exercise browser DOM scoping and delayed replies without Google side effects."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from djd_maker.adapters.browser import find_chrome
from djd_maker.adapters.chat_flow import ChatFlow, ChatFlowError
from djd_maker.adapters.replies import ReplyKind
from djd_maker.adapters.notebook import NotebookDomAdapter, RemoteVideoStatus


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(find_chrome()), headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.set_content("""
      <section class='source-panel'><textarea placeholder='ウェブで新しいソースを検索'></textarea><button aria-label='送信'>source send</button></section>
      <chat-panel><div id='history'></div><textarea class='query-box-input' placeholder='質問をするか、何かを作成してみましょう'></textarea><button id='send' aria-label='送信' disabled>send</button></chat-panel>
      <script>
      window.sends=0;
      const input=document.querySelector('chat-panel textarea');
      input.oninput=()=>setTimeout(()=>document.querySelector('#send').disabled=false, 100);
      document.querySelector('#send').onclick=()=>{
        window.sends++;
        const node=document.createElement('div');node.dataset.messageAuthorRole='user';node.textContent=input.value;
        document.querySelector('#history').append(node);input.value='';
        if(window.replyText) setTimeout(()=>{
          const answer=document.createElement('div');answer.dataset.messageAuthorRole='assistant';answer.textContent=window.replyText;
          document.querySelector('#history').append(answer);
        }, window.replyDelay||100);
      };
      </script>
    """)
    yield page
    page.close()


def flow(page, timeout=6):
    dom = SimpleNamespace(page=page, clock=lambda: datetime.now(timezone.utc), diagnostic=lambda _: None,
                          _input_text=lambda control: control.input_value(), inspect_status=lambda: RemoteVideoStatus.NOT_STARTED)
    return ChatFlow(dom, reply_timeout=timeout, send_timeout=.5)


def test_correct_chat_target_wait_enabled_and_current_reply(page):
    page.evaluate("window.replyText='説明動画の作成を開始しました。'")
    result = flow(page).send("本文\n日本語")
    assert result.kind is ReplyKind.GENERATION_ACCEPTED
    assert page.locator('.source-panel textarea').input_value() == ""
    assert page.evaluate("window.sends") == 1


def test_source_search_is_rejected_without_central_panel(page):
    page.locator('chat-panel').evaluate("e=>e.remove()")
    with pytest.raises(ChatFlowError, match="WRONG_INPUT_TARGET"):
        flow(page).send("本文")
    assert page.locator('.source-panel textarea').input_value() == ""


def test_disabled_send_never_clicked_max_three(page):
    page.evaluate("document.querySelector('chat-panel textarea').oninput=null")
    with pytest.raises(ChatFlowError, match="3回"):
        flow(page, .1).send("本文")
    assert page.evaluate("window.sends") == 0


def test_no_response_maximum_three_sends(page):
    with pytest.raises(ChatFlowError, match="PRESET_RESPONSE_TIMEOUT"):
        flow(page, .1).send("本文")
    assert page.evaluate("window.sends") == 3


def test_old_quota_reply_ignored(page):
    page.locator('#history').evaluate("e=>e.innerHTML=\"<div data-message-author-role='user'>本文</div><div data-message-author-role='assistant'>動画生成はクォータ不足で利用できません</div>\"")
    page.evaluate("window.replyText='解説動画の作成を開始しました。'")
    assert flow(page).send("本文").kind is ReplyKind.GENERATION_ACCEPTED
    assert page.evaluate("window.sends") == 1


def test_late_reply_prevents_duplicate_send(page):
    page.evaluate("window.replyText='説明動画の作成を開始しました。';window.replyDelay=700")
    # The reply arrives during the last poll. The boundary recheck must catch it.
    assert flow(page, .2).send("本文").kind is ReplyKind.GENERATION_ACCEPTED
    assert page.evaluate("window.sends") == 1


def test_current_quota_reply(page):
    page.evaluate("window.replyText='動画生成は利用制限に達しているため作成を開始することができません。約2時間25分後に回復。'")
    assert flow(page).send("本文").kind is ReplyKind.QUOTA_EXHAUSTED


def test_source_error_is_not_ready(page):
    page.locator('.source-panel').evaluate("e=>e.innerHTML+='<span>ソースのアップロード中にエラーが発生しました。</span>'")
    assert NotebookDomAdapter(page).source_state('lesson.txt') == "ERROR"


def test_source_tooltip_error_detected(page):
    page.locator('.source-panel').evaluate("e=>e.innerHTML+='<button title=\"ソースのアップロード中にエラー\">!</button>'")
    assert NotebookDomAdapter(page).source_state('lesson.txt') == "ERROR"


def test_other_source_error_does_not_poison_ready_target(page):
    page.locator('.source-panel').evaluate("""p=>p.innerHTML=`
      <div class='single-source-container'><button class='source-stretched-button' aria-label='lesson.txt'>lesson.txt</button></div>
      <div class='single-source-container'><button class='source-stretched-button' aria-label='other.txt'>other.txt</button><span>ソースのアップロード中にエラー</span></div>`""")
    dom = NotebookDomAdapter(page)
    dom._source_processing = lambda: False
    dom._chat_source_count_positive = lambda: True
    dom._studio_video_card_active = lambda: True
    assert dom.source_state('lesson.txt') == "READY"
    assert dom.source_state('other.txt') == "ERROR"


def test_source_retry_is_scoped_to_matching_error_card(page, tmp_path):
    page.locator('.source-panel').evaluate("""p=>{
      p.innerHTML=`<div class='single-source-container'><button class='source-stretched-button' aria-label='lesson.txt'>lesson.txt</button><span class='err'>ソースのアップロード中にエラー</span><button class='retry'>再試行</button></div>
      <div class='single-source-container'><button class='source-stretched-button' aria-label='other.txt'>other.txt</button><button class='other'>再試行</button></div>`;
      window.targetRetries=0;window.otherRetries=0;
      p.querySelector('.retry').onclick=()=>{window.targetRetries++;p.querySelector('.err').remove()};
      p.querySelector('.other').onclick=()=>window.otherRetries++;
    }""")
    dom = NotebookDomAdapter(page)
    dom.wait_for_source_ready = lambda _: None
    dom.ensure_source(tmp_path / 'lesson.txt')
    assert page.evaluate('window.targetRetries') == 1
    assert page.evaluate('window.otherRetries') == 0


def test_source_retry_missing_preserves_failed_card(page, tmp_path):
    from djd_maker.adapters.notebook import SourceProcessingError
    page.locator('.source-panel').evaluate("""p=>p.innerHTML=`<div class='single-source-container'><button class='source-stretched-button' aria-label='lesson.txt'>lesson.txt</button><span>ソースのアップロード中にエラー</span></div>`""")
    dom = NotebookDomAdapter(page)
    with pytest.raises(SourceProcessingError, match='再試行UI'):
        dom.ensure_source(tmp_path / 'lesson.txt')
    assert page.locator('.single-source-container').count() == 1


def test_duplicate_source_identity_rejected(page):
    from djd_maker.adapters.notebook import SourceProcessingError
    page.locator('.source-panel').evaluate("""p=>p.innerHTML=`<div class='single-source-container'><button aria-label='lesson.txt'>lesson.txt</button></div>`.repeat(2)""")
    with pytest.raises(SourceProcessingError, match='SOURCE_IDENTITY_AMBIGUOUS'):
        NotebookDomAdapter(page).source_state('lesson.txt')


def test_source_error_material_tooltip_is_read_by_association(page):
    page.locator('.source-panel').evaluate("""p=>p.innerHTML=`<div class='single-source-container'><button class='source-stretched-button' aria-label='lesson.txt'>lesson.txt</button><span aria-describedby='source-error-tooltip'>!</span></div>`""")
    page.evaluate("""()=>{const tooltip=document.createElement('div');tooltip.id='source-error-tooltip';tooltip.hidden=true;tooltip.textContent='ソースのアップロード中にエラーが発生しました。';document.body.append(tooltip)}""")
    assert NotebookDomAdapter(page).source_state('lesson.txt') == "ERROR"


def test_persistent_source_error_retries_at_most_three_without_reupload(page, tmp_path):
    from djd_maker.adapters.notebook import SourceProcessingError
    page.locator('.source-panel').evaluate("""p=>{
      p.innerHTML=`<div class='single-source-container'><button aria-label='lesson.txt'>lesson.txt</button><span>ソースのアップロード中にエラー</span><button class='retry'>再試行</button></div>`;
      window.retries=0;p.querySelector('.retry').onclick=()=>window.retries++;
    }""")
    dom = NotebookDomAdapter(page)
    def still_error(_name):
        raise SourceProcessingError("SOURCE_UPLOAD_FAILED: persistent fixture error")
    dom.wait_for_source_ready = still_error
    dom.upload_txt = lambda _: pytest.fail("existing source must not be duplicated")
    dom.create_notebook = lambda: pytest.fail("source failure must not recreate Notebook")
    with pytest.raises(SourceProcessingError, match='persistent'):
        dom.ensure_source(tmp_path / 'lesson.txt')
    assert page.evaluate('window.retries') == 3
    assert page.locator('.single-source-container').count() == 1
