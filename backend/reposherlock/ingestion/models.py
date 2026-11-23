"""Data passed between scanning, chunking, and persistence."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    relative_path: str
    language: str
    content: str
    size_bytes: int


@dataclass(frozen=True)
class SkippedEntry:
    relative_path: str
    reason: str
    is_directory: bool = False


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    relative_path: str
    language: str
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class SemanticChunk(CodeChunk):
    start_column: int
    end_column: int
    strategy: str
    symbol_name: str | None = None
    symbol_type: str | None = None


@dataclass(frozen=True)
class IndexResult:
    chunks_path: Path
    manifest_path: Path
    scanned_files: int
    indexed_files: int
    skipped_files: int
    total_chunks: int
