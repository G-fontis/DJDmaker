from __future__ import annotations

import json
import subprocess
from pathlib import Path

from djd_maker.packaging.preflight import (
    DEFAULT_SETTING_KEYS,
    WRITABLE_DIRECTORIES,
    application_root,
    find_media_tool,
    inspect_packaging,
    release_tree_violations,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _default_config(root: Path) -> None:
    config = root / "config" / "default-settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    values = {key: "" for key in DEFAULT_SETTING_KEYS}
    values.update(
        {
            "input_directory": "input",
            "raw_directory": "raw_files",
            "output_directory": "output",
            "first_notebook_check_seconds": 600,
            "notebook_poll_seconds": 120,
            "audio_tail_padding_seconds": 0.5,
            "ffmpeg_concurrency": 1,
        }
    )
    config.write_text(json.dumps(values), encoding="utf-8")


def _tool_runner(command, **_kwargs):
    return subprocess.CompletedProcess(command, 0, f"{Path(command[0]).stem} version test\n", "")


def _fake_environment(tmp_path: Path) -> dict[str, str]:
    chrome = tmp_path / "Chrome 日本語" / "chrome.exe"
    chrome.parent.mkdir(parents=True, exist_ok=True)
    chrome.write_bytes(b"chrome")
    return {"DJD_CHROME_PATH": str(chrome)}


def test_source_application_root_is_repository_root() -> None:
    assert application_root() == PROJECT_ROOT


def test_media_tool_resolution_prefers_explicit_then_portable(tmp_path: Path) -> None:
    portable = tmp_path / "runtime" / "ffmpeg" / "ffmpeg.exe"
    portable.parent.mkdir(parents=True)
    portable.write_bytes(b"portable")
    explicit = tmp_path / "reviewed" / "ffmpeg.exe"
    explicit.parent.mkdir()
    explicit.write_bytes(b"explicit")
    assert find_media_tool(
        tmp_path,
        "ffmpeg",
        environ={"DJD_FFMPEG_PATH": str(explicit)},
        which=lambda _name: "from-path",
    ) == explicit.resolve()
    assert find_media_tool(
        tmp_path, "ffmpeg", environ={}, which=lambda _name: None
    ) == portable.resolve()


def test_complete_source_preflight_supports_unicode_and_space_path(tmp_path: Path) -> None:
    root = tmp_path / "日本語 portable root"
    root.mkdir()
    _default_config(root)
    media = root / "runtime" / "ffmpeg"
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (media / "ffprobe.exe").write_bytes(b"ffprobe")
    report = inspect_packaging(
        root,
        environ=_fake_environment(root),
        which=lambda _name: None,
        runner=_tool_runner,
        module_finder=lambda _name: object(),
    )
    assert report.passed
    assert next(item for item in report.checks if item.name == "unicode-space-path").passed
    assert not (root / "日本語 path 検証").exists()


def test_bad_default_config_blocks_preflight(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "default-settings.json").write_text("{}", encoding="utf-8")
    report = inspect_packaging(
        root,
        environ={},
        which=lambda _name: None,
        runner=_tool_runner,
        module_finder=lambda _name: object(),
    )
    check = next(item for item in report.checks if item.name == "default-config")
    assert not check.passed
    assert not report.passed


def test_release_tree_requires_assets_and_rejects_runtime_user_data(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    violations = release_tree_violations(root)
    assert any("DJDmaker.exe" in item for item in violations)
    for relative in WRITABLE_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    private = root / "browser" / "chrome-profile" / "Cookies"
    private.write_bytes(b"private")
    violations = release_tree_violations(root)
    assert any("runtime/user data" in item for item in violations)
    assert any("private browser" in item for item in violations)


def test_complete_release_tree_passes_structural_scan(tmp_path: Path) -> None:
    root = tmp_path / "DJDmaker_v0.1"
    (root / "_internal").mkdir(parents=True)
    (root / "DJDmaker.exe").write_bytes(b"exe")
    _default_config(root)
    media = root / "runtime" / "ffmpeg"
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (media / "ffprobe.exe").write_bytes(b"ffprobe")
    licenses = root / "licenses"
    licenses.mkdir()
    (licenses / "FFmpeg-LICENSE.txt").write_text("reviewed license", encoding="utf-8")
    for relative in WRITABLE_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    assert release_tree_violations(root) == []
    report = inspect_packaging(
        root,
        release_tree=True,
        environ=_fake_environment(tmp_path),
        which=lambda _name: None,
        runner=_tool_runner,
        module_finder=lambda _name: object(),
    )
    assert report.passed


def test_spec_is_windowed_onedir_and_build_script_never_creates_zip() -> None:
    spec = (PROJECT_ROOT / "packaging" / "DJDMaker.spec").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    hook = (PROJECT_ROOT / "packaging" / "runtime_hooks" / "portable_paths.py").read_text(encoding="utf-8")
    assert 'console=False' in spec
    assert 'name="DJDmaker_v0.1"' in spec
    assert 'collect_all("playwright")' in spec
    assert '"PySide6.QtMultimedia"' in spec
    assert '"runtime/ffmpeg"' in spec
    assert '"default-settings.json"' in spec
    assert '"windows_version_info.txt"' in spec
    version_info = (PROJECT_ROOT / "packaging" / "windows_version_info.txt").read_text(
        encoding="utf-8"
    )
    assert "ProductVersion', u'0.1'" in version_info
    assert "FileVersion', u'0.1.0.0'" in version_info
    assert '"config"' in spec
    assert "--release-tree" in script
    assert 'Move-Item -LiteralPath $Source -Destination $Destination' in script
    assert '$ResolvedLicense -ne $StagedLicense' in script
    assert "Compress-Archive" not in script
    assert ".zip" not in script.casefold()
    assert 'os.environ["PATH"]' in hook
