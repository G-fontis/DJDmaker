from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from djd_maker.core.interfaces import require_remote_deletion_gate
from djd_maker.core.models import DownloadSafetyGate, Job


@dataclass(slots=True)
class FakeNotebookAdapter:
    """実Geminiへ接続せず完成artifactを再現する。Notebook削除APIは持たない。"""

    fixture_by_source: dict[str, Path]
    status_by_job: dict[str, str] = field(default_factory=dict)
    submit_calls: list[str] = field(default_factory=list)
    submitted_prompts: list[str | None] = field(default_factory=list)
    download_calls: list[str] = field(default_factory=list)
    artifact_delete_calls: list[str] = field(default_factory=list)
    fail_delete: bool = False

    def submit(self, job: Job) -> tuple[str, str]:
        self.submit_calls.append(job.id)
        self.submitted_prompts.append(job.require_preset_body_snapshot())
        notebook_id = f"fake-{job.id}"
        self.status_by_job[job.id] = "READY"
        return notebook_id, f"https://notebook.google.com/notebook/{notebook_id}"

    def inspect_status(self, job: Job) -> str:
        return self.status_by_job.get(job.id, "NOT_STARTED")

    def download_artifact(self, job: Job, destination: Path) -> Path:
        source = self.fixture_by_source[job.source_path]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"download destination exists: {destination}")
        shutil.copyfile(source, destination)
        self.download_calls.append(job.id)
        return destination

    def delete_video_artifact(self, job: Job, gate: DownloadSafetyGate) -> None:
        require_remote_deletion_gate(gate)
        if self.fail_delete:
            raise RuntimeError("fake artifact deletion failure")
        self.artifact_delete_calls.append(job.id)
