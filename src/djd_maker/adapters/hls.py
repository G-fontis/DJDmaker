from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from djd_maker.core.cancellation import run_process, checkpoint
from djd_maker.core.runtime_operation import report_operation
import tempfile
from hashlib import sha256
from djd_maker.core.deferred_state import SaveDeferred
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from djd_maker.core.interfaces import HlsResult


_SEGMENT_NAME = re.compile(r"segment(\d{5})\.ts")


def _source_digest(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            checkpoint('hls.hash')
            digest.update(block)
    return digest.hexdigest()


class HlsAdapterError(RuntimeError):
    """Raised when HLS conversion or its safety checks fail."""


class HlsOutputCollisionError(HlsAdapterError):
    """Raised when publishing would replace an existing output."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    duration_seconds: float
    video_codec: str
    audio_codec: str | None


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _resolve_tool(value: str | Path, name: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(str(value))
    if found:
        return Path(found).resolve()
    raise HlsAdapterError(f"{name} executable was not found: {value}")


def _run(command: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        return run_process(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=_creation_flags(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HlsAdapterError(f"command failed to run: {command[0]}: {exc}") from exc


def probe_media(ffprobe: str | Path, source: Path, timeout_seconds: float = 30) -> ProbeResult:
    result = _run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        timeout_seconds,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()[:1000]
        raise HlsAdapterError(f"ffprobe rejected {source}: {detail}")
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        videos = [item for item in streams if item.get("codec_type") == "video"]
        audios = [item for item in streams if item.get("codec_type") == "audio"]
        duration = float(
            data.get("format", {}).get("duration")
            or (videos[0].get("duration") if videos else 0)
            or 0
        )
        if not videos:
            raise ValueError("video stream is missing")
        if duration <= 0:
            raise ValueError("duration is not positive")
        return ProbeResult(
            duration_seconds=duration,
            video_codec=str(videos[0].get("codec_name") or ""),
            audio_codec=str(audios[0].get("codec_name") or "") if audios else None,
        )
    except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HlsAdapterError(f"invalid ffprobe output for {source}: {exc}") from exc


def validate_hls(hls_directory: Path) -> tuple[Path, tuple[Path, ...]]:
    """Validate a flat VOD HLS directory and return its ordered artifacts."""
    root = hls_directory.resolve()
    playlist = root / "playlist.m3u8"
    if not playlist.is_file() or playlist.is_symlink():
        raise HlsAdapterError("playlist.m3u8 is missing or is not a regular file")
    if playlist.stat().st_size == 0:
        raise HlsAdapterError("playlist.m3u8 is 0 byte")
    try:
        text = playlist.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise HlsAdapterError(f"playlist.m3u8 cannot be read: {exc}") from exc

    lines = [line.strip() for line in text.splitlines()]
    if "#EXT-X-ENDLIST" not in lines:
        raise HlsAdapterError("playlist.m3u8 has no #EXT-X-ENDLIST")
    references = [line for line in lines if line and not line.startswith("#")]
    if not references:
        raise HlsAdapterError("playlist.m3u8 contains no segment references")
    if len(references) != len(set(references)):
        raise HlsAdapterError("playlist.m3u8 contains duplicate segment references")

    segments: list[Path] = []
    for expected_number, reference in enumerate(references):
        relative = Path(reference)
        match = _SEGMENT_NAME.fullmatch(reference)
        if (
            not match
            or relative.is_absolute()
            or len(relative.parts) != 1
            or int(match.group(1)) != expected_number
        ):
            raise HlsAdapterError(f"invalid or non-contiguous segment reference: {reference}")
        segment = root / relative
        try:
            resolved_segment = segment.resolve(strict=True)
            resolved_segment.relative_to(root)
        except (OSError, ValueError) as exc:
            raise HlsAdapterError(f"segment is missing or escapes HLS directory: {reference}") from exc
        if not resolved_segment.is_file() or segment.is_symlink():
            raise HlsAdapterError(f"segment is not a regular file: {reference}")
        if resolved_segment.stat().st_size == 0:
            raise HlsAdapterError(f"segment is 0 byte: {reference}")
        segments.append(resolved_segment)

    actual_segments = {item.name for item in root.iterdir() if item.suffix.lower() == ".ts"}
    referenced_segments = {item.name for item in segments}
    if actual_segments != referenced_segments:
        raise HlsAdapterError("playlist references and segment files do not match")
    return playlist, tuple(segments)


def create_and_validate_zip(
    playlist: Path, segments: tuple[Path, ...], temporary_zip: Path
) -> None:
    expected_names = [playlist.name, *(segment.name for segment in segments)]
    with ZipFile(temporary_zip, "x", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.write(playlist, playlist.name)
        for segment in segments:
            checkpoint('zip.segment')
            archive.write(segment, segment.name)
    try:
        with ZipFile(temporary_zip, "r") as archive:
            infos = archive.infolist()
            if archive.testzip() is not None:
                raise HlsAdapterError("ZIP CRC integrity test failed")
            if archive.namelist() != expected_names:
                raise HlsAdapterError("ZIP entry set is invalid")
            if any(info.compress_type != ZIP_STORED for info in infos):
                raise HlsAdapterError("ZIP contains a compressed entry")
            if any(info.file_size <= 0 for info in infos):
                raise HlsAdapterError("ZIP contains a 0 byte entry")
            if any(Path(info.filename).is_absolute() or len(Path(info.filename).parts) != 1 for info in infos):
                raise HlsAdapterError("ZIP contains a non-flat or unsafe path")
    except BadZipFile as exc:
        raise HlsAdapterError(f"invalid ZIP archive: {exc}") from exc


def _publish_without_overwrite(temporary: Path, destination: Path) -> None:
    """Atomically publish through a hard link; link creation cannot overwrite."""
    checkpoint('zip.publish')
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise HlsOutputCollisionError(f"output already exists: {destination}") from exc
    except OSError as exc:
        raise HlsAdapterError(f"could not atomically publish {destination}: {exc}") from exc
    temporary.unlink()


class HlsAdapter:
    def __init__(
        self,
        ffmpeg: str | Path = "ffmpeg",
        ffprobe: str | Path = "ffprobe",
        *,
        conversion_timeout_seconds: float = 3600,
        probe_timeout_seconds: float = 30,
    ) -> None:
        self.ffmpeg = _resolve_tool(ffmpeg, "ffmpeg")
        self.ffprobe = _resolve_tool(ffprobe, "ffprobe")
        self.conversion_timeout_seconds = conversion_timeout_seconds
        self.probe_timeout_seconds = probe_timeout_seconds

    def convert_validate_and_zip(self, video: Path, output_zip: Path) -> HlsResult:
        video = video.resolve()
        output_zip = output_zip.resolve()
        if not video.is_file() or video.is_symlink() or video.stat().st_size == 0:
            raise HlsAdapterError(f"input video is missing, unsafe, or empty: {video}")
        if output_zip.exists():
            raise HlsOutputCollisionError(f"output already exists: {output_zip}")
        if output_zip.suffix.lower() != ".zip":
            raise HlsAdapterError("output path must have a .zip extension")

        input_probe = probe_media(self.ffprobe, video, self.probe_timeout_seconds)
        if input_probe.audio_codec is None:
            raise HlsAdapterError("input video has no audio stream; AAC output is required")

        output_zip.parent.mkdir(parents=True, exist_ok=True)
        hls_directory = Path(
            tempfile.mkdtemp(prefix=f".{output_zip.stem}.hls-", dir=output_zip.parent)
        )
        temporary_zip = output_zip.parent / f".{output_zip.name}.{os.urandom(8).hex()}.tmp"
        published = False
        preserve_checkpoint = False
        try:
            command = [
                str(self.ffmpeg),
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-f",
                "hls",
                "-hls_time",
                "6",
                "-hls_playlist_type",
                "vod",
                "-hls_segment_filename",
                str(hls_directory / "segment%05d.ts"),
                str(hls_directory / "playlist.m3u8"),
            ]
            report_operation('hls.start', next_action='HLS検証後ZIP作成')
            conversion = _run(command, self.conversion_timeout_seconds)
            if conversion.returncode != 0:
                raise HlsAdapterError(
                    f"ffmpeg HLS conversion failed ({conversion.returncode}): "
                    f"{conversion.stderr.strip()[-2000:]}"
                )
            playlist, segments = validate_hls(hls_directory)
            output_probe = probe_media(self.ffprobe, playlist, self.probe_timeout_seconds)
            if output_probe.video_codec != "h264" or output_probe.audio_codec != "aac":
                raise HlsAdapterError(
                    "HLS codecs are invalid: "
                    f"video={output_probe.video_codec!r}, audio={output_probe.audio_codec!r}"
                )
            report_operation('hls.complete', next_action='ZIP作成',
                hls_checkpoint_directory=str(hls_directory), hls_source_sha256=_source_digest(video))
            report_operation('zip.start', next_action='ZIP整合性検証・完成保存', zip_checkpoint_path=str(temporary_zip))
            create_and_validate_zip(playlist, segments, temporary_zip)
            _publish_without_overwrite(temporary_zip, output_zip)
            published = True
            report_operation('zip.complete', next_action='完成状態保存')
            return HlsResult(hls_directory, playlist, segments, output_zip)
        except SaveDeferred:
            preserve_checkpoint = True
            raise
        finally:
            if not preserve_checkpoint:
                temporary_zip.unlink(missing_ok=True)
            if not published and not preserve_checkpoint:
                shutil.rmtree(hls_directory, ignore_errors=True)

    def resume_validated(self, video: Path, output_zip: Path, directory: Path,
                         source_sha256: str, temporary_zip: Path | None = None) -> HlsResult:
        """Revalidate saved HLS; never rerun FFmpeg after a state-save failure."""
        directory, output_zip = directory.resolve(), output_zip.resolve()
        if directory.parent != output_zip.parent or not directory.name.startswith(f'.{output_zip.stem}.hls-'):
            raise HlsAdapterError('HLS_CHECKPOINT_PATH_MISMATCH')
        if temporary_zip and (temporary_zip.resolve().parent != output_zip.parent or not temporary_zip.name.startswith(f'.{output_zip.name}.')):
            raise HlsAdapterError('ZIP_CHECKPOINT_PATH_MISMATCH')
        if _source_digest(video) != source_sha256:
            raise HlsAdapterError('HLS_CHECKPOINT_SOURCE_CHANGED')
        playlist, segments = validate_hls(directory)
        probe = probe_media(self.ffprobe, playlist, self.probe_timeout_seconds)
        if probe.video_codec != 'h264' or probe.audio_codec != 'aac':
            raise HlsAdapterError('HLS_CHECKPOINT_CODECS_INVALID')
        temporary_zip = temporary_zip or output_zip.parent / f'.{output_zip.name}.{os.urandom(8).hex()}.tmp'
        if temporary_zip.is_file():
            with ZipFile(temporary_zip) as archive:
                paths = (playlist, *segments)
                if archive.testzip() is not None or archive.namelist() != [p.name for p in paths]:
                    raise HlsAdapterError('ZIP_CHECKPOINT_INVALID')
                if any(archive.getinfo(p.name).compress_type != ZIP_STORED or archive.read(p.name) != p.read_bytes() for p in paths):
                    raise HlsAdapterError('ZIP_CHECKPOINT_CONTENT_MISMATCH')
        else:
            report_operation('zip.start', zip_checkpoint_path=str(temporary_zip))
            create_and_validate_zip(playlist, segments, temporary_zip)
        _publish_without_overwrite(temporary_zip, output_zip)
        report_operation('zip.complete')
        return HlsResult(directory, playlist, segments, output_zip)


# Compatibility with the engine-oriented name used in design documents.
HlsEngineAdapter = HlsAdapter
