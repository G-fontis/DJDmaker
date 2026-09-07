"""Conservative, dialog-scoped service announcement handling."""
from enum import StrEnum
import re
import time
from djd_maker.core.cancellation import checkpoint
from djd_maker.core.runtime_operation import report_operation


class ModalKind(StrEnum):
    ANNOUNCEMENT = 'ANNOUNCEMENT'
    ONBOARDING = 'ONBOARDING'
    SAFE_INFO = 'SAFE_INFO'
    ACTION_CONFIRMATION = 'ACTION_CONFIRMATION'
    UNKNOWN = 'UNKNOWN'


class BlockingModalError(RuntimeError):
    pass


def classify_modal(text):
    normalized = re.sub(r'\s+', '', text).casefold()
    if any(word in normalized for word in ('削除','購入','支払い','確認してください','delete','purchase','payment','areyousure')):
        return ModalKind.ACTION_CONFIRMATION
    if ('gemininotebookの使用方法をより柔軟に管理できるようになりました' in normalized
        or all(word in normalized for word in ('limitsrefreshevery5hours','newbackgroundqueue'))):
        return ModalKind.ANNOUNCEMENT
    if normalized.startswith(('notebooklmへようこそ','welcometonotebooklm')):
        return ModalKind.ONBOARDING
    if normalized.startswith(('notebooklmの新機能','what’snewinnotebooklm',"what'snewinnotebooklm")):
        return ModalKind.SAFE_INFO
    return ModalKind.UNKNOWN


def ensure_notebook_interactable(page, *, diagnostic=lambda _: None, target=None, timeout_seconds=5):
    deadline = time.monotonic()+timeout_seconds
    dismissed = False
    while True:
        checkpoint('modal.detect')
        dialogs = page.locator('[role="dialog"], [aria-modal="true"], dialog[open]')
        visible = [dialogs.nth(i) for i in range(dialogs.count()) if dialogs.nth(i).is_visible()]
        if not visible:
            if not dismissed:
                return
            scrims = page.locator('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, [data-testid="modal-scrim"]')
            blocked = any(scrims.nth(i).is_visible() for i in range(scrims.count()))
            if not blocked:
                checkpoint('modal.target_trial')
                if target is None:
                    controls = page.locator('chat-panel textarea, section.source-panel button[aria-label="ソースを追加"], section.source-panel button[aria-label="Add source"]')
                    target = next((controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible() and controls.nth(i).is_enabled()), None)
                if target is None:
                    raise BlockingModalError('BLOCKING_MODAL_TARGET_UNVERIFIED')
                target.click(trial=True, timeout=1000)
                # When both surfaces exist, verify both; never click an action.
                controls = page.locator('chat-panel textarea, section.source-panel button[aria-label="ソースを追加"], section.source-panel button[aria-label="Add source"]')
                for i in range(controls.count()):
                    control = controls.nth(i)
                    if control.is_visible() and control.is_enabled():
                        control.click(trial=True, timeout=1000)
                diagnostic('MODAL_DISMISSED:dialog_hidden:scrim_gone')
                report_operation('modal.ready')
                return
        else:
            # Nested or concurrent dialogs are ambiguous; do not guess an X.
            if len(visible) != 1:
                raise BlockingModalError('BLOCKING_MODAL_UNKNOWN: multiple dialogs')
            dialog = visible[0]
            kind = classify_modal(dialog.inner_text())
            report_operation('modal.found', decision=kind.value)
            diagnostic(f'BLOCKING_MODAL:{kind.value}')
            if kind in {ModalKind.UNKNOWN, ModalKind.ACTION_CONFIRMATION}:
                raise BlockingModalError(f'BLOCKING_MODAL_{kind.value}: 自動で閉じず停止します')
            close = dialog.get_by_role('button', name=re.compile(r'^(閉じる|ダイアログを閉じる|Close|close|Dismiss|×|✕)$'))
            if close.count() != 1:
                close = dialog.locator('button:has(mat-icon:text-is("close"))')
            if close.count() != 1 or not close.first.is_visible() or not close.first.is_enabled():
                raise BlockingModalError('BLOCKING_MODAL_CLOSE_UNVERIFIED')
            if not dismissed:
                checkpoint('modal.dismiss')
                close.first.click(timeout=1000)
                dismissed = True
        if time.monotonic() >= deadline:
            raise BlockingModalError('BLOCKING_MODAL_DISMISS_TIMEOUT')
        page.wait_for_timeout(100)
