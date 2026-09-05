from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from djd_maker.media.raw_store import RawSafeStore, RawStoreCollisionError
from djd_maker.media.validator import MediaValidationError, VideoValidator


def _write_fake_mp4(path: Path) -> None:
    path.write_bytes(b"not-real-but-probe-is-mocked")


def test_validator_parses_video_and_optional_audio(tmp_path, monkeypatch):
    video = tmp_path / "lesson.mp4"
    _write_fake_mp4(video)
    payload = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720},
            {"codec_type": "audio"},
        ],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""),
    )
    validator = VideoValidator(shutil.which("python"), stability_interval_seconds=0)
    result = validator.validate(video)
    assert result.size_bytes == video.stat().st_size
    assert result.metadata.duration_seconds == 12.5
    assert result.metadata.has_video and result.metadata.has_audio


def test_validator_rejects_temp_zero_and_corrupt(tmp_path, monkeypatch):
    validator = VideoValidator(shutil.which("python"), stability_interval_seconds=0)
    temporary = tmp_path / "lesson.mp4.crdownload"
    _write_fake_mp4(temporary)
    with pytest.raises(MediaValidationError, match="temporary"):
        validator.validate(temporary)
    empty = tmp_path / "empty.mp4"
    empty.touch()
    with pytest.raises(MediaValidationError, match="empty"):
        validator.validate(empty)

    corrupt = tmp_path / "corrupt.mp4"
    _write_fake_mp4(corrupt)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "invalid data"),
    )
    with pytest.raises(MediaValidationError, match="ffprobe rejected"):
        validator.validate(corrupt)


def test_raw_save_is_non_destructive_atomic_and_builds_full_gate(tmp_path, monkeypatch):
    source = tmp_path / "download.mp4"
    original = b"verified-video-content"
    source.write_bytes(original)
    payload = {
        "format": {"duration": "3.0"},
        "streams": [{"codec_type": "video", "width": 320, "height": 240}],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""),
    )
    validator = VideoValidator(shutil.which("python"), stability_interval_seconds=0)
    destination = tmp_path / "raw_files" / "lesson.mp4"
    result = RawSafeStore(validator).save(source, destination)

    assert source.read_bytes() == original
    assert destination.read_bytes() == original
    assert result.media.path == destination
    assert result.safety_gate.remote_deletion_allowed
    assert not list(destination.parent.glob("*.staging"))


def test_raw_collision_does_not_modify_either_file(tmp_path, monkeypatch):
    source = tmp_path / "download.mp4"
    destination = tmp_path / "raw" / "lesson.mp4"
    destination.parent.mkdir()
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    payload = {"format": {"duration": "1"}, "streams": [{"codec_type": "video"}]}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""),
    )
    validator = VideoValidator(shutil.which("python"), stability_interval_seconds=0)
    with pytest.raises(RawStoreCollisionError):
        RawSafeStore(validator).save(source, destination)
    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


def test_existing_identical_raw_can_be_recovered_without_rewrite(tmp_path, monkeypatch):
    source = tmp_path / "download.mp4"
    destination = tmp_path / "raw" / "lesson.mp4"
    destination.parent.mkdir()
    source.write_bytes(b"same-video")
    destination.write_bytes(b"same-video")
    payload = {"format": {"duration": "1"}, "streams": [{"codec_type": "video"}]}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""),
    )
    validator = VideoValidator(shutil.which("python"), stability_interval_seconds=0)
    before = destination.stat().st_mtime_ns
    result = RawSafeStore(validator).verify_existing(source, destination)
    assert result.safety_gate.remote_deletion_allowed
    assert destination.stat().st_mtime_ns == before
