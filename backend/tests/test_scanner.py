import sys
from pathlib import Path

import pytest

from reposherlock.core.config import ScanLimits
from reposherlock.ingestion.models import SkippedEntry, SourceFile
from reposherlock.ingestion.scanner import scan_repository


def test_discovery(sample_repository: Path) -> None:
    entries = list(scan_repository(str(sample_repository)))

    assert [(entry.relative_path, entry.language) for entry in entries if isinstance(entry, SourceFile)] == [
        ("README.md", "markdown"),
        ("config/settings.json", "json"),
        ("src/inventory.py", "python"),
    ]
    assert SkippedEntry("notes.txt", "unsupported_extension") in entries


@pytest.mark.parametrize("extension", "py js jsx ts tsx java cpp cc c h hpp go rs sql json md yaml yml".split())
def test_supported_extensions(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"example.{extension.upper()}").write_bytes(b"sample\n")
    [entry] = scan_repository(tmp_path)
    assert isinstance(entry, SourceFile)
    assert entry.size_bytes == 7


def test_excluded_directories_and_sensitive_names(tmp_path: Path) -> None:
    directories = [
        ".git", "Node_Modules", ".venv", "venv", "__pycache__", "dist", "build",
        "coverage", ".next", ".reposherlock", ".ssh",
    ]
    filenames = [".env", ".ENV.local", "host.pem", "host.key", "id_ed25519", "credentials.JSON",
                 "service-account.json", ".git-credentials", "secrets.yaml"]
    for name in directories:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "hidden.py").write_bytes(b"hidden\n")
    for name in filenames:
        (tmp_path / name).write_bytes(b"example\n")

    assert set(scan_repository(tmp_path)) == {
        *(SkippedEntry(name, "excluded_directory", is_directory=True) for name in directories),
        *(SkippedEntry(name, "sensitive_filename") for name in filenames),
    }


def test_binary_oversized_and_non_utf8_files(tmp_path: Path) -> None:
    (tmp_path / "binary.py").write_bytes(b"prefix\x00")
    (tmp_path / "large.py").write_bytes(b"X" * 17)
    (tmp_path / "legacy.md").write_bytes(b"\xe9")
    (tmp_path / "small.py").write_bytes(b"X" * 16)

    assert list(scan_repository(tmp_path, ScanLimits(max_file_bytes=16))) == [
        SkippedEntry("binary.py", "binary_content"),
        SkippedEntry("large.py", "file_size_limit"),
        SkippedEntry("legacy.md", "invalid_utf8"),
        SourceFile("small.py", "python", "X" * 16, 16),
    ]


def test_private_key_header_in_supported_extension(tmp_path: Path) -> None:
    (tmp_path / "misc.md").write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n")
    assert list(scan_repository(tmp_path)) == [
        SkippedEntry("misc.md", "private_key_content"),
    ]


def test_total_bytes_and_file_count(tmp_path: Path) -> None:
    for name, content in {"a.py": b"123", "b.py": b"1234", "c.py": b"12", "d.py": b"1"}.items():
        (tmp_path / name).write_bytes(content)

    assert list(scan_repository(tmp_path, ScanLimits(max_files=2, max_total_bytes=5))) == [
        SourceFile("a.py", "python", "123", 3),
        SkippedEntry("b.py", "total_byte_limit"),
        SourceFile("c.py", "python", "12", 2),
        SkippedEntry("d.py", "file_count_limit"),
    ]


def test_unreadable_file_does_not_stop_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "blocked.py"
    blocked.touch()
    (tmp_path / "readable.py").write_bytes(b"12\n")
    original_open = Path.open

    def selective_open(self: Path, *args, **kwargs):
        if self == blocked:
            raise PermissionError("test read error")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", selective_open)
    assert list(scan_repository(tmp_path)) == [
        SkippedEntry("blocked.py", "unreadable_file"),
        SourceFile("readable.py", "python", "12\n", 3),
    ]


def test_symlinks_are_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.py").write_bytes(b"outside\n")
    root = tmp_path / "repo"
    root.mkdir()
    try:
        (root / "directory").symlink_to(outside, target_is_directory=True)
        (root / "file.py").symlink_to(outside / "source.py")
        (root / "dangling.py").symlink_to(outside / "missing.py")
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    assert list(scan_repository(root)) == [
        SkippedEntry("dangling.py", "symlink_or_reparse_point"),
        SkippedEntry("directory", "symlink_or_reparse_point"),
        SkippedEntry("file.py", "symlink_or_reparse_point"),
    ]
    with pytest.raises(ValueError, match="real directory"):
        scan_repository(root / "directory")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_windows_junction_is_skipped(tmp_path: Path) -> None:
    from _winapi import CreateJunction

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.py").write_bytes(b"outside\n")
    root = tmp_path / "repo"
    root.mkdir()
    junction = root / "junction"
    try:
        CreateJunction(str(outside), str(junction))
    except PermissionError:
        pytest.skip("Windows junction creation is unavailable")
    try:
        assert list(scan_repository(root)) == [
            SkippedEntry("junction", "symlink_or_reparse_point"),
        ]
    finally:
        junction.rmdir()


def test_empty_and_invalid_paths(tmp_path: Path) -> None:
    assert list(scan_repository(tmp_path)) == []
    with pytest.raises(ValueError, match="does not exist"):
        scan_repository(tmp_path / "missing")
    file = tmp_path / "example.py"
    file.touch()
    with pytest.raises(ValueError, match="real directory"):
        scan_repository(file)
