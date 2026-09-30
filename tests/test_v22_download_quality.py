from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from djd_maker.core.download_quality import (
    DownloadQualityProfile, MAX_DOWNLOAD_ATTEMPTS, MAX_DOWNLOAD_RETRIES,
    build_quality_profile, evaluate_download, mib_per_sec, quality_check_delay,
)
from djd_maker.core.models import Job, JobState
from test_pipeline import MemoryJobs, coordinator


class ProfileValidator:
    def __init__(self, durations=None, invalid=()):
        self.durations = durations or {}
        self.invalid = set(invalid)

    def validate(self, path):
        path = Path(path)
        if path.name in self.invalid:
            raise ValueError('ffprobe rejected')
        duration = self.durations.get(path.name, 100.0)
        if duration <= 0:
            raise ValueError('duration is not positive')
        return SimpleNamespace(
            size_bytes=path.stat().st_size,
            metadata=SimpleNamespace(duration_seconds=duration, has_video=True),
        )


def sparse(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.truncate(size)
    return path


def test_quality_profile_uses_only_files_ge_10mib(tmp_path):
    paths = [sparse(tmp_path/'a.mp4', 11*1024**2), sparse(tmp_path/'b.mp4', 12*1024**2),
             sparse(tmp_path/'small.mp4', 9*1024**2)]
    profile = build_quality_profile(paths, ProfileValidator(), generated_at='fixed')
    assert profile.sample_count == 2


def test_quality_profile_requires_ffprobe_valid(tmp_path):
    paths = [sparse(tmp_path/'a.mp4', 11*1024**2), sparse(tmp_path/'b.mp4', 12*1024**2),
             sparse(tmp_path/'bad.mp4', 13*1024**2)]
    profile = build_quality_profile(paths, ProfileValidator(invalid={'bad.mp4'}))
    assert profile.sample_count == 2


def test_quality_profile_requires_positive_duration(tmp_path):
    paths = [sparse(tmp_path/'a.mp4', 11*1024**2), sparse(tmp_path/'b.mp4', 12*1024**2),
             sparse(tmp_path/'zero.mp4', 13*1024**2)]
    profile = build_quality_profile(paths, ProfileValidator({'zero.mp4': 0}))
    assert profile.sample_count == 2


def test_mib_per_sec_calculation():
    assert mib_per_sec(8*1024**2, 4) == 2


def test_sample_mean_stddev_and_threshold_use_n_minus_1(tmp_path):
    paths = [sparse(tmp_path/'a.mp4', 10*1024**2), sparse(tmp_path/'b.mp4', 20*1024**2)]
    profile = build_quality_profile(paths, ProfileValidator({'a.mp4': 10, 'b.mp4': 10}))
    assert profile.mean_mib_per_sec == pytest.approx(1.5)
    assert profile.stddev_mib_per_sec == pytest.approx(2**-.5)
    assert profile.lower_3sigma_mib_per_sec == pytest.approx(1.5-3*(2**-.5))


def test_aggregate_profile_contains_no_media(tmp_path):
    paths = [sparse(tmp_path/'private-a.mp4', 10*1024**2), sparse(tmp_path/'private-b.mp4', 11*1024**2)]
    data = build_quality_profile(paths, ProfileValidator()).to_dict()
    assert not any('private' in str(value) or '.mp4' in str(value) for value in data.values())


@pytest.mark.parametrize('ratio,passed', [(0.09, True), (0.05, True), (0.049999, False)])
def test_statistical_threshold_boundary(tmp_path, ratio, passed):
    path = sparse(tmp_path/'x.mp4', int(ratio*100*1024**2))
    profile = DownloadQualityProfile(2, .1, 1/60, .05, 'fixed')
    result = evaluate_download(path, ProfileValidator({'x.mp4': 100}), profile)
    assert result.passed is passed


def test_over_10mib_can_still_fail_ratio(tmp_path):
    path = sparse(tmp_path/'long.mp4', 11*1024**2)
    profile = DownloadQualityProfile(2, .1, .01, .07, 'fixed')
    assert not evaluate_download(path, ProfileValidator({'long.mp4': 600}), profile).passed


def test_under_10mib_can_pass_ratio(tmp_path):
    path = sparse(tmp_path/'short.mp4', 8*1024**2)
    profile = DownloadQualityProfile(2, .1, .01, .07, 'fixed')
    assert evaluate_download(path, ProfileValidator({'short.mp4': 30}), profile).passed


@pytest.mark.parametrize('attempt,expected', [(1,1),(2,31),(3,61),(4,91),(5,121),(6,151)])
def test_attempt_delay_progression(attempt, expected):
    assert quality_check_delay(attempt, 1) == expected


def test_retry_constants_are_initial_plus_five():
    assert MAX_DOWNLOAD_RETRIES == 5
    assert MAX_DOWNLOAD_ATTEMPTS == 6


class SequenceDownload:
    def __init__(self, sizes):
        self.sizes = list(sizes)
        self.calls = 0

    def download_artifact(self, _job, destination):
        size = self.sizes[self.calls]
        self.calls += 1
        sparse(destination, size)
        return destination


class ErrorThenDownload(SequenceDownload):
    def __init__(self, failures, sizes):
        super().__init__(sizes)
        self.failures = failures
        self.successes = 0

    def download_artifact(self, job, destination):
        if self.failures:
            self.failures -= 1
            self.calls += 1
            raise RuntimeError('Chrome download timed out')
        size = self.sizes[self.successes]
        self.successes += 1
        self.calls += 1
        sparse(destination, size)
        return destination


def drive_quality(pipe, jobs, job, target, limit=20):
    result = None
    for _ in range(limit):
        result = pipe._download_with_size_gate(job, target)
        current = jobs.get(job.id)
        for field in job.__dataclass_fields__:
            setattr(job, field, getattr(current, field))
        if result is not None:
            return result
        if job.next_quality_check_at:
            job.next_quality_check_at = '2000-01-01T00:00:00+00:00'
            jobs.save(job)
    return result


def quality_pipe(tmp_path, sizes, durations, threshold=.05):
    job = Job(str(tmp_path/'source.txt'), state=JobState.DOWNLOADING)
    jobs = MemoryJobs(job)
    remote = SequenceDownload(sizes)
    pipe = coordinator(tmp_path, jobs, remote)
    pipe.download_quality_profile = DownloadQualityProfile(2,.1,.01,threshold,'fixed')
    pipe.quality_delay_baseline_seconds = 0
    pipe.validator = ProfileValidator(durations)
    return job, jobs, remote, pipe


def test_third_small_valid_ratio_can_complete(tmp_path):
    sizes = [2*1024**2, 3*1024**2, 8*1024**2]
    job,jobs,remote,pipe = quality_pipe(tmp_path,sizes,{'.source.attempt-1.mp4':100,'.source.attempt-2.mp4':100,'.source.attempt-3.mp4':30})
    target=tmp_path/'work'/job.id/'download'/'source.mp4'
    assert drive_quality(pipe,jobs,job,target) == target
    assert remote.calls == 3


def test_third_small_bad_ratio_continues_and_success_stops(tmp_path):
    sizes=[2*1024**2,3*1024**2,4*1024**2,12*1024**2]
    durations={f'.source.attempt-{n}.mp4':600 for n in range(1,4)}|{'.source.attempt-4.mp4':100}
    job,jobs,remote,pipe=quality_pipe(tmp_path,sizes,durations)
    target=tmp_path/'work'/job.id/'download'/'source.mp4'
    assert drive_quality(pipe,jobs,job,target)==target
    assert remote.calls==4 and job.download_retry_count==3


def test_six_bad_attempts_exhaust_and_no_seventh(tmp_path):
    sizes=[11*1024**2+i for i in range(6)]
    durations={f'.source.attempt-{n}.mp4':600 for n in range(1,7)}
    job,jobs,remote,pipe=quality_pipe(tmp_path,sizes,durations)
    target=tmp_path/'work'/job.id/'download'/'source.mp4'
    with pytest.raises(RuntimeError,match='DOWNLOAD_RETRY_EXHAUSTED'):
        drive_quality(pipe,jobs,job,target)
    assert remote.calls==6 and job.download_retry_count==5
    with pytest.raises(RuntimeError,match='DOWNLOAD_RETRY_EXHAUSTED'):
        pipe._download_with_size_gate(job,target)
    assert remote.calls==6


def test_retry_count_survives_roundtrip():
    job=Job('x.txt',download_attempt_count=6,download_retry_count=5,
            last_download_size=123,last_duration=45,last_mib_per_sec=.01,
            quality_threshold=.05,last_quality_result='DOWNLOAD_RETRY_EXHAUSTED',
            next_quality_check_at=datetime.now(UTC).isoformat())
    loaded=Job.from_dict(job.to_dict())
    assert loaded.download_retry_count==5 and loaded.last_mib_per_sec==.01


def test_crdownload_is_never_quality_checked(tmp_path):
    candidate=sparse(tmp_path/'x.mp4.crdownload', 1024)
    with pytest.raises(Exception):
        evaluate_download(candidate, __import__('djd_maker.media.validator',fromlist=['VideoValidator']).VideoValidator(stability_interval_seconds=0),
                          DownloadQualityProfile(2,.1,.01,.05,'fixed'))


def test_browser_incomplete_uses_bounded_download_budget_then_succeeds(tmp_path):
    job = Job(str(tmp_path/'source.txt'), state=JobState.DOWNLOADING)
    jobs = MemoryJobs(job)
    remote = ErrorThenDownload(2, [12*1024**2])
    pipe = coordinator(tmp_path, jobs, remote)
    pipe.download_quality_profile = DownloadQualityProfile(2,.1,.01,.05,'fixed')
    pipe.quality_delay_baseline_seconds = 0
    pipe.validator = ProfileValidator({'.source.attempt-3.mp4':100})
    target = tmp_path/'work'/job.id/'download'/'source.mp4'
    assert drive_quality(pipe, jobs, job, target) == target
    assert remote.calls == 3
    assert job.download_attempt_count == 3


def test_six_browser_incomplete_attempts_exhaust_without_seventh(tmp_path):
    job = Job(str(tmp_path/'source.txt'), state=JobState.DOWNLOADING)
    jobs = MemoryJobs(job)
    remote = ErrorThenDownload(7, [])
    pipe = coordinator(tmp_path, jobs, remote)
    pipe.download_quality_profile = DownloadQualityProfile(2,.1,.01,.05,'fixed')
    target = tmp_path/'work'/job.id/'download'/'source.mp4'
    with pytest.raises(RuntimeError, match='DOWNLOAD_RETRY_EXHAUSTED'):
        pipe._download_with_size_gate(job, target)
    assert remote.calls == 6
    assert job.download_attempt_count == 6
