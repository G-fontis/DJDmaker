"""Statistical quality gate for completed Notebook video downloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from statistics import mean, stdev
from typing import Iterable, Protocol


MIB = 1024 * 1024
MIN_SAMPLE_BYTES = 10 * MIB
BASE_QUALITY_DELAY_SECONDS = 1.0
MAX_DOWNLOAD_RETRIES = 5
MAX_DOWNLOAD_ATTEMPTS = 1 + MAX_DOWNLOAD_RETRIES


class Validator(Protocol):
    def validate(self, path: Path): ...


@dataclass(frozen=True, slots=True)
class DownloadQualityProfile:
    sample_count: int
    mean_mib_per_sec: float
    stddev_mib_per_sec: float
    lower_3sigma_mib_per_sec: float
    generated_at: str
    sample_min_mib_per_sec: float | None = None
    sample_max_mib_per_sec: float | None = None
    duration_min_seconds: float | None = None
    duration_max_seconds: float | None = None
    size_min_bytes: int | None = None
    size_max_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DownloadQualityResult:
    passed: bool
    result: str
    size_bytes: int
    duration_seconds: float
    mib_per_sec: float
    threshold: float


def load_quality_profile(path: Path) -> DownloadQualityProfile:
    """Load the single aggregate production source of truth."""
    value = json.loads(path.read_text(encoding='utf-8'))
    profile = DownloadQualityProfile(**value)
    if profile.sample_count < 2 or profile.lower_3sigma_mib_per_sec <= 0:
        raise ValueError('download quality profile is invalid')
    return profile


def mib_per_sec(size_bytes: int, duration_seconds: float) -> float:
    if size_bytes < 0 or duration_seconds <= 0:
        raise ValueError('size and duration must be positive')
    return size_bytes / MIB / duration_seconds


def quality_check_delay(attempt: int, baseline_seconds: float = BASE_QUALITY_DELAY_SECONDS) -> float:
    if not 1 <= attempt <= MAX_DOWNLOAD_ATTEMPTS:
        raise ValueError('attempt must be between 1 and 6')
    if baseline_seconds < 0:
        raise ValueError('baseline_seconds must not be negative')
    return baseline_seconds + 30.0 * (attempt - 1)


def build_quality_profile(paths: Iterable[Path], validator: Validator, *, generated_at: str | None = None) -> DownloadQualityProfile:
    rows: list[tuple[int, float, float]] = []
    for path in paths:
        try:
            if not path.is_file() or path.stat().st_size < MIN_SAMPLE_BYTES:
                continue
            checked = validator.validate(path)
            duration = float(checked.metadata.duration_seconds)
            size = int(checked.size_bytes)
            rows.append((size, duration, mib_per_sec(size, duration)))
        except Exception:
            continue
    if len(rows) < 2:
        raise ValueError('at least two valid >=10MiB samples are required')
    values = [row[2] for row in rows]
    average = mean(values)
    sigma = stdev(values)  # sample standard deviation (n - 1)
    return DownloadQualityProfile(
        sample_count=len(rows), mean_mib_per_sec=average,
        stddev_mib_per_sec=sigma, lower_3sigma_mib_per_sec=average - 3 * sigma,
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        sample_min_mib_per_sec=min(values), sample_max_mib_per_sec=max(values),
        duration_min_seconds=min(row[1] for row in rows),
        duration_max_seconds=max(row[1] for row in rows),
        size_min_bytes=min(row[0] for row in rows), size_max_bytes=max(row[0] for row in rows),
    )

def evaluate_download(path: Path, validator: Validator, profile: DownloadQualityProfile) -> DownloadQualityResult:
    checked = validator.validate(path)
    duration = float(checked.metadata.duration_seconds)
    ratio = mib_per_sec(int(checked.size_bytes), duration)
    passed = ratio >= profile.lower_3sigma_mib_per_sec
    return DownloadQualityResult(
        passed=passed,
        result='DOWNLOAD_QUALITY_PASS' if passed else 'DOWNLOAD_QUALITY_OUTLIER',
        size_bytes=int(checked.size_bytes), duration_seconds=duration,
        mib_per_sec=ratio, threshold=profile.lower_3sigma_mib_per_sec,
    )
