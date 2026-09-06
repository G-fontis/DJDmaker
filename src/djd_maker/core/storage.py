from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4


RetryObserver = Callable[[str, int, int, Path, OSError], None]

logger = logging.getLogger(__name__)


def is_transient_replace_error(error: OSError) -> bool:
    """Return whether an atomic publish failed due to a short-lived Windows conflict."""

    return isinstance(error, PermissionError) or getattr(error, "winerror", None) in {5, 32}


class JsonStore:
    """UTF-8 JSONを同一directory内の一時ファイル経由で原子的に置換する。"""

    def __init__(
        self,
        path: str | Path,
        *,
        replace_retry_delays: tuple[float, ...] = (),
        retry_observer: RetryObserver | None = None,
    ) -> None:
        self.path = Path(path)
        self.replace_retry_delays = replace_retry_delays
        self.retry_observer = retry_observer

    def load(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        with self.path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        ready_to_publish = False
        try:
            payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            ready_to_publish = True
            self._replace(temporary, self.path, expected_content=payload.encode("utf-8"))
        finally:
            # A fully flushed temporary is crash/retry recovery evidence when
            # the final publish remains blocked. Partial JSON is never kept.
            if not ready_to_publish:
                temporary.unlink(missing_ok=True)

    def _replace(
        self,
        source: Path,
        destination: Path,
        *,
        expected_content: bytes | None = None,
    ) -> None:
        total_attempts = len(self.replace_retry_delays) + 1
        recovered = False
        if expected_content is None:
            expected_content = source.read_bytes()
        error: OSError | None = None
        for attempt, delay in enumerate((*self.replace_retry_delays, None), start=1):
            if recovered and not source.exists():
                assert error is not None
                if self._published_content_matches(source, destination, expected_content):
                    logger.info("JSON publish recovered successfully: %s", destination)
                    self._notify_retry(
                        "recovered", attempt, total_attempts, destination, error
                    )
                    return
                if delay is None:
                    raise error
                # Some Windows filter drivers complete ReplaceFile semantics,
                # remove the source, and still return ACCESS_DENIED while the
                # destination is temporarily unreadable. Recreate the already
                # fsynced payload at the same private temp path so the next
                # bounded attempt can publish it again instead of depending on
                # an ambiguous read of the destination.
                try:
                    self._restore_source(source, expected_content)
                except OSError as restore_error:
                    if not is_transient_replace_error(restore_error):
                        raise
                logger.warning(
                    "JSON publish completion verification is temporarily unavailable; "
                    "retry %d/%d: %s",
                    attempt,
                    total_attempts,
                    destination,
                )
                self._notify_retry(
                    "retry", attempt, total_attempts, destination, error
                )
                time.sleep(delay)
                continue
            try:
                os.replace(source, destination)
                if recovered:
                    logger.info("JSON publish recovered successfully: %s", destination)
                    assert error is not None
                    self._notify_retry("recovered", attempt, total_attempts, destination, error)
                return
            except OSError as caught:
                if (
                    recovered
                    and isinstance(caught, FileNotFoundError)
                    and not source.exists()
                ):
                    assert error is not None
                    if self._published_content_matches(source, destination, expected_content):
                        logger.info("JSON publish recovered successfully: %s", destination)
                        self._notify_retry(
                            "recovered", attempt, total_attempts, destination, error
                        )
                        return
                    if delay is None:
                        raise error
                    logger.warning(
                        "JSON publish completion verification is temporarily unavailable; "
                        "retry %d/%d: %s",
                        attempt,
                        total_attempts,
                        destination,
                    )
                    self._notify_retry(
                        "retry", attempt, total_attempts, destination, error
                    )
                    time.sleep(delay)
                    continue
                if not is_transient_replace_error(caught):
                    raise
                error = caught
                recovered = True
                logger.warning(
                    "JSON publish encountered a transient sharing/permission violation; "
                    "retry %d/%d: %s",
                    attempt,
                    total_attempts,
                    destination,
                )
                self._notify_retry(
                    "retry", attempt, total_attempts, destination, caught
                )
                # On Windows an atomic replace can be committed even though the
                # API reports a sharing/permission error. With the caller's
                # per-path lock held, a vanished source plus an existing target
                # is evidence that this exact publish completed. Retrying would
                # otherwise turn a successful commit into FileNotFoundError.
                if self._published_content_matches(source, destination, expected_content):
                    logger.info("JSON publish recovered successfully: %s", destination)
                    self._notify_retry(
                        "recovered", attempt, total_attempts, destination, caught
                    )
                    return
                if delay is None:
                    raise
                time.sleep(delay)

    @staticmethod
    def _published_content_matches(
        source: Path, destination: Path, expected_content: bytes
    ) -> bool:
        if source.exists():
            return False
        try:
            return destination.read_bytes() == expected_content
        except OSError:
            return False

    @staticmethod
    def _restore_source(source: Path, expected_content: bytes) -> None:
        try:
            with source.open("xb") as stream:
                stream.write(expected_content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass

    def _notify_retry(
        self,
        event: str,
        attempt: int,
        total_attempts: int,
        destination: Path,
        error: OSError,
    ) -> None:
        if self.retry_observer is not None:
            try:
                self.retry_observer(event, attempt, total_attempts, destination, error)
            except Exception:
                logger.exception("JSON retry observer failed: %s", destination)
