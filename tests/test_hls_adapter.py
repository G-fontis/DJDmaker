from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from djd_maker.adapters.hls import (
    HlsAdapter,
    HlsAdapterError,
    HlsOutputCollisionError,
    create_and_validate_zip,
    validate_hls,
)


def _write_hls(directory: Path, names: tuple[str, ...] = ("segment00000.ts",)) -> None:
    directory.mkdir()
    body = "#EXTM3U\n#EXT-X-VERSION:3\n"
    for name in names:
        body += f"#EXTINF:6.0,\n{name}\n"
        (directory / name).write_bytes(b"segment-data")
    (directory / "playlist.m3u8").write_text(body + "#EXT-X-ENDLIST\n", encoding="utf-8")


def test_validate_hls_accepts_complete_flat_sequence(tmp_path: Path) -> None:
    hls = tmp_path / "hls"
    _write_hls(hls, ("segment00000.ts", "segment00001.ts"))
    playlist, segments = validate_hls(hls)
    assert playlist.name == "playlist.m3u8"
    assert [item.name for item in segments] == ["segment00000.ts", "segment00001.ts"]


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("missing_playlist", "playlist.m3u8"),
        ("missing_segment", "missing"),
        ("zero_segment", "0 byte"),
        ("gap", "non-contiguous"),
        ("no_endlist", "ENDLIST"),
        ("unsafe_path", "invalid"),
        ("unreferenced", "do not match"),
    ],
)
def test_validate_hls_rejects_invalid_outputs(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    hls = tmp_path / "hls"
    _write_hls(hls, ("segment00000.ts",))
    playlist = hls / "playlist.m3u8"
    if mutation == "missing_playlist":
        playlist.unlink()
    elif mutation == "missing_segment":
        (hls / "segment00000.ts").unlink()
    elif mutation == "zero_segment":
        (hls / "segment00000.ts").write_bytes(b"")
    elif mutation == "gap":
        (hls / "segment00000.ts").rename(hls / "segment00001.ts")
        playlist.write_text("#EXTM3U\nsegment00001.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
    elif mutation == "no_endlist":
        playlist.write_text("#EXTM3U\nsegment00000.ts\n", encoding="utf-8")
    elif mutation == "unsafe_path":
        playlist.write_text("#EXTM3U\n../segment00000.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
    elif mutation == "unreferenced":
        (hls / "segment00001.ts").write_bytes(b"extra")
    with pytest.raises(HlsAdapterError, match=expected):
        validate_hls(hls)


def test_zip_is_stored_flat_complete_and_crc_valid(tmp_path: Path) -> None:
    hls = tmp_path / "hls"
    _write_hls(hls, ("segment00000.ts", "segment00001.ts"))
    playlist, segments = validate_hls(hls)
    target = tmp_path / "temporary.zip"
    create_and_validate_zip(playlist, segments, target)
    with ZipFile(target) as archive:
        assert archive.namelist() == ["playlist.m3u8", "segment00000.ts", "segment00001.ts"]
        assert archive.testzip() is None
        assert all(info.compress_type == ZIP_STORED for info in archive.infolist())


def test_output_collision_is_rejected_before_tools_run(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are not installed")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not probed because collision is checked first")
    output = tmp_path / "result.zip"
    output.write_bytes(b"keep-me")
    adapter = HlsAdapter(ffmpeg, ffprobe)
    with pytest.raises(HlsOutputCollisionError):
        adapter.convert_validate_and_zip(source, output)
    assert output.read_bytes() == b"keep-me"


def test_real_ffmpeg_fixture_conversion(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are not installed")
    source = tmp_path / "fixture.mp4"
    fixture = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=7",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=7",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if fixture.returncode != 0:
        pytest.skip(f"local ffmpeg cannot create H.264 fixture: {fixture.stderr[-300:]}")

    output = tmp_path / "lesson.zip"
    result = HlsAdapter(ffmpeg, ffprobe).convert_validate_and_zip(source, output)
    assert result.zip_path == output
    assert result.playlist.is_file()
    assert result.segments
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert all(info.compress_type == ZIP_STORED for info in archive.infolist())


def test_ffmpeg_command_contract_with_fake_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.touch()
    ffprobe.touch()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "-show_streams" in command:
            payload = {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "7.0"},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        playlist = Path(command[-1])
        playlist.write_text(
            "#EXTM3U\n#EXTINF:6.0,\nsegment00000.ts\n#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        (playlist.parent / "segment00000.ts").write_bytes(b"segment")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("djd_maker.adapters.hls._run", fake_run)
    output = tmp_path / "result.zip"
    HlsAdapter(ffmpeg, ffprobe).convert_validate_and_zip(source, output)
    conversion = next(command for command in commands if "-hls_time" in command)
    joined = " ".join(conversion)
    assert "-c:v libx264" in joined
    assert "-c:a aac" in joined
    assert "-hls_time 6" in joined
    assert "segment%05d.ts" in joined
    assert conversion[-1].endswith("playlist.m3u8")


def test_zip_failure_does_not_publish_partial_output(tmp_path: Path, monkeypatch) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.touch()
    ffprobe.touch()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    def fake_run(command, _timeout):
        if "-show_streams" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264"},
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": "1"},
                    }
                ),
                "",
            )
        playlist = Path(command[-1])
        playlist.write_text("#EXTM3U\nsegment00000.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        (playlist.parent / "segment00000.ts").write_bytes(b"segment")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("djd_maker.adapters.hls._run", fake_run)
    monkeypatch.setattr(
        "djd_maker.adapters.hls.create_and_validate_zip",
        lambda *_args: (_ for _ in ()).throw(OSError("zip failed")),
    )
    output = tmp_path / "result.zip"
    with pytest.raises(OSError, match="zip failed"):
        HlsAdapter(ffmpeg, ffprobe).convert_validate_and_zip(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))
