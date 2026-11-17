"""Positive recognition and bounded dismissal of Notebook's home sort menu."""
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urljoin
import time
from playwright.sync_api import Error as PlaywrightError
from djd_maker.core.cancellation import checkpoint
from djd_maker.core.runtime_operation import report_operation


class OverlayKind(StrEnum):
    SORT_MENU = 'SORT_MENU'
    DROPDOWN = 'DROPDOWN'
    POPOVER = 'POPOVER'
    TOOLTIP = 'TOOLTIP'
    KNOWN_MODAL = 'KNOWN_MODAL'
    UNKNOWN_BLOCKING_MODAL = 'UNKNOWN_BLOCKING_MODAL'


def classify_overlay(element):
    from .notebook_modal import classify_modal, ModalKind
    role = element.get_attribute('role')
    if role == 'menu' and element.get_by_role('menuitemradio').count() == 2 and all(
            element.get_by_text(label, exact=True).count() == 1 for label in ('Most recent', 'Title')):
        return OverlayKind.SORT_MENU
    if role in {'menu', 'listbox'}:
        return OverlayKind.DROPDOWN
    if role == 'tooltip':
        return OverlayKind.TOOLTIP
    if element.get_attribute('popover') is not None:
        return OverlayKind.POPOVER
    return (OverlayKind.KNOWN_MODAL if classify_modal(element.inner_text()) is not ModalKind.UNKNOWN
            else OverlayKind.UNKNOWN_BLOCKING_MODAL)


@dataclass
class SortRecovery:
    state: str = 'ABSENT'
    target: object = None


def _home(page):
    url = urlsplit(page.url)
    return url.scheme == 'https' and url.hostname == 'notebook.google.com' and url.path in ('', '/')


def _visible(page, selector):
    items = page.locator(selector)
    return [items.nth(i) for i in range(items.count()) if items.nth(i).is_visible()]


def _recognize(page):
    if not _home(page):
        return None
    # Auth, consent, quota and every unrelated dialog take precedence.
    if _visible(page, '[role="dialog"], [aria-modal="true"], dialog[open]'):
        return None
    menus = page.get_by_role('menu')
    shown = [menus.nth(i) for i in range(menus.count()) if menus.nth(i).is_visible()]
    trigger = page.get_by_role('button', name='Sort notebooks', exact=True)
    if len(shown) != 1 or trigger.count() != 1 or not trigger.is_visible() or not trigger.is_enabled():
        return None
    menu = shown[0]
    items = menu.get_by_role('menuitemradio')
    if items.count() != 2 or any(not items.nth(i).is_visible() for i in range(2)):
        return None
    if any(menu.get_by_text(label, exact=True).count() != 1 for label in ('Most recent', 'Title')):
        return None
    controls = trigger.get_attribute('aria-controls')
    if controls and controls != menu.get_attribute('id'):
        return None
    backdrops = _visible(page, '.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')
    if len(backdrops) > 1 or _visible(page, '[data-testid="modal-scrim"], [data-testid="blocking-overlay"], .modal-backdrop.show'):
        return None
    backdrop = backdrops[0] if backdrops else None
    if backdrop is not None:
        # CDK's backdrop immediately precedes its overlay host. Do not consume
        # another modal's backdrop based on a shared CSS class alone.
        associated = menu.evaluate('''m=>{const root=m.closest('.cdk-overlay-container');
            if(!root)return false;const bs=[...root.children].filter(e=>
            e.classList.contains('cdk-overlay-backdrop')&&e.getClientRects().length);
            return bs.length===1&&bs[0].classList.contains('cdk-overlay-transparent-backdrop')
                &&!!bs[0].nextElementSibling?.contains(m)}''')
        if not associated:
            return None
    return menu, backdrop


def _target(page, supplied):
    if supplied is not None:
        return supplied
    links = page.locator('a[href*="/notebook/"]')
    for i in range(links.count()):
        item = links.nth(i)
        url = urlsplit(urljoin(page.url, item.get_attribute('href') or ''))
        if url.scheme == 'https' and url.hostname == 'notebook.google.com' and url.path.startswith('/notebook/'):
            if item.is_visible() and item.is_enabled():
                return item
    return None


def recover_sort_menu(page, *, target=None, lifecycle_guard=None, diagnostic=lambda _: None):
    try:
        found = _recognize(page)
    except (PlaywrightError, AttributeError, TypeError):
        return SortRecovery()
    if found is None:
        return SortRecovery()
    menu_locator, backdrop_locator = found
    menu = menu_locator.element_handle()
    backdrop = backdrop_locator.element_handle() if backdrop_locator is not None else None
    if menu is None:
        return SortRecovery('NEEDS_REVIEW', target)
    original_id = menu.get_attribute('id')
    chosen = _target(page, target)
    diagnostic('UI_OVERLAY_DETECTED:SORT_MENU:role=menu:items=Most recent,Title')
    report_operation('UI_OVERLAY_DETECTED', decision='SORT_MENU', human_modal_wait=False)
    report_operation('NON_BLOCKING_SORT_MENU', human_modal_wait=False)
    if lifecycle_guard is not None and lifecycle_guard.active_download_count:
        return SortRecovery('DEFERRED', chosen)
    for attempt in range(2):
        checkpoint('sort_menu.dismiss')
        try:
            # Revalidate before each Escape; a replacement or concurrent modal
            # must never receive a key intended for the known sort menu.
            current = _recognize(page)
            if current is None:
                break
            current_menu, _ = current
            if current_menu.get_attribute('id') != original_id:
                break
            menu.press('Escape', timeout=1000)
        except PlaywrightError:
            pass  # A detach during normal dismissal is resolved by observation.
        deadline = time.monotonic()+1.5
        while time.monotonic() < deadline:
            checkpoint('sort_menu.observe')
            try:
                hidden = not menu.is_visible() and (backdrop is None or not backdrop.is_visible())
                if hidden and _home(page) and not _visible(page,
                        '[role="dialog"], [aria-modal="true"], dialog[open], .cdk-overlay-backdrop-showing, [data-testid="blocking-overlay"], [data-testid="modal-scrim"]'):
                    if chosen is not None and chosen.is_visible() and chosen.is_enabled():
                        chosen.click(trial=True, timeout=500)
                        diagnostic('SORT_MENU_DISMISSED:menu_hidden:backdrop_gone:target_actionable')
                        report_operation('NOTEBOOK_TARGET_ACTIONABLE', human_modal_wait=False)
                        report_operation('SORT_MENU_DISMISSED', human_modal_wait=False)
                        return SortRecovery('RECOVERED', chosen)
            except PlaywrightError:
                pass
            page.wait_for_timeout(50)
    return SortRecovery('NEEDS_REVIEW', chosen)
