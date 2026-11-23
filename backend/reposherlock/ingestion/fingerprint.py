"""Fingerprint the accepted source set using the same scanner as ingestion."""

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator

from reposherlock.ingestion.models import SkippedEntry, SourceFile


def fingerprint_sources(
    entries: Iterable[SourceFile | SkippedEntry], update: Callable[[bytes], None]
) -> Iterator[SourceFile | SkippedEntry]:
    for entry in entries:
        if isinstance(entry, SourceFile):
            header = json.dumps([entry.relative_path, entry.size_bytes]).encode("ascii")
            update(header + b"\n")
            update(entry.content.encode("utf-8"))
        yield entry


def source_fingerprint(entries: Iterable[SourceFile | SkippedEntry]) -> str:
    return source_fingerprint_and_size(entries)[0]


def source_fingerprint_and_size(entries: Iterable[SourceFile | SkippedEntry]) -> tuple[str, int]:
    digest = hashlib.sha256()
    source_bytes = 0
    for entry in fingerprint_sources(entries, digest.update):
        if isinstance(entry, SourceFile):
            source_bytes += entry.size_bytes
    return digest.hexdigest(), source_bytes
