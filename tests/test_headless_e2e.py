from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from djd_maker.adapters.ending import EndingEngineAdapter
from djd_maker.adapters.hls import HlsAdapter
from djd_maker.core.models import Job, JobState, Preset
from djd_maker.core.repositories import JobRepository
from djd_maker.media.raw_store import RawSafeStore
from djd_maker.media.validator import VideoValidator
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.testing.fake_notebook import FakeNotebookAdapter


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def _main_fixture(path: Path) -> None:
    _run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=if(lt(t\\,1)\\,0.25*sin(2*PI*440*t)\\,0):s=48000:d=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )


def _ending_fixture(path: Path) -> None:
    _run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000:duration=0.5",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe required")
def test_txt_to_fake_notebook_raw_ending_hls_zip(tmp_path: Path) -> None:
    script = tmp_path / "SD001_仕事とは.txt"
    script.write_text("授業台本", encoding="utf-8")
    fixture = tmp_path / "fake-notebook-video.mp4"
    ending_file = tmp_path / "ending.mp4"
    _main_fixture(fixture)
    _ending_fixture(ending_file)

    validator = VideoValidator(
        FFPROBE, stability_checks=2, stability_interval_seconds=0
    )
    jobs = JobRepository(tmp_path / "system" / "jobs")
    job = Job(str(script))
    jobs.save(job)
    notebook = FakeNotebookAdapter({job.source_path: fixture})
    pipeline = PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RawSafeStore(validator),
        ending=EndingEngineAdapter(FFMPEG, validator=validator),
        hls=HlsAdapter(FFMPEG, FFPROBE),
        validator=validator,
        paths=PipelinePaths(
            tmp_path / "raw_files",
            tmp_path / "output",
            tmp_path / "work",
            ending_file,
        ),
        ffmpeg_concurrency=1,
        generation_preset=Preset(
            "test-preset", "Test preset", "test body", "created", "updated"
        ),
    )

    pipeline.run_cycle()

    completed = jobs.require(job.id)
    assert completed.state is JobState.COMPLETED
    raw = Path(completed.raw_path)
    edited = Path(completed.edited_path)
    output_zip = Path(completed.zip_path)
    assert raw.name == "SD001_仕事とは.mp4"
    assert raw.read_bytes() == fixture.read_bytes()
    assert 1.8 <= validator.validate(edited).metadata.duration_seconds <= 2.2
    assert output_zip.name == "SD001_仕事とは.zip"
    with ZipFile(output_zip) as archive:
        assert archive.testzip() is None
        assert archive.getinfo("playlist.m3u8").compress_type == ZIP_STORED
        assert any(name.startswith("segment") and name.endswith(".ts") for name in archive.namelist())
    assert notebook.artifact_delete_calls == [job.id]
    assert not hasattr(notebook, "delete_notebook")
