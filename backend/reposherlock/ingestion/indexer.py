"""Write a local JSONL snapshot and commit it by replacing the manifest."""

import hashlib
import json
import os
import stat
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from reposherlock.core.config import ChunkSettings, ScanLimits
from reposherlock.core.paths import WINDOWS_DIRECTORY_PATH_LIMIT, extended_path
from reposherlock.ingestion.chunker import chunk_file
from reposherlock.ingestion.fingerprint import fingerprint_sources
from reposherlock.ingestion.models import CodeChunk, IndexResult, SkippedEntry, SourceFile
from reposherlock.ingestion.scanner import is_link, scan_repository, validate_repository


def repository_identifier(root: Path) -> str:
    canonical_path = os.path.normcase(extended_path(root))
    return hashlib.sha256(os.fsencode(canonical_path)).hexdigest()


def index_directory(root: Path, repository_id: str, *, create: bool = True) -> Path:
    parts = (".reposherlock", "indexes", repository_id)
    longest_name = "chunks-" + "0" * 64 + ".jsonl"
    if len(str(root.joinpath(*parts, longest_name))) >= WINDOWS_DIRECTORY_PATH_LIMIT:
        root = extended_path(root)
    directory = root
    for part in parts:
        directory = directory / part
        if create:
            directory.mkdir(exist_ok=True)
        info = directory.lstat()
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Index output path must be a real directory: {directory}")
    if not create:
        return directory
    # Keep generated files untracked in the target checkout as well.
    try:
        with (root / ".reposherlock" / ".gitignore").open("x", encoding="utf-8") as ignore:
            ignore.write("/.gitignore\n/indexes/\n")
    except FileExistsError:
        pass
    return directory


def index_repository(
    path: Path | str,
    scan_limits: ScanLimits = ScanLimits(),
    chunk_settings: ChunkSettings = ChunkSettings(),
    *,
    code_aware: bool = False,
) -> IndexResult:
    root = validate_repository(path)
    repository_id = repository_identifier(root)
    output = index_directory(root, repository_id)
    manifest_path = output / ("semantic-manifest.json" if code_aware else "manifest.json")
    make_chunks: Callable[[SourceFile, ChunkSettings], Iterator[CodeChunk]] = chunk_file
    schema_version = 1
    if code_aware:
        from reposherlock.ingestion.syntax_chunker import STRATEGY_VERSION, chunk_source

        make_chunks = chunk_source
        schema_version = 2

    indexed_files = indexed_bytes = total_chunks = 0
    skipped_files: list[SkippedEntry] = []
    skipped_directories: list[SkippedEntry] = []
    chunks_digest = hashlib.sha256()
    source_digest = hashlib.sha256()

    with TemporaryDirectory(prefix=".index-", dir=output) as staging:
        staged_chunks = Path(staging) / "chunks.jsonl"
        with staged_chunks.open("wb") as stream:
            entries = scan_repository(root, scan_limits)
            if code_aware:
                entries = fingerprint_sources(entries, source_digest.update)
            for entry in entries:
                if isinstance(entry, SkippedEntry):
                    skipped = skipped_directories if entry.is_directory else skipped_files
                    skipped.append(entry)
                    continue
                indexed_files += 1
                indexed_bytes += entry.size_bytes
                for chunk in make_chunks(entry, chunk_settings):
                    record = asdict(chunk)
                    if code_aware:
                        record["repository_id"] = repository_id
                    row = (json.dumps(record) + "\n").encode("utf-8")
                    stream.write(row)
                    chunks_digest.update(row)
                    total_chunks += 1
            stream.flush()
            os.fsync(stream.fileno())

        # A reader of an older manifest can still open the JSONL it references.
        chunks_sha256 = chunks_digest.hexdigest()
        chunks_path = output / f"chunks-{chunks_sha256}.jsonl"
        all_skipped = skipped_files + skipped_directories
        scanned_files = indexed_files + len(skipped_files)
        manifest = {
            "schema_version": schema_version,
            "repository_id": repository_id,
            "chunks_file": chunks_path.name,
            "chunks_sha256": chunks_sha256,
            "scanned_files": scanned_files,
            "indexed_files": indexed_files,
            "indexed_source_bytes": indexed_bytes,
            "skipped_files": len(skipped_files),
            "skipped_directories": len(skipped_directories),
            "total_chunks": total_chunks,
            "scan_limits": asdict(scan_limits),
            "chunk_settings": asdict(chunk_settings),
            "skip_reasons": dict(sorted(Counter(entry.reason for entry in all_skipped).items())),
            "skipped_entries": [asdict(entry) for entry in all_skipped],
        }
        if code_aware:
            manifest["source_sha256"] = source_digest.hexdigest()
            manifest["chunker_version"] = STRATEGY_VERSION
        staged_manifest = Path(staging) / "manifest.json"
        with staged_manifest.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(staged_chunks, chunks_path)
        os.replace(staged_manifest, manifest_path)

    return IndexResult(
        chunks_path=chunks_path,
        manifest_path=manifest_path,
        scanned_files=scanned_files,
        indexed_files=indexed_files,
        skipped_files=len(skipped_files),
        total_chunks=total_chunks,
    )
