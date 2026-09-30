from djd_maker.adapters.notebook import NotebookDomAdapter


class Control:
    def __init__(self, page, *, found=False, enabled=True):
        self.page = page
        self.found = found
        self.enabled = enabled
        self.clicked = False
    @property
    def first(self):
        return self
    def count(self):
        return int(self.found)
    def nth(self, _index):
        return self
    def is_visible(self, **_kwargs):
        return self.found
    def is_enabled(self, **_kwargs):
        return self.enabled
    def click(self):
        assert self.found and self.enabled
        self.clicked = True
        self.page.url = 'https://notebook.google.com/notebook/live-created-id'


class CreatePage:
    def __init__(self, name='ノートブックを作成'):
        self.url = 'https://notebook.google.com/'
        self.name = name
        self.control = Control(self, found=True)
    def goto(self, url, **_kwargs):
        self.url = url
    def get_by_role(self, role, *, name, exact=True):
        return self.control if role == 'button' and name == self.name else Control(self)
    def get_by_label(self, *_args, **_kwargs):
        return Control(self)
    def get_by_text(self, *_args, **_kwargs):
        return Control(self)
    def locator(self, *_args, **_kwargs):
        return Control(self)
    def evaluate(self, _script):
        return {'text': '', 'disabled': False, 'enabled': True}
    def wait_for_url(self, pattern, timeout):
        assert pattern == '**/notebook/**'
        assert timeout > 0
    def wait_for_timeout(self, _milliseconds):
        pass


def test_current_japanese_create_control_is_shared_by_preflight_and_click():
    page = CreatePage()
    assert NotebookDomAdapter.preflight_home_page(page, timeout_ms=100)
    result = NotebookDomAdapter(page, timeout_ms=100).create_notebook()
    assert page.control.clicked
    assert result.notebook_id == 'live-created-id'
    assert result.notebook_url.endswith('/live-created-id')


def test_disabled_create_control_is_never_clicked():
    page = CreatePage()
    page.control.enabled = False
    assert not NotebookDomAdapter.preflight_home_page(page, timeout_ms=1)
    assert not page.control.clicked
