from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from djd_maker.core.repositories import (
    DuplicatePresetNameError,
    MalformedJsonError,
    PresetRepository,
    SCHEMA_VERSION,
)


def test_preset_repository_save_load_and_default(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    assert repository.list() == []
    assert repository.selected() is None
    assert not path.exists()

    created = repository.create("通常講義", "日本語で説明してください。")
    assert PresetRepository(path).get(created.id) == created
    assert repository.selected() == created
    assert PresetRepository(path).selected() is None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["kind"] == "presets"


def test_preset_create_edit_select_and_restart_starts_unselected(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    first = repository.create("B", "本文B")
    second = repository.create("A", "本文A")
    changed = repository.update(second.id, "A edited", "更新本文")
    repository.select(changed.id)

    restarted = PresetRepository(path)
    assert [item.name for item in restarted.list()] == ["A edited", "B"]
    assert restarted.selected() is None
    assert restarted.get(first.id) == first


def test_legacy_persisted_selected_id_is_ignored_on_new_launch(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    created = repository.create("Legacy selected", "must require a fresh selection")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selected_preset_id"] = created.id
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    restarted = PresetRepository(path)

    assert restarted.list() == [created]
    assert restarted.selected() is None


def test_next_preset_save_clears_legacy_persisted_selection(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    created = repository.create("Legacy selected", "must require a fresh selection")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selected_preset_id"] = created.id
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    restarted = PresetRepository(path)
    restarted.update(created.id, created.name, "updated body")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["selected_preset_id"] is None
    assert restarted.selected() is None


def test_select_is_process_local_and_never_persisted(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    created = repository.create("Current run", "selected only for this run")
    repository.select(created.id)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["selected_preset_id"] is None
    assert repository.require_selected() == created
    assert PresetRepository(path).selected() is None


def test_preset_duplicate_name_is_case_insensitive(tmp_path) -> None:
    repository = PresetRepository(tmp_path / "presets.json")
    repository.create("Lecture", "one")
    with pytest.raises(DuplicatePresetNameError):
        repository.create(" lecture ", "two")


@pytest.mark.parametrize(
    ("name", "body"),
    [("", "body"), ("   ", "body"), ("name", ""), ("name", "\n\t")],
)
def test_preset_empty_name_or_body_is_rejected(tmp_path, name, body) -> None:
    repository = PresetRepository(tmp_path / "presets.json")
    with pytest.raises(ValueError):
        repository.create(name, body)


def test_delete_selected_chooses_first_name_or_none(tmp_path) -> None:
    repository = PresetRepository(tmp_path / "presets.json")
    zulu = repository.create("Zulu", "z")
    alpha = repository.create("Alpha", "a")
    repository.select(zulu.id)
    repository.delete(zulu.id)
    assert repository.selected() == alpha
    repository.delete(alpha.id)
    assert repository.selected() is None


def test_duplicate_matches_gnb_copy_naming_and_select_is_explicit(tmp_path) -> None:
    repository = PresetRepository(tmp_path / "presets.json")
    original = repository.create("講義", "本文")
    copied = repository.duplicate(original.id)
    copied_again = repository.duplicate(original.id)
    assert (copied.name, copied_again.name) == ("講義 (copy)", "講義 (copy) 2")
    assert copied.prompt_text == original.prompt_text
    assert repository.selected() == original


def test_preset_malformed_primary_recovers_backup(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    first = repository.create("first", "old")
    repository.create("second", "new")
    path.write_text("{broken", encoding="utf-8")

    recovered = PresetRepository(path)
    assert recovered.list() == [first]
    assert list(tmp_path.glob("presets.json.corrupt-*"))


def test_unrecoverable_preset_json_fails_closed(tmp_path) -> None:
    path = tmp_path / "presets.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(MalformedJsonError):
        PresetRepository(path).list()


def test_preset_atomic_save_keeps_backup_and_no_temporary(tmp_path) -> None:
    path = tmp_path / "presets.json"
    repository = PresetRepository(path)
    repository.create("one", "body")
    repository.create("two", "body")
    assert json.loads(path.read_text(encoding="utf-8"))["kind"] == "presets"
    assert json.loads((tmp_path / "presets.json.bak").read_text(encoding="utf-8"))[
        "kind"
    ] == "presets"
    assert list(tmp_path.glob(".presets.json.*.tmp")) == []


def test_preset_same_process_concurrent_saves_are_serialized(tmp_path) -> None:
    path = tmp_path / "presets.json"

    def create(number: int) -> None:
        PresetRepository(path).create(f"preset-{number:02}", f"body-{number}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(create, range(40)))
    assert len(PresetRepository(path).list()) == 40


def test_stale_legacy_lock_does_not_block_preset_save(tmp_path) -> None:
    path = tmp_path / "presets.json"
    lock = tmp_path / ".presets.json.lock"
    lock.write_text("legacy", encoding="utf-8")
    os.chmod(lock, 0o444)
    PresetRepository(path).create("name", "body")
    assert len(PresetRepository(path).list()) == 1


def test_preset_preflight_requires_selected_readable_body(tmp_path) -> None:
    repository = PresetRepository(tmp_path / "presets.json")
    with pytest.raises(RuntimeError, match="動画生成プリセット"):
        repository.require_selected()
    created = repository.create("name", "body")
    assert repository.require_selected() == created
