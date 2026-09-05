from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from djd_maker.adapters.ending import (
    EndingEngineAdapter,
    EndingOutputCollisionError,
    EndingProcessingError,
)
from djd_maker.media.validator import VideoMetadata


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _create_video(path: Path, *, duration: float, audio: bool, trailing_silence: float = 0) -> None:
    assert FFMPEG
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=30:d={duration}",
    ]
    if audio:
        audible = max(0.1, duration - trailing_silence)
        audio_filter = f"sine=frequency=880:sample_rate=48000:duration={audible}"
        if trailing_silence:
            audio_filter += f",apad=pad_dur={trailing_silence}"
        command += ["-f", "lavfi", "-i", audio_filter, "-shortest"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True)


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe unavailable")
def test_real_ffmpeg_trims_trailing_silence_and_appends_ending(tmp_path):
    main = tmp_path / "main.mp4"
    ending = tmp_path / "ending.mp4"
    output = tmp_path / "result.mp4"
    _create_video(main, duration=4, audio=True, trailing_silence=2)
    _create_video(ending, duration=1, audio=True)
    adapter = EndingEngineAdapter(FFMPEG, FFPROBE)

    result = adapter.process(main, ending, output)

    assert output.is_file() and output.stat().st_size > 0
    # 2 seconds audio + 0.5 padding + 1 second ending (encoder tolerance).
    assert 3.2 <= result.duration_seconds <= 3.9
    assert main.is_file() and main.stat().st_size > 0


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe unavailable")
def test_no_audio_keeps_full_main_duration(tmp_path):
    main = tmp_path / "silent-main.mp4"
    ending = tmp_path / "ending.mp4"
    output = tmp_path / "result.mp4"
    _create_video(main, duration=2, audio=False)
    _create_video(ending, duration=1, audio=True)
    result = EndingEngineAdapter(FFMPEG, FFPROBE).process(main, ending, output)
    assert 2.8 <= result.duration_seconds <= 3.3


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe unavailable")
def test_very_short_video_is_processed_safely(tmp_path):
    main = tmp_path / "very-short.mp4"
    ending = tmp_path / "ending.mp4"
    output = tmp_path / "result.mp4"
    _create_video(main, duration=0.2, audio=True)
    _create_video(ending, duration=0.2, audio=True)
    result = EndingEngineAdapter(FFMPEG, FFPROBE).process(main, ending, output)
    assert 0.25 <= result.duration_seconds <= 0.8


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe unavailable")
def test_missing_corrupt_and_collision_fail_safely(tmp_path):
    main = tmp_path / "main.mp4"
    ending = tmp_path / "ending.mp4"
    output = tmp_path / "result.mp4"
    _create_video(main, duration=1, audio=True)
    adapter = EndingEngineAdapter(FFMPEG, FFPROBE)
    with pytest.raises(EndingProcessingError):
        adapter.process(main, ending, output)
    ending.write_bytes(b"corrupt")
    with pytest.raises(EndingProcessingError):
        adapter.process(main, ending, output)
    output.write_bytes(b"existing")
    with pytest.raises(EndingOutputCollisionError):
        adapter.process(main, ending, output)
    assert output.read_bytes() == b"existing"


def test_backward_search_expands_an_all_silent_suffix(monkeypatch, tmp_path):
    adapter = object.__new__(EndingEngineAdapter)
    adapter.ffmpeg_path = Path(shutil.which("python") or "python")
    adapter.timeout_seconds = 1
    adapter.initial_tail_window_seconds = 30
    calls = []
    outputs = iter([
        "[silencedetect] silence_start: 0\n",
        "[silencedetect] silence_start: 0\n",
        "[silencedetect] silence_start: 50\n",
    ])

    def fake_run(command, purpose):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", next(outputs))

    monkeypatch.setattr(adapter, "_run", fake_run)
    metadata = VideoMetadata(100, 1, 1, 320, 240)
    assert adapter.find_last_audio_end(tmp_path / "x.mp4", metadata) == 50
    assert len(calls) == 3
