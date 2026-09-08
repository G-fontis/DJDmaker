"""Read visible limit banners and the central chat's disabled state."""
from djd_maker.core.cloud_limit import parse_limit_text, is_limit_warning
from djd_maker.core.runtime_operation import report_operation


class UsageLimitDetector:
    def __init__(self, page, clock):
        self.page, self.clock = page, clock

    def observe(self):
        values = self.page.evaluate("""() => {
          const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
          const roots = [...document.querySelectorAll('chat-panel, [role="region"][aria-label="チャット"], [data-testid="chat-panel"]')].filter(visible);
          const boxes = [...document.querySelectorAll('query-box')].filter(e => visible(e) && !e.closest('source-discovery-query-box, source-panel, sources-panel'));
          const inputs = [...new Set([...roots, ...boxes].flatMap(r => [...r.querySelectorAll('textarea, [role="textbox"]')]))].filter(visible);
          const sends = boxes.flatMap(r => [...r.querySelectorAll('button[aria-label="送信"], button[aria-label="Send"]')]).filter(visible);
          const disabled = e => e.disabled || e.readOnly || e.getAttribute('aria-disabled') === 'true';
          const messages = [];
          for (const e of document.querySelectorAll('body *')) {
            if (!visible(e) || e.closest('script, style, source-panel, sources-panel, .source-panel, [data-message-author-role], .message-text-content')) continue;
            const text = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
            if (/使用量上限|まで無効|以降にすべての機能|利用可能になります|usage limit|reached your limit|chat (?:is |has been )?disabled/i.test(text)) messages.push(text);
          }
          for (const e of inputs) messages.push(e.getAttribute('placeholder') || '', e.getAttribute('aria-label') || '');
          return {text: messages.join('\\n'), disabled: inputs.some(disabled) || (inputs.length === 0 && sends.some(disabled)), enabled: inputs.length === 1 && !disabled(inputs[0])};
        }""")
        observation = parse_limit_text(values['text'], now=self.clock(), chat_disabled=values['disabled'])
        if observation is None and is_limit_warning(values['text']):
            report_operation('limit.warning', decision='上限接近（生成継続）',
                             next_action='実際の上限到達・Chat無効化まで生成継続',
                             message=values['text'])
        return observation, values['enabled']
