from datetime import datetime, timezone

import pytest

from djd_maker.adapters.notebook import NotebookDomAdapter
from djd_maker.adapters.replies import classify_reply, ReplyKind
from djd_maker.core.cloud_limit import CloudLimitGate, CloudLimitReached
from djd_maker.core.runtime_operation import operation_scope
from djd_maker.orchestration.task_discovery import Capabilities


@pytest.mark.parametrize('suffix', ['', ' 利用枠が回復した際（約3時間後）に再開できます。'])
def test_current_generation_budget_shortage_blocks_generation_not_collection(monkeypatch, tmp_path, suffix):
    now = datetime(2026, 9, 8, 4, tzinfo=timezone.utc)
    text = '現在、動画生成に必要な利用枠（クォータ）が不足しているため、直接の動画生成を行うことができません。' + suffix
    reply = classify_reply(text, now=now)
    assert reply.kind is ReplyKind.QUOTA_EXHAUSTED

    class EmptyCards:
        def count(self):
            return 0

    class Page:
        def locator(self, selector):
            assert selector == 'artifact-library-item'
            return EmptyCards()

    adapter = NotebookDomAdapter(Page(), clock=lambda: now)
    monkeypatch.setattr(adapter, 'ensure_interactable', lambda: None)
    monkeypatch.setattr(adapter, 'check_usage_limit', lambda: None)  # No hard banner; Chat remains usable.
    monkeypatch.setattr(adapter, '_wait_for_generation_chat_ready', lambda: None)
    sent = []

    def send(flow, prompt):
        sent.append(prompt)
        return reply

    monkeypatch.setattr('djd_maker.adapters.chat_flow.ChatFlow.send', send)
    events = []
    with operation_scope(lambda stage, fields: events.append((stage, fields))):
        with pytest.raises(CloudLimitReached) as caught:
            adapter.start_video_generation_from_chat('selected snapshot')
    assert sent == ['selected snapshot']
    gate = CloudLimitGate(tmp_path / 'cloud-limit.json', clock=lambda: now)
    gate.block(caught.value.observation)
    assert gate.blocked
    capabilities = Capabilities.from_limit(gate.blocked)
    assert not capabilities.can_generate
    assert capabilities.can_check_artifact and capabilities.can_download and capabilities.can_local_process
    assert events[-1][1]['decision'] == 'QUOTA_EXHAUSTED'
    assert 'Download' in events[-1][1]['next_action']
