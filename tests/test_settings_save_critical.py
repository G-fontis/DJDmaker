from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from djd_maker.core.repositories import (
    SCHEMA_VERSION,
    SettingsRepository,
    SettingsSaveError,
)
from djd_maker.core.settings import AppSettings


def _settings(index: int = 0) -> AppSettings:
    return AppSettings(
        input_directory=f"input-{index}",
        raw_directory=f"raw-{index}",
        output_directory=f"output-{index}",
        ending_video=f"ending-{index}.mp4",
        first_notebook_check_seconds=600 + index,
        notebook_poll_seconds=120 + index,
        ffmpeg_concurrency=1 + index % 2,
    )


def test_settings_single_save(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    repository.save(_settings(1))
    assert repository.load() == _settings(1)


def test_settings_rapid_repeated_save(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    for index in range(20):
        repository.save(_settings(index))
    assert repository.load() == _settings(19)


def test_settings_one_hundred_consecutive_saves(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    for index in range(100):
        repository.save(_settings(index))
    assert repository.load() == _settings(99)


def test_settings_multithread_save_is_serialized_by_path(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repositories = [SettingsRepository(path) for _ in range(8)]
    errors: list[Exception] = []

    def save(index: int) -> None:
        try:
            repositories[index % len(repositories)].save(_settings(index))
        except Exception as error:  # pragma: no cover - assertion captures it
            errors.append(error)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(80)))

    assert errors == []
    assert SettingsRepository(path).load() in [_settings(index) for index in range(80)]


@pytest.mark.parametrize("error", [PermissionError(13, "permission"), OSError(13, "denied")])
def test_settings_permission_error_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    from djd_maker.core import storage

    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    real_replace = storage.os.replace
    attempts = 0
    sleeps: list[float] = []

    def transient(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PermissionError(*error.args)
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", transient)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    repository.save(_settings(2))

    assert attempts == 3
    assert sleeps == [0.1, 0.2]
    assert repository.load() == _settings(2)


def test_settings_winerror32_simulation_retries_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    real_replace = storage.os.replace
    calls = 0

    def sharing_violation(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError(13, "sharing violation", str(destination))
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", sharing_violation)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    repository.save(_settings(3))
    assert calls == 2
    assert repository.load() == _settings(3)


def test_settings_retry_exhaustion_preserves_primary_and_reports_retryable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from djd_maker.core import storage

    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(_settings(1))
    original = path.read_bytes()
    real_replace = storage.os.replace
    attempts = 0

    def blocked(source, destination):
        nonlocal attempts
        if Path(destination) == path:
            attempts += 1
            raise PermissionError(13, "sharing violation", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda _delay: None)
    with pytest.raises(SettingsSaveError, match="破損していません.*再試行"):
        repository.save(_settings(2))

    assert attempts == 5
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".settings.json.*.tmp"))


@pytest.mark.parametrize("lock_name", [".settings.json.lock", "settings.json.lock"])
def test_legacy_settings_lock_is_ignored(tmp_path: Path, lock_name: str) -> None:
    lock = tmp_path / lock_name
    lock.write_text("legacy lock", encoding="utf-8")
    repository = SettingsRepository(tmp_path / "settings.json")
    repository.save(_settings(4))
    assert repository.load() == _settings(4)
    assert lock.read_text(encoding="utf-8") == "legacy lock"


def test_read_only_legacy_lock_does_not_block_save(tmp_path: Path) -> None:
    lock = tmp_path / ".settings.json.lock"
    lock.write_text("legacy", encoding="utf-8")
    lock.chmod(stat.S_IREAD)
    try:
        repository = SettingsRepository(tmp_path / "settings.json")
        repository.save(_settings(5))
        assert repository.load() == _settings(5)
    finally:
        lock.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_dropbox_japanese_space_and_deep_path(tmp_path: Path) -> None:
    path = tmp_path / "Dropbox" / "福ゼミ 設定" / "深い" / "階層" / "system" / "settings.json"
    repository = SettingsRepository(path)
    repository.save(_settings(6))
    assert SettingsRepository(path).load() == _settings(6)


def test_settings_backup_keeps_previous_valid_value(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(_settings(7))
    repository.save(_settings(8))
    backup = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert AppSettings(**backup["settings"]) == _settings(7)


def test_settings_atomicity_never_exposes_partial_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    reader_repository = SettingsRepository(path)
    repository.save(_settings())
    stop = threading.Event()
    read_errors: list[Exception] = []

    def read_repeatedly() -> None:
        while not stop.is_set():
            try:
                reader_repository.load()
            except Exception as error:  # pragma: no cover - assertion captures it
                read_errors.append(error)

    reader = threading.Thread(target=read_repeatedly)
    reader.start()
    try:
        for index in range(30):
            repository.save(_settings(index))
    finally:
        stop.set()
        reader.join()
    assert read_errors == []


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_real_windows_reader_sharing_violation_recovers_after_release(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(_settings(1))
    reader = path.open("rb")
    timer = threading.Timer(0.25, reader.close)
    timer.start()
    try:
        repository.save(_settings(2))
    finally:
        timer.join()
        if not reader.closed:
            reader.close()
    assert repository.load() == _settings(2)


def test_crash_ready_temporary_recovers_when_primary_missing(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    temporary = tmp_path / ".settings.json.before-crash.tmp"
    value = _settings(9)
    temporary.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "settings",
                "settings": value.to_dict(),
            }
        ),
        encoding="utf-8",
    )
    assert SettingsRepository(path).load() == value
    assert path.is_file()


def test_malformed_settings_recovers_valid_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(_settings(10))
    repository.save(_settings(11))
    path.write_text("{broken", encoding="utf-8")
    assert SettingsRepository(path).load() == _settings(10)
    assert list(tmp_path.glob("settings.json.corrupt-*"))


def test_settings_restart_restores_latest_value(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    SettingsRepository(path).save(_settings(12))
    assert SettingsRepository(path).load() == _settings(12)


def test_ending_path_change_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    value = _settings(13)
    SettingsRepository(path).save(value)
    assert SettingsRepository(path).load().ending_video == "ending-13.mp4"


def test_input_raw_output_folder_changes_are_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    value = _settings(14)
    SettingsRepository(path).save(value)
    loaded = SettingsRepository(path).load()
    assert (loaded.input_directory, loaded.raw_directory, loaded.output_directory) == (
        "input-14",
        "raw-14",
        "output-14",
    )


def test_scheduler_settings_changes_are_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    value = _settings(15)
    SettingsRepository(path).save(value)
    loaded = SettingsRepository(path).load()
    assert loaded.first_notebook_check_seconds == 615
    assert loaded.notebook_poll_seconds == 135
