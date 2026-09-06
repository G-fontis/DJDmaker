from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re
from typing import Any, Callable, Iterable


class CreditState(StrEnum):
    AVAILABLE = "CREDIT_AVAILABLE"
    LOW = "CREDIT_LOW"
    EXHAUSTED = "CREDIT_EXHAUSTED"
    UNKNOWN = "CREDIT_UNKNOWN"


@dataclass(frozen=True, slots=True)
class CreditSnapshot:
    state: CreditState = CreditState.UNKNOWN
    percent: int | None = None
    reset_at: datetime | None = None
    message: str | None = None


CREDIT_CONTAINERS = (
    "[role='alert']",
    "[aria-live='assertive']",
    "[aria-live='polite']",
    "[data-testid*='error' i]",
    "[data-testid*='credit' i]",
    "[aria-label*='credit' i]",
    "[aria-label*='クレジット']",
    "mat-snack-bar-container",
    ".error-message",
)

CREDIT_EXHAUSTED_PHRASES = (
    "クレジット不足",
    "クレジットを使い切りました",
    "上限に達しました",
    "即時生成できません",
    "予約して生成してください",
    "credit exhausted",
    "credits exhausted",
    "usage limit",
    "quota exceeded",
    "you've reached your limit",
)

_RESET_PATTERNS = (
    re.compile(
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?:に)?\s*"
        r"(?:クレジット(?:が)?(?:リセット|回復|更新))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:credit|credits).*?(?:reset|renew).*?"
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        re.IGNORECASE,
    ),
)

_PERCENT_PATTERNS = (
    re.compile(r"クレジット(?:残量)?\s*[:：]?\s*(?P<percent>\d{1,3})\s*%"),
    re.compile(
        r"(?:credit|credits)(?:\s+remaining)?\s*[:：]?\s*"
        r"(?P<percent>\d{1,3})\s*%",
        re.IGNORECASE,
    ),
)


def parse_credit_reset_at(message: str, *, now: datetime) -> datetime | None:
    """Resolve a local HH:mm reset display to today or the following day."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("credit reset clock must be timezone-aware")
    match = next((item.search(message) for item in _RESET_PATTERNS if item.search(message)), None)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def parse_credit_percent(message: str) -> int | None:
    match = next((item.search(message) for item in _PERCENT_PATTERNS if item.search(message)), None)
    if match is None:
        return None
    value = int(match.group("percent"))
    return value if 0 <= value <= 100 else None


class CreditDetector:
    """Read only explicit credit/status surfaces, never uploaded source text."""

    def __init__(
        self,
        page: Any,
        *,
        clock: Callable[[], datetime],
        containers: Iterable[str] = CREDIT_CONTAINERS,
    ) -> None:
        self.page = page
        self.clock = clock
        self.containers = tuple(containers)

    def visible_messages(self) -> tuple[str, ...]:
        messages: list[str] = []
        for selector in self.containers:
            try:
                items = self.page.locator(selector)
                for index in range(min(items.count(), 20)):
                    item = items.nth(index)
                    if not item.is_visible(timeout=300):
                        continue
                    text = " ".join((item.inner_text() or "").split())
                    if text and text not in messages:
                        messages.append(text)
            except Exception:
                continue
        return tuple(messages)

    def detect(self) -> CreditSnapshot:
        messages = self.visible_messages()
        joined = "\n".join(messages)
        percent = parse_credit_percent(joined)
        exhausted_message = next(
            (
                message
                for message in messages
                if any(
                    phrase.casefold() in message.casefold()
                    for phrase in CREDIT_EXHAUSTED_PHRASES
                )
            ),
            None,
        )
        if exhausted_message is not None:
            return CreditSnapshot(
                CreditState.EXHAUSTED,
                percent,
                # Notebook may render the reset clock in a sibling live-status
                # element instead of the exhaustion sentence itself.
                parse_credit_reset_at(joined, now=self.clock()),
                exhausted_message,
            )
        if percent is None:
            return CreditSnapshot(message=messages[0] if messages else None)
        state = CreditState.LOW if percent <= 20 else CreditState.AVAILABLE
        return CreditSnapshot(state, percent, message=joined)
