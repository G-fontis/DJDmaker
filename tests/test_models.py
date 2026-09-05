from dataclasses import fields

import pytest

from djd_maker.core.interfaces import RemoteDeletionDenied, require_remote_deletion_gate
from djd_maker.core.models import (
    DownloadSafetyGate,
    InvalidStateTransition,
    Job,
    JobState,
)


def passing_gate() -> DownloadSafetyGate:
    return DownloadSafetyGate(**{item.name: True for item in fields(DownloadSafetyGate)})


def test_job_uses_txt_stem_as_script_name() -> None:
    job = Job(r"C:\scripts\SD001_仕事ができる人.txt")
    assert job.script_name == "SD001_仕事ができる人"


def test_happy_path_state_transitions() -> None:
    job = Job("SD001.txt")
    for state in (
        JobState.UPLOADING,
        JobState.GENERATING,
        JobState.WAITING_VIDEO,
        JobState.DOWNLOADING,
        JobState.RAW_READY,
        JobState.ENDING,
        JobState.HLS_ENCODING,
        JobState.ZIPPING,
        JobState.COMPLETED,
    ):
        job.transition_to(state)
    assert job.state is JobState.COMPLETED


def test_invalid_transition_fails_closed() -> None:
    job = Job("SD001.txt")
    with pytest.raises(InvalidStateTransition):
        job.transition_to(JobState.COMPLETED)
    assert job.state is JobState.WAITING


def test_job_json_round_trip_preserves_gate() -> None:
    original = Job("SD001.txt", safety_gate=passing_gate())
    restored = Job.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
    assert restored.safety_gate.remote_deletion_allowed


def test_remote_deletion_requires_every_check() -> None:
    gate = passing_gate()
    gate.raw_ffprobe_ok = False
    with pytest.raises(RemoteDeletionDenied, match="raw_ffprobe_ok"):
        require_remote_deletion_gate(gate)


def test_remote_deletion_accepts_complete_gate() -> None:
    require_remote_deletion_gate(passing_gate())

