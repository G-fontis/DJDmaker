"""Classify only a reply correlated to the current submitted user message."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re
import unicodedata

from .credit import parse_credit_reset_at


class ReplyKind(StrEnum):
    GENERATION_ACCEPTED = "GENERATION_ACCEPTED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    OTHER_FAILURE = "OTHER_FAILURE"
    NO_RESPONSE = "NO_RESPONSE"


@dataclass(frozen=True)
class ReplyResult:
    kind: ReplyKind
    score: float = 0
    matched_terms: tuple[str, ...] = ()
    reset_at: datetime | None = None
    reset_conflict: bool = False


def resolve_reset(text: str, now: datetime, explicit: datetime | None = None):
    if now.utcoffset() is None or (explicit is not None and explicit.utcoffset() is None):
        raise ValueError("reset time must be timezone-aware")
    stamp = re.search(r"\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}(?::\d{2})?(?:[zZ]|[+-]\d{2}:\d{2})", text)
    if explicit is None:
        if stamp and re.search(r"reset|リセット|回復|解除", text):
            try:
                explicit = datetime.fromisoformat(stamp[0].upper().replace("Z", "+00:00"))
            except ValueError:
                pass
    # An ISO timestamp's offset must not become a separate local HH:mm clock.
    clock_text = text[:stamp.start()] + text[stamp.end():] if stamp else text
    clock = parse_credit_reset_at(clock_text, now=now)
    if clock is None and re.search(r"reset|リセット|回復|解除|リフレッシュ", clock_text):
        hhmm = re.search(r"(?<![\d:Tt-])(\d{1,2}):(\d{2})(?![\d:])", clock_text)
        if hhmm and int(hhmm[1]) < 24 and int(hhmm[2]) < 60:
            clock = now.replace(hour=int(hhmm[1]), minute=int(hhmm[2]), second=0, microsecond=0)
            if clock <= now:
                clock += timedelta(days=1)
    relative = re.search(r"(?:約\s*)?(?:(\d+)\s*時間)?\s*(?:(\d+)\s*分)?\s*後", text)
    delta = None
    if relative and any(relative.groups()):
        delta = now + timedelta(hours=int(relative[1] or 0), minutes=int(relative[2] or 0))
    candidates = [value for value in (explicit, clock, delta) if value is not None]
    conflict = bool(candidates and any(abs((value - candidates[0]).total_seconds()) > 300 for value in candidates[1:]))
    return (candidates[0] if candidates else None), conflict


def classify_reply(text: str, *, now: datetime, explicit_reset: datetime | None = None) -> ReplyResult:
    text = unicodedata.normalize("NFKC", text).casefold()
    if not text.strip():
        return ReplyResult(ReplyKind.NO_RESPONSE)
    video = bool(re.search(r"動画|video", text))
    generation = bool(re.search(r"生成|作成|generation|generat|creat", text))
    negated = bool(re.search(r"(?:クォータ(?:不足|制限)?|利用制限|上限).{0,8}(?:ではありません|ではない|ありません)", text))
    weighted = {"クォータ": 3, "クォータ不足": 5, "クォータ制限": 5, "利用制限": 3, "制限に達": 4, "上限": 3, "quota": 3, "usage limit": 3, "利用いただけない": 2, "利用できない": 2, "回復": 1, "解除": 1, "リフレッシュ": 1}
    matched = tuple(term for term in weighted if term in text)
    score = sum(weighted[term] for term in matched)
    limit = any(term in text for term in ("クォータ", "利用制限", "制限に達", "上限", "quota", "usage limit"))
    failure_match = re.search(r"できません|できない|いただけない|利用不可|unavailable|cannot|exhausted|不足|制限のため", text)
    failed = failure_match is not None
    if failure_match and generation:
        score += 2
        matched += (failure_match[0],)
    relative_signal = re.search(r"(?:\d+\s*時間(?:\s*\d+\s*分)?|\d+\s*分)\s*後", text)
    if relative_signal:
        score += 2
        matched += (relative_signal[0],)
    reset, conflict = resolve_reset(text, now, explicit_reset)
    if video and generation and limit and failed and not negated and score >= 5:
        return ReplyResult(ReplyKind.QUOTA_EXHAUSTED, score, matched, reset, conflict)
    if video and generation and re.search(r"開始しました|(?:started|starting).*(?:generat|creat)|(?:generat|creat).*started", text) and not failed:
        return ReplyResult(ReplyKind.GENERATION_ACCEPTED)
    return ReplyResult(ReplyKind.OTHER_FAILURE, score, matched)
