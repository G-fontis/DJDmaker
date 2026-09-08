from datetime import datetime, timedelta, timezone

import pytest

from djd_maker.adapters.credit import CreditDetector, CreditState
from djd_maker.adapters.usage_limit import UsageLimitDetector
from djd_maker.core.cloud_limit import CloudLimitGate, parse_limit_text
from djd_maker.core.runtime_operation import operation_scope
from test_v12_chat_dom import browser, page, flow

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone(timedelta(hours=9)))
WARNING = "You're almost at your AI usage limit. Limit resets at 16:21."


def test_warning_does_not_block():
    assert parse_limit_text(WARNING, now=NOW) is None


@pytest.mark.parametrize('text', [
    "You've reached your AI usage limit. Limit resets at 16:21.",
    'AI usage limit reached. Limit resets at 16:21.',
    'Chat is disabled until 16:21.',
])
def test_english_hard_limit_and_margin(tmp_path, text):
    gate = CloudLimitGate(tmp_path / 'limit.json', clock=lambda: NOW)
    gate.block(parse_limit_text(text, now=NOW))
    assert gate.blocked
    assert datetime.fromisoformat(gate.state['cloud_resume_at']).strftime('%H:%M') == '16:26'


def install(page, text=WARNING, disabled=False):
    page.locator('chat-panel').evaluate('''(e, v) => {
        e.innerHTML = '<div class="banner-text" role="status"></div><query-box><textarea></textarea><button aria-label="Send" disabled>Send</button></query-box>';
        e.querySelector('.banner-text').textContent = v.text;
        e.querySelector('textarea').disabled = v.disabled;
    }''', dict(text=text, disabled=disabled))


def test_real_dom_warning_empty_send_is_not_disabled_chat(page):
    install(page)
    events = []
    with operation_scope(lambda stage, fields: events.append((stage, fields))):
        observation, enabled = UsageLimitDetector(page, lambda: NOW).observe()
    assert observation is None and enabled
    assert events[0][0] == 'limit.warning'
    assert WARNING in events[0][1]['message']
    assert CreditDetector(page, clock=lambda: NOW).detect().state is not CreditState.EXHAUSTED


def test_warning_with_disabled_chat_during_reply_does_not_block(page):
    install(page, disabled=True)
    page.locator('chat-panel textarea').evaluate("e => e.setAttribute('placeholder', '回答しています...')")
    observation, enabled = UsageLimitDetector(page, lambda: NOW).observe()
    assert observation is None and not enabled


def test_explicit_limit_with_almost_banner_still_blocks(page):
    install(page, text=WARNING + '\nChat is disabled until 16:21.', disabled=True)
    observation, enabled = UsageLimitDetector(page, lambda: NOW).observe()
    assert observation.chat_disabled and not enabled
    assert observation.blocked_until.hour == 16


@pytest.mark.parametrize('container', ['source-panel', 'div data-message-author-role="user"', 'div data-message-author-role="assistant"'])
def test_warning_content_is_ignored(page, container):
    tag = container.split()[0]
    page.evaluate('(html) => document.body.insertAdjacentHTML("beforeend", html)',
                  f'<{container}>{WARNING}</{tag}>')
    events = []
    with operation_scope(lambda stage, fields: events.append(stage)):
        assert UsageLimitDetector(page, lambda: NOW).observe()[0] is None
    assert 'limit.warning' not in events


def test_warning_does_not_release_existing_gate(page, tmp_path):
    gate = CloudLimitGate(tmp_path / 'limit.json', clock=lambda: NOW)
    gate.block(parse_limit_text('Chat is disabled until 16:21.', now=NOW))
    install(page)
    assert UsageLimitDetector(page, lambda: NOW).observe()[0] is None
    assert gate.blocked and not gate.due
