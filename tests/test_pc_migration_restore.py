from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.restore_pc_migration import RestoreError, load_plan, restore


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def package(tmp_path: Path, content=b"safe settings") -> tuple[Path, Path]:
    migration = tmp_path / "Dropbox" / "VSCode_local" / "DJDmaker"
    repository = tmp_path / "clone"
    source = migration / "settings" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    manifest = {
        "project_name": "DJDmaker",
        "copied_files": [{
            "migration_relative_path": "settings/settings.json",
            "destination_relative_path": "system/settings.json",
            "size": len(content), "sha256": digest(source), "restore_required": True,
            "machine_specific": True, "sensitive": False,
        }],
    }
    (migration / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return migration, repository


def test_restore_defaults_to_preview_and_apply_creates_verified_file(tmp_path):
    migration, repository = package(tmp_path)
    _, plan = load_plan(migration, repository)
    assert [item["status"] for item in plan] == ["CREATE"]
    restore(plan, apply=False)
    assert not (repository / "system/settings.json").exists()
    restore(plan, apply=True)
    assert (repository / "system/settings.json").read_bytes() == b"safe settings"


def test_restore_identical_is_noop_and_conflict_fails_before_any_write(tmp_path):
    migration, repository = package(tmp_path)
    destination = repository / "system/settings.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"safe settings")
    _, identical = load_plan(migration, repository)
    assert identical[0]["status"] == "IDENTICAL"
    restore(identical, apply=True)
    destination.write_bytes(b"different")
    _, conflict = load_plan(migration, repository)
    assert conflict[0]["status"] == "CONFLICT"
    with pytest.raises(RestoreError, match="CONFLICT"):
        restore(conflict, apply=True)
    assert destination.read_bytes() == b"different"


def test_restore_rejects_tamper_traversal_and_sensitive_entry(tmp_path):
    migration, repository = package(tmp_path)
    source = migration / "settings/settings.json"
    source.write_bytes(b"tampered")
    with pytest.raises(RestoreError, match="hash/size"):
        load_plan(migration, repository)

    migration, repository = package(tmp_path / "traversal")
    manifest_path = migration / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["copied_files"][0]["destination_relative_path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RestoreError, match="root外"):
        load_plan(migration, repository)

    migration, repository = package(tmp_path / "sensitive")
    manifest_path = migration / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["copied_files"][0]["sensitive"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RestoreError, match="秘密情報"):
        load_plan(migration, repository)
