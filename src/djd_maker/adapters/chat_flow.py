"""GNB polling sequence with a strict chat boundary and current-turn correlation."""
from __future__ import annotations

import time
import re

from .replies import ReplyKind, classify_reply
from djd_maker.core.cancellation import checkpoint


# Keep input selection inside the central panel. No page-wide textarea fallback.
CHAT_ROOT = "chat-panel, [role='region'][aria-label='チャット'], [role='region'][aria-label='Chat'], [data-testid='chat-panel']"
# Semantic class names observed in GNB STATE_08_VIDEO_CREATE/page.html;
# never bind to generated Angular ng-c* identifiers.
USER = "[data-message-author-role='user'], .from-user-message-card-content .message-text-content, .user-message, .query-message"
ASSISTANT = "[data-message-author-role='assistant'], .to-user-message-card-content .message-text-content, .response-message, .model-message"


class ChatFlowError(RuntimeError):
    pass


class ChatFlow:
    def __init__(self, dom, *, reply_timeout=180, send_timeout=60):
        self.dom = dom
        self.page = dom.page
        self.reply_timeout = reply_timeout
        self.send_timeout = send_timeout

    def root(self):
        checkpoint('chat.interactable')
        ensure = getattr(self.dom, 'ensure_interactable', None)
        if callable(ensure):
            ensure()
        roots = self.page.locator(CHAT_ROOT)
        visible = [roots.nth(i) for i in range(roots.count()) if roots.nth(i).is_visible()]
        if len(visible) != 1:
            raise ChatFlowError("WRONG_INPUT_TARGET: 中央Chat panelを一意に確認できません")
        return visible[0]

    def input(self):
        root = self.root()
        inputs = root.locator("textarea.query-box-input, textarea[placeholder], [role='textbox'][contenteditable='true']")
        candidates = []
        for i in range(inputs.count()):
            control = inputs.nth(i)
            valid = control.evaluate("e => !e.closest('source-panel, sources-panel, .source-panel, [data-testid*=source], [aria-label*=Research]')")
            if valid and control.is_visible() and control.is_enabled():
                candidates.append(control)
        if len(candidates) != 1:
            raise ChatFlowError("WRONG_INPUT_TARGET: 入力先が中央Chatの質問欄ではありません")
        self.dom.diagnostic("TARGET_CHAT_INPUT_VALIDATED")
        return candidates[0]

    def turns(self):
        # A single DOM query preserves document order and deduplicates selectors.
        return self.root().evaluate("""(root, selectors) => {
          const nodes = [...root.querySelectorAll(selectors.user + ',' + selectors.assistant)];
          return nodes.filter(e => !nodes.some(p => p !== e && p.contains(e))).map(e => ({
            role: e.matches(selectors.user) ? 'user' : 'assistant',
            text: e.matches(selectors.user) ? e.textContent : e.innerText,
          }));
        }""", {"user": USER, "assistant": ASSISTANT})

    def correlated_reply(self, prompt, baseline):
        turns = self.turns()
        # Require the pre-send history prefix to remain unchanged. A reset or
        # virtualized history is ambiguous and cannot justify another send.
        if turns[:len(baseline)] != baseline:
            raise ChatFlowError("CHAT_HISTORY_CHANGED: 送信前履歴との対応を確認できません")
        new = turns[len(baseline):]
        if not new or new[0]["role"] != "user" or new[0]["text"] != prompt:
            return False, ""
        replies = []
        for turn in new[1:]:
            if turn["role"] == "user":
                break
            replies.append(turn["text"])
        return True, "\n".join(replies)

    def wait_reply(self, prompt, baseline):
        deadline = time.monotonic() + self.reply_timeout
        previous = None
        stable = time.monotonic()
        message_reported = False
        while time.monotonic() < deadline:
            found, reply = self.correlated_reply(prompt, baseline)
            if found and not message_reported:
                self.dom.diagnostic("PRESET_SENT:current_user_message_confirmed")
                message_reported = True
            if reply != previous:
                previous, stable = reply, time.monotonic()
            result = classify_reply(reply, now=self.dom.clock())
            if found and result.kind in {ReplyKind.GENERATION_ACCEPTED, ReplyKind.QUOTA_EXHAUSTED} and time.monotonic() - stable >= 2:
                self.dom.diagnostic(f"REPLY_CLASSIFIED:{result.kind}:score={result.score}:terms={','.join(result.matched_terms)}")
                return result
            self.page.wait_for_timeout(2000)
        return None

    def send(self, prompt):
        baseline = None
        last_target_error = None
        for attempt in range(1, 4):
            checkpoint('chat.retry')
            if baseline is not None:
                # Read again just before retransmission to catch delayed replies.
                found, reply = self.correlated_reply(prompt, baseline)
                result = classify_reply(reply, now=self.dom.clock())
                if found and result.kind in {ReplyKind.GENERATION_ACCEPTED, ReplyKind.QUOTA_EXHAUSTED}:
                    return result
                if self.dom.inspect_status().value in {"READY", "GENERATING", "WAITING"}:
                    raise ChatFlowError("GENERATION_STATE_UNCERTAIN: artifactあり。追加送信を停止")
            self.dom.diagnostic(f"PRESET_ATTEMPT:{attempt}/3")
            try:
                baseline = self.turns()
                control = self.input()
            except ChatFlowError as exc:
                if not str(exc).startswith("WRONG_INPUT_TARGET"):
                    raise
                last_target_error = exc
                baseline = None
                self.page.wait_for_timeout(500)
                continue
            control.fill(prompt)
            if self.dom._input_text(control) != prompt:
                raise ChatFlowError("PRESET_APPLY_MISMATCH: 入力本文が一致しません")
            deadline = time.monotonic() + self.send_timeout
            sent = False
            while time.monotonic() < deadline:
                buttons = self.root().get_by_role("button", name=re.compile(r"^(送信|Send)$"))
                active = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible() and buttons.nth(i).is_enabled()]
                if len(active) == 1:
                    checkpoint('chat.send')
                    self.dom.diagnostic("CHAT_SEND_ENABLED")
                    active[0].click()
                    sent = True
                    break
                self.page.wait_for_timeout(250)
            if sent:
                result = self.wait_reply(prompt, baseline)
                if result is not None:
                    return result
        if last_target_error is not None:
            raise last_target_error
        raise ChatFlowError("PRESET_RESPONSE_TIMEOUT: 3回の送信試行で今回の生成開始・クォータ返信を確認できません")
