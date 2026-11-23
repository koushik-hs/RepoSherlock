import pytest

from reposherlock.core.config import ChunkSettings
from reposherlock.ingestion.chunker import chunk_file
from reposherlock.ingestion.models import SourceFile


def source_file(content: str, path: str = "src/demo.py") -> SourceFile:
    return SourceFile(path, "python", content, len(content.encode("utf-8")))


def test_chunk_ranges_overlap_and_keep_original_line_endings() -> None:
    source = source_file("one\r\n\r\nthree\nfour\rfive")
    chunks = list(chunk_file(source, ChunkSettings(chunk_lines=3, overlap_lines=1)))

    assert [(chunk.start_line, chunk.end_line, chunk.content) for chunk in chunks] == [
        (1, 3, "one\r\n\r\nthree\n"),
        (3, 5, "three\nfour\rfive"),
    ]
    assert all(chunk.relative_path == source.relative_path and chunk.language == "python" for chunk in chunks)


def test_default_window_has_no_redundant_tail() -> None:
    source = source_file("a\n" * 185)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunk_file(source)] == [
        (1, 100), (86, 185)
    ]


def test_stable_ids_depend_on_path_range_and_content() -> None:
    settings = ChunkSettings(chunk_lines=1, overlap_lines=0)
    source = source_file("same\nsame\n")
    first, second = chunk_file(source, settings)
    assert list(chunk_file(source, settings)) == [first, second]
    assert first.chunk_id != second.chunk_id
    assert first.chunk_id != next(chunk_file(source_file(source.content, "other.py"), settings)).chunk_id
    assert first.chunk_id != next(chunk_file(source_file("changed\n"), settings)).chunk_id


def test_empty_source_and_invalid_overlap() -> None:
    assert list(chunk_file(source_file(""))) == []
    with pytest.raises(ValueError, match="overlap_lines"):
        ChunkSettings(chunk_lines=5, overlap_lines=5)
