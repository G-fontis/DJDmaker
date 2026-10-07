"""Conservative, dialog-scoped service announcement handling."""
from enum import StrEnum
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import Error as PlaywrightError
from djd_maker.core.cancellation import checkpoint, human_wait_scope, interruptible_sleep
from djd_maker.core.runtime_operation import report_operation


class ModalKind(StrEnum):
    ANNOUNCEMENT = 'ANNOUNCEMENT'
    ONBOARDING = 'ONBOARDING'
    SAFE_INFO = 'SAFE_INFO'
    AUTH_REQUIRED = 'AUTH_REQUIRED'
    USAGE_LIMIT = 'USAGE_LIMIT'
    CONSENT_REQUIRED = 'CONSENT_REQUIRED'
    ERROR_MODAL = 'ERROR_MODAL'
    SECURITY_WARNING = 'SECURITY_WARNING'
    ACTION_CONFIRMATION = 'ACTION_CONFIRMATION'
    UNKNOWN = 'UNKNOWN'


class BlockingModalError(RuntimeError):
    pass


def classify_modal(text):
    normalized = re.sub(r'\s+', '', text).casefold()
    # Safety exclusions always win over promotional copy in the same dialog.
    for kind, markers in (
        (ModalKind.AUTH_REQUIRED, ('ログインが必要', '再ログイン', 'signinrequired', 'signintocontinue')),
        (ModalKind.USAGE_LIMIT, ('利用枠の上限', '上限に達', 'クォータ不足', 'usagelimitreached', 'quotaexhausted', 'limitreached')),
        (ModalKind.CONSENT_REQUIRED, ('同意してください', 'consentrequired', 'termsofservice')),
        (ModalKind.SECURITY_WARNING, ('セキュリティ警告', 'securitywarning', '不審な操作', 'suspiciousactivity')),
        (ModalKind.ERROR_MODAL, ('notebookエラー', 'エラーが発生', '処理エラー', 'anerroroccurred', '問題が発生', 'somethingwentwrong')),
    ):
        if any(marker in normalized for marker in markers):
            return kind
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


def ensure_notebook_interactable(page, *, diagnostic=lambda _: None, target=None, timeout_seconds=5,
                                 verify_ready=None, owned_context=None, lifecycle_guard=None, poll_seconds=2):
    deadline = time.monotonic()+timeout_seconds
    dismissed = False
    while True:
        checkpoint('modal.detect')
        dialogs = page.locator('[role="dialog"], [aria-modal="true"], dialog[open]')
        visible = [dialogs.nth(i) for i in range(dialogs.count()) if dialogs.nth(i).is_visible()]
        if not visible:
            if not dismissed:
                if _visible(page, SCRIMS):
                    return wait_for_human_modal(page, diagnostic=diagnostic, target=target,
                        verify_ready=verify_ready, owned_context=owned_context,
                        lifecycle_guard=lifecycle_guard, poll_seconds=poll_seconds)
                return
            scrims = page.locator('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, [data-testid="modal-scrim"]')
            blocked = any(scrims.nth(i).is_visible() for i in range(scrims.count()))
            if not blocked:
                checkpoint('modal.target_trial')
                if verify_ready is not None:
                    if not verify_ready(page):
                        raise BlockingModalError('BLOCKING_MODAL_TARGET_UNVERIFIED')
                    diagnostic('MODAL_DISMISSED:dialog_hidden:scrim_gone')
                    report_operation('modal.ready')
                    return
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
            if len(visible) != 1 or classify_modal(visible[0].inner_text()) is ModalKind.UNKNOWN:
                return wait_for_human_modal(page, diagnostic=diagnostic, target=target,
                    verify_ready=verify_ready, owned_context=owned_context,
                    lifecycle_guard=lifecycle_guard, poll_seconds=poll_seconds)
            dialog = visible[0]
            kind = classify_modal(dialog.inner_text())
            report_operation('modal.found', decision=kind.value)
            diagnostic(f'BLOCKING_MODAL:{kind.value}')
            if kind not in {ModalKind.ANNOUNCEMENT, ModalKind.ONBOARDING, ModalKind.SAFE_INFO}:
                _classified_error(kind, page)
            close = dialog.get_by_role('button', name=re.compile(r'^(閉じる|ダイアログを閉じる|Close|close|Dismiss|×|✕)$'))
            if close.count() != 1:
                close = dialog.locator('button:has(mat-icon:text-is("close"))')
            if close.count() != 1 or not close.first.is_visible() or not close.first.is_enabled():
                raise BlockingModalError('BLOCKING_MODAL_CLOSE_UNVERIFIED')
            if not dismissed:
                checkpoint('modal.dismiss')
                report_operation('modal.dismiss')
                close.first.click(timeout=1000)
                dismissed = True
        if time.monotonic() >= deadline:
            raise BlockingModalError('BLOCKING_MODAL_DISMISS_TIMEOUT')
        page.wait_for_timeout(100)


HUMAN_MODAL_MESSAGE = (
    'Notebookに確認が必要な画面が表示されています。\n'
    'ChromeのNotebook画面を確認し、表示されているモーダルを閉じてください。\n'
    '閉じると処理は自動的に再開します。'
)
SCRIMS = ('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing, [data-testid="modal-scrim"], '
          '.modal-backdrop.show, [data-testid="blocking-overlay"]')
DIALOGS = '[role="dialog"], [aria-modal="true"], dialog[open]'


def _visible(page, selector):
    controls = page.locator(selector)
    return [controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible()]


def _identity(page):
    try:
        parsed = urlsplit(page.url)
        return parsed.scheme, parsed.hostname, parsed.path
    except Exception:
        return None


def _current_notebook(page, context, identity):
    try:
        if not page.is_closed() and (context is None or _identity(page) == identity):
            return page
    except Exception:
        if context is None:
            return page
    if context is not None:
        try:
            candidates = [p for p in context.pages if not p.is_closed() and _identity(p) == identity]
        except Exception:
            return None
        if len(candidates) == 1:
            return candidates[0]
    return None


def _usable(page, target, verify_ready):
    if verify_ready is not None:
        return bool(verify_ready(page))
    if target is None:
        controls = _visible(page, 'chat-panel textarea, section.source-panel button[aria-label="ソースを追加"], section.source-panel button[aria-label="Add source"]')
        target = next((p for p in controls if p.is_enabled()), None)
    if target is None and _identity(page) and _identity(page)[1] == 'notebook.google.com':
        if _identity(page)[2] in ('', '/'):
            from .notebook import CREATE_NOTEBOOK, NotebookDomAdapter
            target = NotebookDomAdapter(page, timeout_ms=1000)._first_enabled_visible(CREATE_NOTEBOOK, 'Notebook作成ボタン')
        else:
            controls = _visible(page, 'main, [role="main"]')
            target = controls[0] if len(controls) == 1 else None
    if target is None or not target.is_visible():
        return False
    target.click(trial=True, timeout=1000)
    return True


def _classified_error(kind, page):
    if kind is ModalKind.AUTH_REQUIRED:
        from .browser import BrowserAuthenticationRequired
        raise BrowserAuthenticationRequired('Googleへのログインが必要です。Googleログインから確認してください。')
    if kind is ModalKind.USAGE_LIMIT:
        from datetime import datetime
        from .usage_limit import UsageLimitDetector
        from djd_maker.core.cloud_limit import CloudLimitReached
        observation, _ = UsageLimitDetector(page, lambda: datetime.now().astimezone()).observe()
        if observation:
            raise CloudLimitReached(observation)
    raise BlockingModalError(f'BLOCKING_MODAL_{kind.value}: 自動で閉じず確認が必要です')


def wait_for_human_modal(page, *, diagnostic, target, verify_ready, owned_context,
                         lifecycle_guard, poll_seconds):
    identity = _identity(page)
    if owned_context is not None and (identity is None or identity[:2] != ('https', 'notebook.google.com')):
        raise BlockingModalError('NOTEBOOK_IDENTITY_UNVERIFIED')
    domain = identity[1] if identity else None
    diagnostic(f'UNKNOWN_MODAL_DETECTED:role=dialog_or_overlay:text_summary=unclassified:domain={domain}')
    report_operation('UNKNOWN_MODAL_DETECTED', human_modal_wait=True, message=HUMAN_MODAL_MESSAGE,
                     modal_role='dialog_or_overlay', text_summary='unclassified', current_domain=domain)
    reported_hidden = False
    with human_wait_scope():
        while True:
            checkpoint('modal.human_poll')
            if owned_context is not None and _identity(page) and _identity(page)[1] == 'accounts.google.com':
                _classified_error(ModalKind.AUTH_REQUIRED, page)
            current = _current_notebook(page, owned_context, identity)
            message = HUMAN_MODAL_MESSAGE
            if current is None:
                message = 'Notebook画面を確認できません。Notebookのタブを開いた状態にしてください。'
            else:
                try:
                    dialogs = _visible(current, DIALOGS)
                    kinds = [classify_modal(dialog.inner_text()) for dialog in dialogs]
                    scrims = _visible(current, SCRIMS)
                except PlaywrightError:
                    # Human dismissal can detach the DOM between observations.
                    # Re-observe without changing any durable job checkpoint.
                    dialogs, kinds, scrims = [None], [], [None]
                for kind in kinds:
                    if kind not in {ModalKind.UNKNOWN, ModalKind.ANNOUNCEMENT, ModalKind.ONBOARDING, ModalKind.SAFE_INFO}:
                        _classified_error(kind, current)
                if not dialogs and not scrims:
                    if not reported_hidden:
                        diagnostic('MODAL_DISMISSED_BY_USER')
                        report_operation('MODAL_DISMISSED_BY_USER', human_modal_wait=True)
                        reported_hidden = True
                    active = lifecycle_guard is not None and lifecycle_guard.active_download_count
                    try:
                        ready = not active and _usable(current, target if current is page else None, verify_ready)
                    except Exception:
                        ready = False
                    if ready:
                        break
            diagnostic('MODAL_STILL_VISIBLE')
            report_operation('MODAL_STILL_VISIBLE', human_modal_wait=True, message=message)
            report_operation('WAITING_FOR_HUMAN_MODAL_DISMISSAL', human_modal_wait=True,
                             message=message, decision='ユーザー確認待ち', next_action='手動で閉じると自動再開')
            deadline = time.monotonic() + max(.01, poll_seconds)
            while time.monotonic() < deadline:
                checkpoint('modal.human_poll')
                wait_ms = min(100, max(1, (deadline-time.monotonic())*1000))
                if current is not None:
                    try:
                        current.wait_for_timeout(wait_ms)
                    except PlaywrightError:
                        current = None  # Closed tab: wait for the same Notebook identity.
                else:
                    interruptible_sleep(wait_ms/1000)
    if owned_context is not None:
        current.bring_to_front()
    diagnostic('NOTEBOOK_UI_RESTORED')
    report_operation('NOTEBOOK_UI_RESTORED', human_modal_wait=False, message='Notebook画面を確認しました。')
    diagnostic('PIPELINE_RESUMED_AFTER_MODAL')
    report_operation('PIPELINE_RESUMED_AFTER_MODAL', human_modal_wait=False, message='Notebook処理を自動再開します。')
    return current
