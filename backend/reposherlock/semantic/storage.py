"""Filesystem state and Chroma collection publication for one checkout."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chromadb import ClientAPI
    from chromadb.api.models.Collection import Collection

from reposherlock.core.config import ScanLimits
from reposherlock.ingestion.fingerprint import source_fingerprint_and_size
from reposherlock.ingestion.indexer import index_directory
from reposherlock.ingestion.models import SemanticChunk, SkippedEntry, SourceFile
from reposherlock.ingestion.scanner import is_link, scan_repository
from reposherlock.semantic.embeddings import Embeddings

SNAPSHOT_PREFIX = "snapshot-"
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SourceSnapshot:
    repository_id: str
    chunks_sha256: str
    source_sha256: str
    count: int
    scan_limits: ScanLimits
    indexed_source_bytes: int

    @property
    def chunks_file(self) -> str:
        return f"chunks-{self.chunks_sha256}.jsonl"


@dataclass(frozen=True)
class VectorState:
    repository_id: str
    chunks_sha256: str
    source_sha256: str
    count: int
    model: str
    dimension: int
    collection: str

    def matches(self, source: SourceSnapshot) -> bool:
        return (
            self.repository_id == source.repository_id
            and self.chunks_sha256 == source.chunks_sha256
            and self.source_sha256 == source.source_sha256
            and self.count == source.count
        )

    def compatible(self, backend: Embeddings) -> bool:
        return self.model == backend.identity and self.dimension == backend.dimension


def _regular_file(path: Path) -> None:
    info = path.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Index data must be a regular file: {path}")


def _read_json(path: Path) -> dict:
    _regular_file(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_source(directory: Path, repository_id: str) -> SourceSnapshot:
    try:
        data = _read_json(directory / "semantic-manifest.json")
        snapshot = SourceSnapshot(
            data["repository_id"], data["chunks_sha256"], data["source_sha256"],
            data["total_chunks"], ScanLimits(**data["scan_limits"]), data["indexed_source_bytes"],
        )
        if (
            data["schema_version"] != 2 or snapshot.repository_id != repository_id
            or not SHA256.fullmatch(snapshot.chunks_sha256)
            or not SHA256.fullmatch(snapshot.source_sha256)
            or snapshot.chunks_file != data["chunks_file"]
            or type(snapshot.count) is not int or snapshot.count < 0
            or type(snapshot.indexed_source_bytes) is not int or snapshot.indexed_source_bytes < 0
        ):
            raise ValueError("invalid manifest metadata")
        return snapshot
    except FileNotFoundError as error:
        raise ValueError("No semantic source index. Run semantic-index first.") from error
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid semantic source manifest. Run semantic-index again.") from error


def read_chunks(directory: Path, source: SourceSnapshot) -> Iterator[SemanticChunk]:
    path = directory / source.chunks_file
    _regular_file(path)
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        for row in stream:
            digest.update(row)
            fields = json.loads(row)
            if fields.pop("repository_id") != source.repository_id:
                raise ValueError("Chunk belongs to a different repository.")
            count += 1
            yield SemanticChunk(**fields)
    if count != source.count or digest.hexdigest() != source.chunks_sha256:
        raise ValueError("Semantic JSONL failed verification. Run semantic-index again.")


def verify_chunk_file(directory: Path, source: SourceSnapshot) -> None:
    path = directory / source.chunks_file
    try:
        _regular_file(path)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError as error:
        raise ValueError("Semantic JSONL is missing. Run semantic-index again.") from error
    if actual != source.chunks_sha256:
        raise ValueError("Semantic JSONL failed verification. Run semantic-index again.")


def load_vector_state(directory: Path) -> VectorState | None:
    try:
        state = VectorState(**_read_json(directory / "vectors.json"))
        if not re.fullmatch(r"snapshot-[0-9a-f]{32}", state.collection):
            raise ValueError("invalid collection name")
        return state
    except FileNotFoundError:
        return None
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid vector index metadata. Run semantic-index --rebuild.") from error


def publish(directory: Path, state: VectorState) -> None:
    with TemporaryDirectory(prefix=".vector-", dir=directory) as staging:
        staged = Path(staging) / "vectors.json"
        with staged.open("w", encoding="utf-8") as stream:
            json.dump(asdict(state), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, directory / "vectors.json")


def open_database(directory: Path, *, create: bool = False) -> "ClientAPI":
    import chromadb
    from chromadb.config import Settings

    path = directory / "vectors"
    if create:
        path.mkdir(exist_ok=True)
    pending = [path]
    while pending:
        entry = pending.pop()
        info = entry.lstat()
        if is_link(info):
            raise ValueError(f"Vector storage must not contain links: {entry}")
        if stat.S_ISDIR(info.st_mode):
            pending.extend(entry.iterdir())
        elif entry == path or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Invalid vector storage path: {entry}")
    return chromadb.PersistentClient(
        path=str(path), settings=Settings(
            _env_file=None, chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
            anonymized_telemetry=False, chroma_otel_collection_endpoint="", chroma_otel_granularity=None,
        ),
    )


def collection_for(client: "ClientAPI", state: VectorState) -> "Collection":
    from chromadb.errors import NotFoundError

    try:
        collection = client.get_collection(state.collection, embedding_function=None)
    except NotFoundError as error:
        raise ValueError("Vector collection is missing. Run semantic-index --rebuild.") from error
    if (
        collection.metadata != asdict(state)
        or collection.count() != state.count
        or (collection.configuration_json.get("hnsw") or {}).get("space") != "cosine"
    ):
        raise ValueError("Vector collection metadata or count disagrees with its committed state. "
                         "Run semantic-index --rebuild.")
    return collection


@contextmanager
def repository_lock(directory: Path) -> Iterator[None]:
    """Serialize Chroma operations across CLI processes for this repository."""
    path = directory / "semantic.lock"
    try:
        _regular_file(path)
    except FileNotFoundError:
        pass
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Semantic index is busy. Retry after the other operation finishes.") from error
        # Closing the file releases the OS lock, even after failure.
        yield


@contextmanager
def verified_snapshot(
    root: Path, repository_id: str, *, capture_path: str | None = None,
    captured: list[SourceFile] | None = None,
) -> Iterator[tuple[Path, SourceSnapshot]]:
    """Hold the repository lock while operating on one verified source snapshot."""
    try:
        directory = index_directory(root, repository_id, create=False)
    except FileNotFoundError as error:
        raise ValueError("No semantic index for this checkout. Run semantic-index first.") from error
    with repository_lock(directory):
        source = load_source(directory, repository_id)
        entries = scan_repository(root, source.scan_limits)
        matched: list[SourceFile] = []

        def capture() -> Iterator[SourceFile | SkippedEntry]:
            for entry in entries:
                if isinstance(entry, SourceFile) and entry.relative_path == capture_path:
                    matched.append(entry)
                yield entry

        fingerprint, source_bytes = source_fingerprint_and_size(capture() if capture_path else entries)
        if fingerprint != source.source_sha256:
            raise ValueError("Semantic index is stale. Run semantic-index again.")
        if source_bytes != source.indexed_source_bytes:
            raise ValueError("Semantic manifest source size disagrees with the checkout. Run semantic-index again.")
        verify_chunk_file(directory, source)
        if captured is not None:
            captured.extend(matched)
        yield directory, source
