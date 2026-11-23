import hashlib
import json
import os
from pathlib import Path

import pytest

from reposherlock.core.paths import extended_path
from reposherlock.ingestion.indexer import index_repository
from reposherlock.ingestion.models import IndexResult


def load_index(result: IndexResult) -> tuple[dict, list[dict]]:
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    chunks_path = result.manifest_path.parent / manifest["chunks_file"]
    assert chunks_path == result.chunks_path
    data = chunks_path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["chunks_sha256"]
    return manifest, [json.loads(line) for line in data.splitlines()]


def test_index_output_and_manifest(sample_repository: Path) -> None:
    result = index_repository(sample_repository)
    manifest, chunks = load_index(result)

    expected_directory = (
        sample_repository.resolve() / ".reposherlock" / "indexes" / manifest["repository_id"]
    )
    assert extended_path(result.manifest_path.parent) == extended_path(expected_directory)
    assert "/indexes/" in (sample_repository / ".reposherlock" / ".gitignore").read_text(encoding="utf-8")
    assert manifest["schema_version"] == 1
    assert (manifest["scanned_files"], manifest["indexed_files"], manifest["skipped_files"]) == (4, 3, 1)
    assert manifest["total_chunks"] == len(chunks) == 3
    assert manifest["skip_reasons"] == {"excluded_directory": 1, "unsupported_extension": 1}
    assert manifest["indexed_source_bytes"] == sum(
        (sample_repository / chunk["relative_path"]).stat().st_size for chunk in chunks
    )
    chunk = next(row for row in chunks if row["relative_path"] == "src/inventory.py")
    assert chunk["language"] == "python"
    assert chunk["start_line"] == 1
    source = (sample_repository / chunk["relative_path"]).read_bytes().decode("utf-8")
    assert chunk["content"] == source
    assert chunk["end_line"] == len(source.splitlines())
    assert len(chunk["chunk_id"]) == 64


def test_repeated_index_and_replacement(sample_repository: Path) -> None:
    first = index_repository(sample_repository)
    original_manifest = first.manifest_path.read_bytes()
    original_chunks = first.chunks_path.read_bytes()

    second = index_repository(sample_repository / "src" / "..")
    assert first == second
    assert second.manifest_path.read_bytes() == original_manifest
    assert second.chunks_path.read_bytes() == original_chunks
    assert list(second.chunks_path.parent.glob("*.jsonl")) == [first.chunks_path]

    (sample_repository / "README.md").unlink()
    third = index_repository(sample_repository)
    manifest, chunks = load_index(third)
    assert third.manifest_path == first.manifest_path
    assert third.chunks_path != first.chunks_path
    assert manifest["indexed_files"] == manifest["total_chunks"] == 2
    assert [row["relative_path"] for row in chunks] == ["config/settings.json", "src/inventory.py"]
    assert first.chunks_path.read_bytes() == original_chunks


def test_manifest_failure_preserves_previous_index(
    sample_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = index_repository(sample_repository)
    previous = load_index(first)
    (sample_repository / "README.md").unlink()
    replace = os.replace

    def fail_manifest_replace(source: Path, destination: Path) -> None:
        if destination == first.manifest_path:
            raise PermissionError("test publication failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_manifest_replace)
        with pytest.raises(PermissionError, match="publication failure"):
            index_repository(sample_repository)

    assert load_index(first) == previous
    assert not list(first.manifest_path.parent.glob(".index-*"))
    assert load_index(index_repository(sample_repository))[0]["total_chunks"] == 2


def test_empty_index(tmp_path: Path) -> None:
    result = index_repository(tmp_path)
    manifest, chunks = load_index(result)
    assert manifest["scanned_files"] == manifest["indexed_files"] == manifest["skipped_files"] == 0
    assert manifest["total_chunks"] == manifest["indexed_source_bytes"] == 0
    assert not chunks


def test_empty_source_counts_as_indexed(tmp_path: Path) -> None:
    (tmp_path / "empty.py").touch()
    result = index_repository(tmp_path)
    assert result.scanned_files == result.indexed_files == 1
    assert result.total_chunks == 0


def test_deep_repository_path(tmp_path: Path) -> None:
    root = tmp_path / ("a" * 105) / ("b" * 105)
    directory = extended_path(root)
    directory.mkdir(parents=True)
    (directory / "example.py").write_bytes(b"sample\n")

    result = index_repository(root)
    manifest, chunks = load_index(result)
    assert manifest["total_chunks"] == 1
    assert chunks[0]["relative_path"] == "example.py"
    assert index_repository(directory) == result
