from __future__ import annotations

from dataclasses import dataclass


MIN_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_ATTEMPTS = 3
ACCEPTED_AFTER_3_SMALL_DOWNLOADS = 'ACCEPTED_AFTER_3_SMALL_DOWNLOADS'
PASSED_MIN_SIZE = 'PASSED_MIN_SIZE'


@dataclass(frozen=True, slots=True)
class DownloadSizeDecision:
    retry: bool
    accepted: bool
    status: str


def decide_download_size(size_bytes: int, attempt_count: int) -> DownloadSizeDecision:
    if size_bytes < 0:
        raise ValueError('size_bytes must not be negative')
    if not 1 <= attempt_count <= MAX_DOWNLOAD_ATTEMPTS:
        raise ValueError('attempt_count must be between 1 and 3')
    if size_bytes >= MIN_DOWNLOAD_BYTES:
        return DownloadSizeDecision(False, True, PASSED_MIN_SIZE)
    if attempt_count < MAX_DOWNLOAD_ATTEMPTS:
        return DownloadSizeDecision(True, False, 'RETRY_SMALL_DOWNLOAD')
    return DownloadSizeDecision(False, True, ACCEPTED_AFTER_3_SMALL_DOWNLOADS)
