"""Notebook Studio's observed video form; one durable Generate claim per attempt."""
from djd_maker.core.cancellation import checkpoint, active_monotonic
from djd_maker.core.generation_settings import STYLE_LABELS
from djd_maker.core.models import utc_now
from djd_maker.core.runtime_operation import report_operation


class ConfigurationUIFailed(RuntimeError):
    pass


class StudioOutcomePending(RuntimeError):
    pass


UI_STYLE_LABELS = {**STYLE_LABELS, 'paper_craft': 'ペーパー クラフト'}


def owned_video_dialog(dialog):
    """Transaction-local recognition, never a new globally auto-dismissed modal."""
    return (dialog.get_by_role('heading', name='動画解説をカスタマイズ', exact=True).count() == 1
            and dialog.get_by_role('combobox', name='言語を選択', exact=True).count() == 1
            and dialog.get_by_text('説明動画', exact=True).count() == 1
            and dialog.get_by_role('button', name='生成', exact=True).count() == 1)


def ensure_studio_interactable(dom, expected_url=None):
    from .notebook_modal import ensure_notebook_interactable
    from .cancellable_browser import unwrap
    from urllib.parse import urlsplit
    owner = getattr(dom.interactable_guard, '__self__', None)
    context = getattr(dom.page, 'context', None)
    if owner is not None and hasattr(owner, 'context') and unwrap(context) is not unwrap(owner.context):
        raise ConfigurationUIFailed('NOTEBOOK_IDENTITY_UNVERIFIED')
    def ready(page):
        dialogs = page.get_by_role('dialog')
        for i in range(dialogs.count()):
            item = dialogs.nth(i)
            if item.is_visible() and owned_video_dialog(item):
                item.get_by_text('説明動画', exact=True).click(trial=True, timeout=1000)
                return True
        studio = page.locator('studio-panel')
        if studio.count() == 1 and studio.is_visible():
            studio.get_by_role('button', name='動画解説', exact=True).click(trial=True, timeout=1000)
            return True
        return False
    current = urlsplit(dom.page.url)
    expected = urlsplit(expected_url) if expected_url else None
    mismatch = (current.scheme != 'https' or current.hostname != 'notebook.google.com'
                or (expected is not None and current.path != expected.path))
    if mismatch:
        if expected is None or expected.hostname != 'notebook.google.com':
            raise ConfigurationUIFailed('NOTEBOOK_IDENTITY_UNVERIFIED')
        from .notebook_modal import wait_for_human_modal
        selected = wait_for_human_modal(dom.page, diagnostic=dom.diagnostic, target=None,
            verify_ready=ready, owned_context=context, owned_dialog=owned_video_dialog,
            lifecycle_guard=getattr(owner,'lifecycle_guard',None),poll_seconds=2,
            expected_identity=(expected.scheme,expected.hostname,expected.path))
        dom.page = selected
    selected = ensure_notebook_interactable(dom.page, diagnostic=dom.diagnostic,
        owned_context=context, owned_dialog=owned_video_dialog,
        lifecycle_guard=getattr(owner, 'lifecycle_guard', None), verify_ready=ready)
    if selected is not None:
        dom.page = selected


class StudioGeneration:
    def __init__(self, dom, job, persist, *, timeout_seconds=30, poll_seconds=2):
        self.dom, self.job, self.persist = dom, job, persist
        self.timeout, self.poll = timeout_seconds, poll_seconds

    @property
    def page(self):
        return self.dom.page

    def stage(self, value):
        self.job.studio_generation_stage = value
        if self.persist:
            self.persist(self.job)
        report_operation(value, video_type='説明動画', language='日本語',
                         visual_style=UI_STYLE_LABELS[self.job.require_generation_settings().visual_style])

    def panel(self):
        dialogs = self.page.get_by_role('dialog')
        found = [dialogs.nth(i) for i in range(dialogs.count())
                 if dialogs.nth(i).is_visible() and owned_video_dialog(dialogs.nth(i))]
        return found[0] if len(found) == 1 else None

    def guard(self):
        checkpoint('studio.observe')
        # Use the same Ver2.4 human-review implementation while permitting only
        # this positively identified transaction's settings dialog.
        owner = getattr(self.dom.interactable_guard, '__self__', None)
        lifecycle = getattr(owner, 'lifecycle_guard', None)
        if lifecycle is not None and lifecycle.active_download_count:
            raise StudioOutcomePending('WAITING_DOWNLOAD: Download完了後にStudioを再確認します')
        ensure_studio_interactable(self.dom, self.job.notebook_url)

    def wait(self, predicate, description):
        deadline = active_monotonic() + self.timeout
        while active_monotonic() < deadline:
            self.guard()
            value = predicate()
            if value:
                return value
            self.page.wait_for_timeout(min(self.poll * 1000, max(1, (deadline-active_monotonic())*1000)))
        raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: ' + description)

    def radio(self, label):
        panel = self.panel()
        if panel is None:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 動画設定画面を確認できません')
        tiles = panel.locator('mat-radio-button').filter(has=self.page.get_by_text(label, exact=True))
        if tiles.count() != 1:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: ' + label + 'を一意に確認できません')
        radio = tiles.get_by_role('radio')
        if radio.count() != 1 or not radio.is_enabled():
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 選択肢が無効です')
        return tiles, radio

    def select_radio(self, label, stage):
        for _ in range(3):
            self.guard()
            try:
                tile, radio = self.radio(label)
            except ConfigurationUIFailed:
                self.page.wait_for_timeout(self.poll*1000)
                continue
            if radio.is_checked():
                self.stage(stage)
                return
            # Material radio's native input is covered by its own tile label.
            # Click only the exact visible label, then read the native checked state.
            tile.get_by_text(label, exact=True).click(timeout=2000)
            if radio.is_checked():
                self.stage(stage)
                return
            self.page.wait_for_timeout(self.poll*1000)
        raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 選択値が一致しません: ' + label)

    def sources_selected(self, filename):
        rows = self.page.locator('source-panel, sources-panel, .source-panel').locator('.single-source-container')
        row = rows.filter(has=self.page.get_by_role('button', name=filename, exact=True, include_hidden=True))
        if row.count() != 1:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 必要なSourceを一意に確認できません')
        checkbox = row.get_by_role('checkbox', include_hidden=True)
        if checkbox.count() != 1:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: Source選択を確認できません')
        if not checkbox.is_checked() and self.panel() is not None:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 設定画面open中のSourceが未選択です')
        if not checkbox.is_checked():
            checkbox.check(timeout=2000)
        if not checkbox.is_checked():
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: Sourceが未選択です')

    def verify(self, settings):
        self.guard()
        panel = self.panel()
        if panel is None:
            return False
        _, kind = self.radio('説明動画')
        _, style = self.radio(UI_STYLE_LABELS[settings.visual_style])
        language = panel.get_by_role('combobox', name='言語を選択', exact=True)
        switches = panel.get_by_role('switch')
        producer_off = switches.count() == 1 and switches.get_attribute('aria-checked') == 'false'
        return (kind.is_checked() and style.is_checked() and language.inner_text().strip() == '日本語'
                and producer_off)

    def run(self, *, retry_failed=False):
        from .notebook import GenerationOutcome, RemoteVideoStatus
        from .credit import CreditSnapshot
        job = self.job
        settings = job.require_generation_settings()
        if not callable(self.persist):
            raise ConfigurationUIFailed('GENERATION_PERSISTENCE_REQUIRED: 生成前checkpoint保存が必要です')
        self.persist(job)
        self.guard()
        self.dom.check_usage_limit()
        current = self.dom.inspect_status()
        if current in {RemoteVideoStatus.GENERATING, RemoteVideoStatus.READY}:
            return GenerationOutcome(current, False, CreditSnapshot())
        if job.studio_generate_claimed_at:
            raise StudioOutcomePending('GENERATION_STATE_UNCERTAIN: Generate操作済み。remote状態を再確認します')
        allowed = {RemoteVideoStatus.NOT_STARTED}
        if retry_failed:
            allowed.add(RemoteVideoStatus.FAILED)
        if current not in allowed:
            raise StudioOutcomePending('GENERATION_STATE_UNCERTAIN: 未生成を確定できません')
        from pathlib import Path
        if self.dom.source_state(Path(job.source_path).name, include_hidden=self.panel() is not None) != 'READY':
            raise ConfigurationUIFailed('SOURCE_NOT_READY: Studio生成前にSource READYが必要です')
        self.sources_selected(Path(job.source_path).name)
        if self.panel() is None:
            studio = self.page.locator('studio-panel')
            if studio.count() != 1 or not studio.is_visible():
                raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: Studioを一意に確認できません')
            self.stage('STUDIO_OPENING')
            video = studio.get_by_role('button', name='動画解説', exact=True)
            if video.count() != 1 or not video.is_enabled():
                raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: Studioの動画解説controlが無効です')
            video.click(timeout=3000)
        self.wait(self.panel, '動画設定画面の表示待ち')
        self.stage('VIDEO_SETTINGS_OPEN')
        self.select_radio('説明動画', 'VIDEO_TYPE_SELECTED')
        panel = self.panel()
        language = panel.get_by_role('combobox', name='言語を選択', exact=True)
        if language.inner_text().strip() != '日本語':
            language.click(timeout=2000)
            listbox = self.page.get_by_role('listbox')
            self.wait(lambda: listbox.count() == 1 and listbox.is_visible(), '言語選択listboxの表示待ち')
            option = listbox.get_by_role('option', name='日本語', exact=True)
            self.wait(lambda: option.count() == 1 and option.is_visible(), '日本語の選択肢の表示待ち')
            option.click(timeout=2000)
        self.wait(lambda: language.inner_text().strip() == '日本語', '日本語の選択確認')
        self.stage('LANGUAGE_SELECTED')
        self.select_radio(UI_STYLE_LABELS[settings.visual_style], 'STYLE_SELECTED')
        switches = self.panel().get_by_role('switch')
        if switches.count() != 1:
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: Producer Note設定を確認できません')
        if switches.get_attribute('aria-checked') == 'true':
            switches.click(timeout=2000)
        self.wait(lambda: self.verify(settings), '生成設定の最終確認が不一致です')
        self.stage('GENERATION_READY')
        self.dom.check_usage_limit()
        if self.dom.inspect_status() not in allowed:
            raise StudioOutcomePending('GENERATION_STATE_UNCERTAIN: 設定中にremote状態が変わりました')
        button = self.panel().get_by_role('button', name='生成', exact=True)
        if button.count() != 1 or not button.is_enabled():
            raise ConfigurationUIFailed('CONFIGURATION_UI_FAILED: 設定画面の生成buttonが無効です')
        button.click(trial=True, timeout=2000)
        checkpoint('studio.before_generate')
        job.studio_generate_claimed_at = utc_now()
        job.studio_generation_attempt += 1
        self.stage('GENERATE_CLICKED')  # Persist intent before the irreversible click.
        try:
            button.click(timeout=3000)
        except Exception:
            pass  # Unknown action outcome: observe, never repeat the click.
        deadline = active_monotonic() + self.timeout
        while active_monotonic() < deadline:
            self.guard()
            current = self.dom.inspect_status()
            if current in {RemoteVideoStatus.GENERATING, RemoteVideoStatus.READY}:
                self.stage('GENERATION_STARTED')
                return GenerationOutcome(current, False, CreditSnapshot())
            self.dom.check_usage_limit()
            self.page.wait_for_timeout(self.poll*1000)
        raise StudioOutcomePending('GENERATION_STATE_UNCERTAIN: Generate後の開始確認待ち。再clickしません')
