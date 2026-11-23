"""Validated limits for local ingestion."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanLimits:
    max_file_bytes: int = 512 * 1024
    max_files: int = 5_000
    max_total_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        for field in ("max_file_bytes", "max_files", "max_total_bytes"):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field} must be a positive integer")


@dataclass(frozen=True)
class ChunkSettings:
    chunk_lines: int = 100
    overlap_lines: int = 15

    def __post_init__(self) -> None:
        if type(self.chunk_lines) is not int or self.chunk_lines < 1:
            raise ValueError("chunk_lines must be a positive integer")
        if type(self.overlap_lines) is not int or not 0 <= self.overlap_lines < self.chunk_lines:
            raise ValueError("overlap_lines must be between 0 and chunk_lines - 1")


SEMANTIC_CHUNK_SETTINGS = ChunkSettings(24, 3)
