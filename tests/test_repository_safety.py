from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_runtime_directories_exist() -> None:
    for relative in (
        "input",
        "raw_files",
        "output",
        "work",
        "system/jobs",
        "logs",
        "browser",
    ):
        assert (ROOT / relative / ".gitkeep").is_file()


def test_gitignore_has_required_safety_patterns() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required = (
        "raw_files/**",
        "work/**",
        "output/**",
        "logs/**",
        "browser/**",
        "*.mp4",
        "*.m3u8",
        "*.ts",
        "*.zip",
        "*.crdownload",
        "*cookie*",
        "*session*",
        "*token*",
        "*credential*",
        "*secret*",
        ".venv/",
        "build/",
        "dist/",
    )
    for pattern in required:
        assert pattern in text


def test_no_database_dependency_or_file_is_tracked_in_source_tree() -> None:
    forbidden_suffixes = {".db", ".sqlite", ".sqlite3"}
    excluded_roots = {".git", ".test-tmp", ".pytest_cache"}
    candidates = (
        path
        for path in ROOT.rglob("*")
        if not excluded_roots.intersection(path.relative_to(ROOT).parts)
    )
    assert not [path for path in candidates if path.suffix.lower() in forbidden_suffixes]
