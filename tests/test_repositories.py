import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from djd_maker.core.models import InvalidStateTransition, Job, JobState, Preset
from djd_maker.core.repositories import (
    JobRepository,
    MalformedJsonError,
    QueueRepository,
    RuntimeState,
    SCHEMA_VERSION,
    SettingsRepository,
    StateRepository,
    UnsupportedSchemaError,
)
from djd_maker.core.settings import AppSettings


def test_settings_round_trip_has_versioned_envelope(tmp_path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    settings = AppSettings(ending_video="ending.mp4", ffmpeg_concurrency=2)
    repository.save(settings)

    assert repository.load() == settings
    payload = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["kind"] == "settings"


def test_missing_settings_returns_valid_defaults_without_creating_file(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = SettingsRepository(path).load()
    assert settings.audio_tail_padding_seconds == 0.5
    assert not path.exists()


def test_job_save_load_list_and_atomic_transition(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    first = Job("input/SD001.txt", id="one")
    second = Job("input/SD002.txt", id="two")
    repository.save(second)
    repository.save(first)

    assert repository.get("missing") is None
    assert repository.require("one").source_path == "input/SD001.txt"
    assert [job.id for job in repository.list()] == ["one", "two"]
    assert repository.transition("one", JobState.UPLOADING).state == JobState.UPLOADING
    with pytest.raises(InvalidStateTransition):
        repository.transition("one", JobState.COMPLETED)


def test_job_id_cannot_escape_jobs_directory(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    with pytest.raises(ValueError):
        repository.get("../state")


def test_previous_valid_document_is_kept_as_backup_and_recovers_malformed_primary(tmp_path) -> None:
    path = tmp_path / "queue.json"
    repository = QueueRepository(path)
    repository.save(["old"])
    repository.save(["new"])
    path.write_text('{"broken":', encoding="utf-8")

    assert repository.load() == ["old"]
    assert json.loads(path.read_text(encoding="utf-8"))["job_ids"] == ["old"]
    assert list(tmp_path.glob("queue.json.corrupt-*"))


def test_valid_interrupted_temporary_is_preferred_for_crash_recovery(tmp_path) -> None:
    path = tmp_path / "queue.json"
    repository = QueueRepository(path)
    repository.save(["backup-value"])
    temp = tmp_path / ".queue.json.crashed.tmp"
    temp.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "kind": "queue", "job_ids": ["latest"]}
        ),
        encoding="utf-8",
    )
    path.write_text("not json", encoding="utf-8")

    assert repository.load() == ["latest"]
    assert not temp.exists()


def test_unrecoverable_malformed_json_is_reported_and_preserved(tmp_path) -> None:
    path = tmp_path / "queue.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(MalformedJsonError):
        QueueRepository(path).load()
    assert path.read_text(encoding="utf-8") == "not-json"


def test_one_unrecoverable_job_json_does_not_hide_other_jobs(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(Job("input/good.txt", id="good"))
    bad = repository.directory / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    jobs, errors = repository.list_with_errors()
    assert [job.id for job in jobs] == ["good"]
    assert "bad" in errors
    assert bad.read_text(encoding="utf-8") == "{broken"


def test_unsupported_schema_is_not_silently_downgraded(tmp_path) -> None:
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps({"schema_version": 999, "kind": "queue", "job_ids": []}),
        encoding="utf-8",
    )
    with pytest.raises(UnsupportedSchemaError):
        QueueRepository(path).load()


def test_newer_primary_schema_is_not_replaced_by_an_old_backup(tmp_path) -> None:
    path = tmp_path / "queue.json"
    repository = QueueRepository(path)
    repository.save(["old"])
    repository.save(["current"])
    path.write_text(
        json.dumps({"schema_version": 999, "kind": "queue", "job_ids": ["future"]}),
        encoding="utf-8",
    )
    with pytest.raises(UnsupportedSchemaError):
        repository.load()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 999


def test_queue_concurrentish_updates_do_not_lose_entries(tmp_path) -> None:
    path = tmp_path / "queue.json"

    def enqueue(number: int) -> None:
        QueueRepository(path).enqueue(f"job-{number}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(enqueue, range(40)))
    assert set(QueueRepository(path).load()) == {f"job-{number}" for number in range(40)}


@pytest.mark.parametrize(
    ("interrupted", "checkpoint"),
    [
        (JobState.UPLOADING, JobState.FAILED),
        (JobState.DOWNLOADING, JobState.WAITING_VIDEO),
        (JobState.ENDING, JobState.RAW_READY),
        (JobState.HLS_ENCODING, JobState.ENDING),
        (JobState.ZIPPING, JobState.HLS_ENCODING),
    ],
)
def test_restart_recovery_returns_interrupted_stage_to_checkpoint(
    tmp_path, interrupted, checkpoint
) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(Job("input/a.txt", id="job", state=interrupted))
    recovered = repository.recover_interrupted()
    assert [job.state for job in recovered] == [checkpoint]
    assert repository.require("job").state == checkpoint


def test_generating_resumes_monitoring_when_remote_identity_is_persisted(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(
        Job(
            "input/a.txt",
            id="job",
            state=JobState.GENERATING,
            notebook_id="remote-id",
            notebook_url="https://notebook.google.com/notebook/remote-id",
        )
    )
    assert repository.recover_interrupted()[0].state is JobState.WAITING_VIDEO


def test_restart_preserves_exact_preset_body_snapshot_and_hash(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    job = Job(
        "input/a.txt",
        id="job",
        state=JobState.WAITING_VIDEO,
        notebook_id="remote-id",
        notebook_url="https://notebook.google.com/notebook/remote-id",
    )
    job.snapshot_preset(
        Preset("preset-id", "Preset name", "exact body", "created", "updated")
    )
    repository.save(job)

    restored = JobRepository(tmp_path / "jobs").require("job")

    assert restored.preset_id == "preset-id"
    assert restored.preset_name == "Preset name"
    assert restored.require_preset_body_snapshot() == "exact body"


def test_generating_without_remote_identity_fails_closed_on_restart(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(Job("input/a.txt", id="job", state=JobState.GENERATING))
    recovered = repository.recover_interrupted()[0]
    assert recovered.state is JobState.FAILED
    assert recovered.error_code == "NOTEBOOK_RESUME_METADATA_MISSING"


@pytest.mark.parametrize(
    "stable",
    [
        JobState.WAITING,
        JobState.WAITING_VIDEO,
        JobState.DOWNLOAD_VERIFY_FAILED,
        JobState.RAW_READY,
        JobState.COMPLETED,
        JobState.FAILED,
    ],
)
def test_restart_recovery_does_not_rerun_stable_or_terminal_jobs(tmp_path, stable) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(Job("input/a.txt", id="job", state=stable))
    assert repository.recover_interrupted() == []
    assert repository.require("job").state == stable


def test_load_recoverable_repairs_interrupted_jobs_and_excludes_terminals(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs")
    repository.save(Job("input/a.txt", id="active", state=JobState.ENDING))
    repository.save(Job("input/b.txt", id="done", state=JobState.COMPLETED))
    repository.save(Job("input/c.txt", id="failed", state=JobState.FAILED))
    assert [(job.id, job.state) for job in repository.load_recoverable()] == [
        ("active", JobState.RAW_READY)
    ]


def test_state_repository_detects_unclean_restart_and_can_close_cleanly(tmp_path) -> None:
    repository = StateRepository(tmp_path / "state.json")
    assert repository.mark_started() is False
    assert repository.mark_started() is True
    repository.save(RuntimeState(False, "notebook-job", ("ffmpeg-job",)))
    assert repository.load().active_ffmpeg_job_ids == ("ffmpeg-job",)
    repository.mark_clean_shutdown()
    assert repository.load() == RuntimeState()
