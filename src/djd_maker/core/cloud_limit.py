"""Persistent cloud suspension; local work does not depend on this gate."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re

from .repositories import _VersionedDocument, SCHEMA_VERSION

LIMIT_RESUME_MARGIN_MINUTES = 5


@dataclass(frozen=True)
class LimitObservation:
    message: str
    blocked_until: datetime | None
    chat_disabled: bool = False


class CloudLimitReached(Exception):
    def __init__(self, observation):
        self.observation = observation
        super().__init__('CLOUD_BLOCKED_UNTIL: ' + observation.message)


def parse_limit_text(message: str, *, now: datetime, chat_disabled=False):
    if now.utcoffset() is None:
        raise ValueError('limit clock must be timezone-aware')
    compact = re.sub(r'\s+', '', message)
    strong = 'AIの使用量上限に達しました' in compact or ('チャットは' in compact and 'まで無効' in compact)
    secondary = '使用量上限' in compact and '利用可能' in compact and not is_limit_warning(message)
    english = message.lower().replace('’', "'")
    strong = strong or bool(re.search(
        r"(?:you(?:'ve| have) reached (?:your |the )?(?:ai )?(?:usage )?limit|"
        r'(?:ai )?usage limit (?:has been )?reached|chat (?:is |has been )?disabled)', english))
    # While producing a reply, Notebook makes the textarea readonly/disabled.
    # An approaching-limit banner does not turn that transient UI state into
    # exhaustion. Explicit reached/disabled notices above still take priority.
    secondary = secondary or ('usage limit' in english and not is_limit_warning(message))
    if not strong and not (secondary and chat_disabled):
        return None
    match = re.search(r'(?<!\d)(?P<period>午前|午後)?\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?!\d)', message)
    reset = None
    if match:
        hour, minute = int(match['h']), int(match['m'])
        if match['period']:
            if not 1 <= hour <= 12:
                return LimitObservation(message, None, chat_disabled)
            hour = hour % 12 + (12 if match['period'] == '午後' else 0)
        if hour < 24 and minute < 60:
            reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset < now:
                reset += timedelta(days=1)
    return LimitObservation(message, reset, chat_disabled)


def is_limit_warning(message: str) -> bool:
    """An approaching limit is not exhaustion while the chat remains enabled."""
    return bool(re.search(
        r'\b(?:almost at|near(?:ing)?|approaching|close to)\s+(?:(?:your|the)\s+)?(?:ai\s+)?(?:usage\s+)?limit\b'
        r'|上限\s*(?:が近|に近|に接近)', message, re.I))


class CloudLimitGate:
    def __init__(self, path: Path, *, clock=None):
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.document = _VersionedDocument(path, 'cloud_limit', use_file_lock=False,
                                          replace_retry_delays=(.1, .2, .4, .8))
        self.state = self.document.load(default={})

    @property
    def blocked(self):
        return bool(self.state.get('active'))

    @property
    def due(self):
        return self.blocked and self.recheck_delay == 0

    def _date(self, name):
        try:
            value = datetime.fromisoformat(self.state.get(name) or '')
            return value if value.utcoffset() is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def recheck_delay(self):
        now = self.clock()
        resume = self._date('cloud_resume_at')
        probe = self._date('next_recheck_at')
        # Retry backoff is bounded; corrupt/old future probes cannot suspend forever.
        if probe and probe > now + timedelta(seconds=120):
            probe = None
        deadlines = [value for value in (resume, probe) if value is not None]
        return max(0, (max(deadlines)-now).total_seconds()) if deadlines else 0

    def defer_recheck(self, seconds=120):
        self.state['next_recheck_at'] = (self.clock()+timedelta(seconds=seconds)).isoformat()
        self._save()

    def block(self, observation, *, notebook_url=None):
        reset = observation.blocked_until
        self.state = dict(active=True, message=observation.message,
                          notebook_url=notebook_url or self.state.get('notebook_url'),
                          cloud_blocked_until=reset.isoformat() if reset else None,
                          cloud_resume_at=(reset + timedelta(minutes=LIMIT_RESUME_MARGIN_MINUTES)).isoformat() if reset else None)
        self.state['observed_at'] = self.clock().isoformat()
        if reset is None:
            # A current quota reply remains effective until a later UI recheck.
            self.state['next_recheck_at'] = (self.clock()+timedelta(seconds=120)).isoformat()
        self._save()

    def release(self):
        self.state = dict(active=False)
        self._save()

    def _save(self):
        self.document.save(dict(schema_version=SCHEMA_VERSION, kind='cloud_limit', **self.state))

    def status(self):
        value = self._date('cloud_resume_at')
        remaining = max(0, int((value-self.clock()).total_seconds())) if value else None
        needs = self.blocked and (value is None or self.clock() >= value)
        return dict(self.state, remaining_seconds=remaining,
                    reconciliation='LIMIT_UNKNOWN_NEEDS_RECHECK' if needs else 'DEADLINE_WAIT' if self.blocked else 'AVAILABLE',
                    recheck_seconds=self.recheck_delay if self.blocked else 0)
