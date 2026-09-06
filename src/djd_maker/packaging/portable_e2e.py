from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from djd_maker.adapters.ending import EndingEngineAdapter
from djd_maker.adapters.hls import HlsAdapter
from djd_maker.core.models import Job, JobState, preset_body_sha256
from djd_maker.core.repositories import JobRepository, PresetRepository
from djd_maker.media.raw_store import RawSafeStore
from djd_maker.media.validator import VideoValidator
from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
from djd_maker.testing.fake_notebook import FakeNotebookAdapter

from .preflight import application_root


def _make_fixture(ffmpeg: Path, target: Path, color: str, duration: float) -> None:
    completed = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-2000:])


def run_portable_fake_e2e(report_path: Path) -> int:
    root = application_root()
    runtime = root / "runtime" / "ffmpeg"
    ffmpeg = runtime / "ffmpeg.exe"
    ffprobe = runtime / "ffprobe.exe"
    fixture_root = root / "work" / "portable-fake-e2e-fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    raw_fixture = fixture_root / "授業 fixture.mp4"
    ending = fixture_root / "Ending fixture.mp4"
    script = fixture_root / "SD999_配布検証.txt"
    script.write_text("portable fake E2E", encoding="utf-8")
    _make_fixture(ffmpeg, raw_fixture, "blue", 3.0)
    _make_fixture(ffmpeg, ending, "red", 1.0)

    jobs = JobRepository(root / "system" / "jobs")
    job = Job(str(script), id="portable-fake-e2e")
    jobs.save(job)
    validator = VideoValidator(ffprobe)
    notebook = FakeNotebookAdapter({job.source_path: raw_fixture})
    preset_repository = PresetRepository(root / "system" / "presets.json")
    smoke_presets = preset_repository.list()
    if not smoke_presets:
        raise RuntimeError("packaging smoke preset is missing")
    preset_repository.select(smoke_presets[-1].id)
    selected_preset = preset_repository.require_selected()
    pipeline = PipelineCoordinator(
        jobs=jobs,
        notebook=notebook,
        raw_store=RawSafeStore(validator, root / "raw_files"),
        ending=EndingEngineAdapter(ffmpeg, ffprobe, validator=validator),
        hls=HlsAdapter(ffmpeg, ffprobe),
        validator=validator,
        paths=PipelinePaths(root / "raw_files", root / "output", root / "work", ending),
        ffmpeg_concurrency=1,
        generation_preset=selected_preset,
    )
    pipeline.run_cycle()
    result = jobs.require(job.id)
    passed = (
        result.state is JobState.COMPLETED
        and result.raw_path is not None
        and Path(result.raw_path).is_file()
        and result.zip_path is not None
        and Path(result.zip_path).is_file()
        and notebook.artifact_delete_calls == [job.id]
        and result.preset_id == selected_preset.id
        and result.preset_name == selected_preset.name
        and result.preset_body_snapshot == selected_preset.prompt_text
        and result.preset_body_sha256 == preset_body_sha256(selected_preset.prompt_text)
    )
    report_path.write_text(
        json.dumps(
            {
                "passed": passed,
                "state": result.state.value,
                "raw_path": result.raw_path,
                "zip_path": result.zip_path,
                "artifact_delete_calls": notebook.artifact_delete_calls,
                "preset_id": result.preset_id,
                "preset_name": result.preset_name,
                "preset_body_sha256": result.preset_body_sha256,
                "preset_body_length": len(result.preset_body_snapshot or ""),
                "selected_body_matches_snapshot": (
                    result.preset_body_snapshot == selected_preset.prompt_text
                    and result.preset_body_sha256
                    == preset_body_sha256(selected_preset.prompt_text)
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if passed else 6
