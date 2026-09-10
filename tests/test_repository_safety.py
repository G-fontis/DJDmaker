from pathlib import Path
import os


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
        "system/**",
        ".venv/",
        "build/",
        "dist/",
    )
    for pattern in required:
        assert pattern in text


def test_no_database_dependency_or_file_is_tracked_in_source_tree() -> None:
    forbidden_suffixes = {".db", ".sqlite", ".sqlite3"}
    # Runtime trees are deliberately ignored and may contain Chrome's own
    # internal cache databases during a real acceptance run. The application
    # source and tracked fixtures must remain database-free.
    excluded_roots = {
        ".git",
        ".test-tmp",
        ".pytest_cache",
        "browser",
        "logs",
        "output",
        "raw_files",
        "work",
    }
    # Prune exactly the existing exclusions before traversal: acceptance Chrome
    # profiles can contain very large caches, none of which this test audits.
    forbidden = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in excluded_roots]
        forbidden.extend(Path(directory) / name for name in files + children
                         if Path(name).suffix.lower() in forbidden_suffixes)
    assert not forbidden
