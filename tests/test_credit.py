from datetime import datetime, timedelta, timezone

from djd_maker.adapters.credit import (
    CreditDetector,
    CreditState,
    parse_credit_percent,
    parse_credit_reset_at,
)


TOKYO = timezone(timedelta(hours=9), "JST")


class Items:
    def __init__(self, values=()):
        self.values = tuple(values)

    def count(self):
        return len(self.values)

    def nth(self, index):
        return Item(self.values[index])


class Item:
    def __init__(self, text):
        self.text = text

    def is_visible(self, **_kwargs):
        return True

    def inner_text(self):
        return self.text


class Page:
    def __init__(self, messages):
        self.messages = messages

    def locator(self, selector):
        return Items(self.messages if selector == "[role='alert']" else ())


def test_credit_exhausted_detects_only_status_container():
    now = datetime(2026, 9, 7, 10, 0, tzinfo=TOKYO)
    result = CreditDetector(
        Page(("クレジットを使い切りました。14:35 にクレジットリセット",)),
        clock=lambda: now,
    ).detect()
    assert result.state is CreditState.EXHAUSTED
    assert result.reset_at == datetime(2026, 9, 7, 14, 35, tzinfo=TOKYO)


def test_reset_same_day():
    now = datetime(2026, 9, 7, 10, 0, tzinfo=TOKYO)
    assert parse_credit_reset_at("14:30 にクレジットリセット", now=now) == (
        datetime(2026, 9, 7, 14, 30, tzinfo=TOKYO)
    )


def test_reset_next_day():
    now = datetime(2026, 9, 7, 22, 0, tzinfo=TOKYO)
    assert parse_credit_reset_at("01:00 にクレジットリセット", now=now) == (
        datetime(2026, 9, 8, 1, 0, tzinfo=TOKYO)
    )


def test_credit_percent_parse_and_unknown():
    assert parse_credit_percent("クレジット残量：72%") == 72
    assert parse_credit_percent("credit remaining: 19%") == 19
    assert parse_credit_percent("残量は表示されていません") is None


def test_credit_percent_not_required_for_exhaustion():
    result = CreditDetector(
        Page(("quota exceeded; credits reset at 01:00",)),
        clock=lambda: datetime(2026, 9, 7, 22, 0, tzinfo=TOKYO),
    ).detect()
    assert result.state is CreditState.EXHAUSTED
    assert result.percent is None


def test_credit_available_low_and_unknown():
    now = lambda: datetime(2026, 9, 7, 10, 0, tzinfo=TOKYO)
    assert CreditDetector(Page(("クレジット残量：72%",)), clock=now).detect().state is CreditState.AVAILABLE
    assert CreditDetector(Page(("クレジット残量：20%",)), clock=now).detect().state is CreditState.LOW
    assert CreditDetector(Page(()), clock=now).detect().state is CreditState.UNKNOWN


def test_unrelated_source_credit_text_is_not_matched():
    class BodyOnlyPage:
        def locator(self, _selector):
            return Items(())

    result = CreditDetector(
        BodyOnlyPage(), clock=lambda: datetime(2026, 9, 7, 10, 0, tzinfo=TOKYO)
    ).detect()
    assert result.state is CreditState.UNKNOWN
