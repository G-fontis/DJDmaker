"""Append one fixed ending after cutting the main at last audio + padding."""

from __future__ import annotations

import re
from pathlib import Path
import os
import subprocess
from uuid import uuid4

from djd_maker.core.interfaces import MediaResult
from djd_maker.media.validator import (
    MediaValidationError,
    VideoMetadata,
    VideoValidator,
    resolve_executable,
)


class EndingProcessingError(RuntimeError):
    pass


class EndingOutputCollisionError(FileExistsError):
    pass


_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


class EndingEngineAdapter:
    """FFmpeg implementation of the Unit 1 single-ending policy."""

    def __init__(
        self,
        ffmpeg_path: str | Path | None = None,
        ffprobe_path: str | Path | None = None,
        *,
        validator: VideoValidator | None = None,
        timeout_seconds: float = 600.0,
        initial_tail_window_seconds: float = 30.0,
    ) -> None:
        self.ffmpeg_path = resolve_executable("ffmpeg", ffmpeg_path)
        self.validator = validator or VideoValidator(ffprobe_path)
        self.timeout_seconds = timeout_seconds
        self.initial_tail_window_seconds = initial_tail_window_seconds

    def _run(self, command: list[str], purpose: str) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command, shell=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EndingProcessingError(f"{purpose} could not run") from exc
        if completed.returncode != 0:
            raise EndingProcessingError(
                f"{purpose} failed: {completed.stderr.strip()}"
            )
        return completed

    def find_last_audio_end(self, video: Path, metadata: VideoMetadata) -> float:
        """Search backward from a 30-second suffix until audio is found."""
        if not metadata.has_audio:
            return metadata.duration_seconds
        total = metadata.duration_seconds
        window = min(self.initial_tail_window_seconds, total)
        while True:
            start = max(0.0, total - window)
            command = [
                str(self.ffmpeg_path), "-hide_banner", "-nostdin", "-nostats",
                "-ss", f"{start:.6f}", "-i", str(video), "-map", "0:a:0",
                "-af", "silencedetect=noise=-50dB:d=0.5", "-t", f"{window:.6f}",
                "-f", "null", "-",
            ]
            stderr = self._run(command, "audio-tail analysis").stderr
            starts = [float(match.group(1)) for match in _SILENCE_START.finditer(stderr)]
            last_start = starts[-1] if starts else None
            tail_after = stderr.rsplit("silence_start:", 1)[-1] if last_start is not None else ""
            ends_after = [float(match.group(1)) for match in _SILENCE_END.finditer(tail_after)]
            # Depending on the demuxer, ffmpeg either leaves EOF silence open or
            # emits silence_end at (within rounding tolerance of) window end.
            is_trailing = last_start is not None and (
                not ends_after or ends_after[-1] >= window - 0.1
            )
            if not is_trailing:
                return total
            if last_start <= 0.001 and start > 0:
                window = min(total, window * 2)
                continue
            if last_start <= 0.001 and start == 0:
                # Entire video silent: the established safe policy keeps all.
                return total
            return min(total, start + last_start)

    @staticmethod
    def _filter(main: VideoMetadata, ending: VideoMetadata, cut: float) -> str:
        width = main.width or ending.width or 1280
        height = main.height or ending.height or 720
        video_common = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
        main_audio = (
            f"[0:a:0]atrim=duration={cut:.6f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a0]"
            if main.has_audio else
            f"anullsrc=r=48000:cl=stereo,atrim=duration={cut:.6f},asetpts=PTS-STARTPTS[a0]"
        )
        end_audio = (
            f"[1:a:0]atrim=duration={ending.duration_seconds:.6f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a1]"
            if ending.has_audio else
            f"anullsrc=r=48000:cl=stereo,atrim=duration={ending.duration_seconds:.6f},asetpts=PTS-STARTPTS[a1]"
        )
        return ";".join((
            f"[0:v:0]trim=duration={cut:.6f},setpts=PTS-STARTPTS,{video_common}[v0]",
            main_audio,
            f"[1:v:0]trim=duration={ending.duration_seconds:.6f},setpts=PTS-STARTPTS,{video_common}[v1]",
            end_audio,
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
        ))

    def process(
        self,
        raw_video: Path,
        ending_video: Path,
        output_path: Path,
        padding_seconds: float = 0.5,
    ) -> MediaResult:
        if padding_seconds < 0:
            raise ValueError("padding_seconds must not be negative")
        raw_video, ending_video, output_path = map(Path, (raw_video, ending_video, output_path))
        if output_path.exists():
            raise EndingOutputCollisionError(f"output already exists: {output_path}")
        try:
            main = self.validator.validate(raw_video).metadata
            ending = self.validator.validate(ending_video).metadata
        except MediaValidationError as exc:
            raise EndingProcessingError(str(exc)) from exc
        try:
            last_audio_end = self.find_last_audio_end(raw_video, main)
        except EndingProcessingError:
            # The source implementation's conservative fallback is the entire
            # main video when audio-tail analysis itself is unavailable.
            last_audio_end = main.duration_seconds
        cut = min(main.duration_seconds, last_audio_end + padding_seconds)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staging = output_path.with_name(f".{output_path.name}.{uuid4().hex}.staging.mp4")
        command = [
            str(self.ffmpeg_path), "-hide_banner", "-nostdin", "-y",
            "-i", str(raw_video), "-i", str(ending_video),
            "-filter_complex", self._filter(main, ending, cut),
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
            "-preset", "medium", "-crf", "20", "-c:a", "aac",
            "-b:a", "192k", "-movflags", "+faststart", str(staging),
        ]
        try:
            self._run(command, "ending processing")
            staged = self.validator.validate(staging, reject_temporary=False)
            try:
                os.link(staging, output_path)
            except FileExistsError:
                raise EndingOutputCollisionError(
                    f"output already exists: {output_path}"
                ) from None
            staging.unlink()
            published = self.validator.validate(output_path)
            return MediaResult(
                path=output_path,
                duration_seconds=published.metadata.duration_seconds,
                size_bytes=published.size_bytes,
                last_audio_end_seconds=last_audio_end,
                cut_at_seconds=cut,
            )
        finally:
            staging.unlink(missing_ok=True)


EndingAdapter = EndingEngineAdapter
