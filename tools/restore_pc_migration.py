"""Safely restore DJDmaker local-only state from a Dropbox handoff package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


PROJECT_NAME = "DJDmaker"
MANIFEST_NAMES = ("MANIFEST.json", "manifest.json")


class RestoreError(RuntimeError):
    """A fail-closed migration validation or restore error."""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def discover_migration_root(explicit: str | Path | None = None) -> Path:
    """Locate the package without assuming a Windows user name."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for variable in ("DROPBOX", "Dropbox", "DROPBOX_ROOT"):
        if value := os.environ.get(variable):
            candidates.extend(
                (Path(value) / "VSCode_local" / PROJECT_NAME,
                 Path(value) / "DELLノートPC(2019～)" / "VSCode_local" / PROJECT_NAME)
            )
    home_dropbox = Path.home() / "Dropbox"
    candidates.extend(
        (home_dropbox / "VSCode_local" / PROJECT_NAME,
         home_dropbox / "DELLノートPC(2019～)" / "VSCode_local" / PROJECT_NAME)
    )
    matches = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if any((resolved / name).is_file() for name in MANIFEST_NAMES) and resolved not in matches:
            matches.append(resolved)
    if not matches:
        raise RestoreError("MANIFEST.jsonを含むDJDmaker移行folderを指定してください。")
    if len(matches) > 1 and explicit is None:
        raise RestoreError("移行folder候補が複数あります。pathを明示してください。")
    return matches[0]


def _safe_child(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise RestoreError(f"{label}が許可root外です: {value}") from error
    return path


def load_plan(migration_root: Path, repository_root: Path) -> tuple[dict, list[dict]]:
    manifest_path = next(
        (migration_root / name for name in MANIFEST_NAMES if (migration_root / name).is_file()),
        migration_root / MANIFEST_NAMES[0],
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RestoreError(f"manifestを読み込めません: {error}") from error
    if manifest.get("project_name") != PROJECT_NAME:
        raise RestoreError("別projectのmanifestです。")
    entries = manifest.get("files", manifest.get("copied_files"))
    if not isinstance(entries, list):
        raise RestoreError("filesが不正です。")
    plan: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RestoreError("file entryが不正です。")
        if entry.get("restore_required") is not True:
            continue
        backup_path = entry.get("relative_backup_path", entry.get("migration_relative_path", ""))
        restore_path = entry.get("restore_destination", entry.get("destination_relative_path", ""))
        source = _safe_child(migration_root, str(backup_path), "移行file")
        destination = _safe_child(repository_root, str(restore_path), "復元先")
        if not source.is_file():
            raise RestoreError(f"移行fileがありません: {source}")
        expected_size = entry.get("size")
        expected_hash = str(entry.get("sha256", "")).upper()
        if source.stat().st_size != expected_size or sha256(source) != expected_hash:
            raise RestoreError(f"移行fileのhash/sizeが一致しません: {source}")
        status = "CREATE"
        if destination.exists():
            if not destination.is_file():
                status = "CONFLICT"
            else:
                status = "IDENTICAL" if sha256(destination) == expected_hash else "CONFLICT"
        plan.append({
            "source": source,
            "destination": destination,
            "status": status,
            "machine_specific": bool(entry.get("machine_specific")),
            "contains_secret": bool(entry.get("contains_secret", entry.get("sensitive", False))),
            "sha256": expected_hash,
        })
    return manifest, plan


def restore(plan: list[dict], *, apply: bool) -> list[dict]:
    if apply and any(item["status"] == "CONFLICT" for item in plan):
        raise RestoreError("CONFLICTがあります。既存fileは変更していません。")
    if not apply:
        return plan
    for item in plan:
        if item["status"] != "CREATE":
            continue
        destination: Path = item["destination"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                                       dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(item["source"], temporary)
            if sha256(temporary) != item["sha256"]:
                raise RestoreError(f"復元tempのhashが一致しません: {temporary}")
            # Same-directory hard-link publication is atomic and fails if a
            # concurrent writer creates the destination. It never overwrites.
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise RestoreError(f"復元直前にdestinationが作成されました: {destination}") from error
        finally:
            temporary.unlink(missing_ok=True)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DJDmaker PC移行データを検証・復元します（既定dry-run）。")
    parser.add_argument("migration_root", nargs="?", help="manifest.jsonを含むDropbox側DJDmaker folder")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true", help="競合がないCREATEだけを復元")
    args = parser.parse_args(argv)
    try:
        migration_root = discover_migration_root(args.migration_root)
        repository_root = Path(args.repo_root).resolve()
        _, plan = load_plan(migration_root, repository_root)
        restore(plan, apply=args.apply)
        for item in plan:
            suffix = " [PC固有pathを再確認]" if item["machine_specific"] else ""
            print(f"{item['status']}: {item['destination']}{suffix}")
        print(f"RESTORE_PC_MIGRATION: {'APPLIED' if args.apply else 'DRY_RUN'} ({len(plan)} files)")
        return 0 if not any(item["status"] == "CONFLICT" for item in plan) else 2
    except RestoreError as error:
        print(f"RESTORE_PC_MIGRATION: BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
