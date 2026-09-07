"""Owner-thread Playwright boundary with bounded calls and interruptible polling."""
import time
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from djd_maker.core.cancellation import current_token, checkpoint


def wrap(value):
    if isinstance(value, CancellableBrowserObject):
        return value
    if (type(value).__module__.startswith('playwright.sync_api')
        or (type(value).__module__ == 'playwright._impl._sync_base'
            and type(value).__name__ in {'EventContextManager', 'EventInfo'})):
        return CancellableBrowserObject(value)
    if isinstance(value, list):
        return [wrap(item) for item in value]
    return value


def unwrap(value):
    return value._wrapped if isinstance(value, CancellableBrowserObject) else value


class CancellableBrowserObject:
    def __init__(self, value):
        self._wrapped = value

    def __getattr__(self, name):
        checkpoint(f'browser.{name}')
        try:
            value = getattr(self._wrapped, name)
        finally:
            checkpoint()
        if not callable(value):
            return wrap(value)
        def call(*args, **kwargs):
            checkpoint(f'browser.{name}')
            token = current_token()
            if name == 'wait_for_timeout' and token:
                deadline = time.monotonic()+args[0]/1000
                while time.monotonic() < deadline:
                    token.check('browser.poll_wait')
                    self._wrapped.wait_for_timeout(min(100, max(0,(deadline-time.monotonic())*1000)))
                token.check()
                return None
            if name == 'goto' and token:
                token.navigation()
            # Retry only observation waits. Never repeat clicks, uploads or
            # navigation: those may already have produced a remote side effect.
            if token and name in {'wait_for', 'wait_for_selector', 'wait_for_function', 'wait_for_url', 'wait_for_load_state'}:
                total_ms = kwargs.get('timeout')
                deadline = time.monotonic() + (total_ms or 30000) / 1000
                while True:
                    token.check(f'browser.{name}')
                    remaining_ms = max(1, (deadline - time.monotonic()) * 1000)
                    try:
                        result = value(*args, **{**kwargs, 'timeout': min(2000, remaining_ms)})
                        token.check()
                        return wrap(result)
                    except PlaywrightTimeoutError:
                        token.check()
                        if time.monotonic() >= deadline:
                            raise
            # Preserve action deadlines: shortening a click/goto can report a
            # failure after the remote action succeeded. On Windows the owned
            # process boundary interrupts a stuck action on the stop deadline.
            args = tuple(unwrap(arg) for arg in args)
            kwargs = {key:unwrap(arg) for key,arg in kwargs.items()}
            try:
                result = value(*args, **kwargs)
            finally:
                checkpoint()
            return wrap(result)
        return call

    def __enter__(self):
        checkpoint()
        return wrap(self._wrapped.__enter__())

    def __exit__(self, *args):
        try:
            return self._wrapped.__exit__(*args)
        finally:
            checkpoint()
