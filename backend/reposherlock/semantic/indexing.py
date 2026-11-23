"""Build a committed Chroma snapshot, reusing compatible vectors by input hash."""

import hashlib
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Literal
from uuid import uuid4

from chromadb import ClientAPI
from chromadb.api.models.Collection import Collection

from reposherlock.core.config import ChunkSettings, ScanLimits, SEMANTIC_CHUNK_SETTINGS
from reposherlock.ingestion.indexer import index_directory, index_repository, repository_identifier
from reposherlock.ingestion.models import SemanticChunk
from reposherlock.ingestion.scanner import validate_repository
from reposherlock.semantic.embeddings import (
    DEFAULT_BATCH_SIZE,
    Embeddings,
    MiniLMEmbeddings,
    checked_embeddings,
    code_embedding_text,
    validate_batch_size,
)
from reposherlock.semantic.storage import (
    SNAPSHOT_PREFIX,
    VectorState,
    collection_for,
    load_source,
    load_vector_state,
    open_database,
    publish,
    read_chunks,
    repository_lock,
)


@dataclass(frozen=True)
class IndexStats:
    repository_id: str
    total: int
    new: int
    reused: int
    updated: int
    removed: int
    embedded: int


@dataclass(frozen=True)
class IndexProgress:
    stage: Literal["scanning", "building_vectors", "publishing", "unchanged"]
    chunks_processed: int | None = None
    chunks_total: int | None = None


def _prune_collections(client: ClientAPI, keep: str | None) -> None:
    for collection in client.list_collections():
        if collection.name.startswith(SNAPSHOT_PREFIX) and collection.name != keep:
            client.delete_collection(collection.name)


def _batches(chunks: Iterator[SemanticChunk], size: int) -> Iterator[list[SemanticChunk]]:
    while batch := list(islice(chunks, size)):
        yield batch


def _copy_chunks(
    chunks: Iterator[SemanticChunk],
    target: Collection,
    previous: Collection | None,
    backend: Embeddings,
    batch_size: int,
    repository_id: str,
    progress: Callable[[int], None] | None = None,
) -> IndexStats:
    previous_slots: dict[str, tuple[str, int, int]] = {}
    previous_inputs: dict[str, str] = {}
    if previous is not None:
        for offset in range(0, previous.count(), 1000):
            page = previous.get(limit=1000, offset=offset, include=["metadatas"])
            for record_id, meta in zip(page["ids"], page["metadatas"], strict=True):
                previous_slots[record_id] = (
                    meta["relative_path"], meta["start_line"], meta["start_column"]
                )
                previous_inputs[meta["embedding_sha256"]] = record_id

    new_slots: Counter[tuple[str, int, int]] = Counter()
    reused = embedded = total = 0
    for batch in _batches(chunks, batch_size):
        inputs = [code_embedding_text(chunk) for chunk in batch]
        keys = [hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest() for text in inputs]
        cached: dict[str, list[float]] = {}
        lookup = {previous_inputs[key]: key for key in keys if key in previous_inputs}
        if lookup and previous is not None:
            records = previous.get(ids=list(lookup), include=["embeddings"])
            if set(records["ids"]) != set(lookup) or records["embeddings"] is None:
                raise ValueError("A reusable vector is missing. Run semantic-index --rebuild.")
            cached = {
                lookup[record_id]: vector.tolist()
                for record_id, vector in zip(records["ids"], records["embeddings"], strict=True)
            }
            if any(len(vector) != backend.dimension for vector in cached.values()):
                raise ValueError("Stored vector dimensions changed. Run semantic-index --rebuild.")

        missing = {key: text for key, text in zip(keys, inputs, strict=True) if key not in cached}
        vectors = checked_embeddings(backend, list(missing.values()))
        cached.update(zip(missing, vectors, strict=True))
        embedded += len(vectors)
        total += len(batch)

        metadata = []
        for chunk, key in zip(batch, keys, strict=True):
            if previous_slots.pop(chunk.chunk_id, None) is not None:
                reused += 1
            else:
                new_slots[(chunk.relative_path, chunk.start_line, chunk.start_column)] += 1
            meta = {
                "repository_id": repository_id,
                "relative_path": chunk.relative_path,
                "language": chunk.language,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "start_column": chunk.start_column,
                "end_column": chunk.end_column,
                "strategy": chunk.strategy,
                "embedding_sha256": key,
            }
            if chunk.symbol_name is not None:
                meta["symbol_name"] = chunk.symbol_name
            if chunk.symbol_type is not None:
                meta["symbol_type"] = chunk.symbol_type
            metadata.append(meta)
        target.add(
            ids=[chunk.chunk_id for chunk in batch],
            embeddings=[cached[key] for key in keys],
            documents=[chunk.content for chunk in batch],
            metadatas=metadata,
        )
        if progress is not None:
            progress(total)

    updated = (new_slots & Counter(previous_slots.values())).total()
    return IndexStats(
        repository_id, total, total - reused - updated, reused, updated,
        len(previous_slots) - updated, embedded,
    )


def build_semantic_index(
    path: Path | str,
    *,
    embeddings: Embeddings | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    scan_limits: ScanLimits = ScanLimits(),
    chunk_settings: ChunkSettings = SEMANTIC_CHUNK_SETTINGS,
    rebuild: bool = False,
    on_progress: Callable[[IndexProgress], None] | None = None,
) -> IndexStats:
    validate_batch_size(batch_size)
    backend = embeddings if embeddings is not None else MiniLMEmbeddings(batch_size)
    root = validate_repository(path)
    repository_id = repository_identifier(root)
    directory = index_directory(root, repository_id)
    with repository_lock(directory):
        previous_state = None if rebuild else load_vector_state(directory)
        if previous_state is not None:
            if previous_state.repository_id != repository_id or not previous_state.compatible(backend):
                raise ValueError("Embedding model or dimension is incompatible. Run semantic-index --rebuild.")
        client = open_database(directory, create=True)
        previous = collection_for(client, previous_state) if previous_state else None

        if on_progress is not None:
            on_progress(IndexProgress("scanning"))
        index_repository(root, scan_limits, chunk_settings, code_aware=True)
        source = load_source(directory, repository_id)
        if previous_state and previous_state.matches(source):
            _prune_collections(client, previous_state.collection)
            if on_progress is not None:
                on_progress(IndexProgress("unchanged", source.count, source.count))
            return IndexStats(repository_id, source.count, 0, source.count, 0, 0, 0)

        state = VectorState(
            repository_id, source.chunks_sha256, source.source_sha256, source.count,
            backend.identity, backend.dimension, SNAPSHOT_PREFIX + uuid4().hex,
        )
        if not rebuild:
            _prune_collections(client, previous_state.collection if previous_state else None)
        target = client.create_collection(
            state.collection, embedding_function=None, metadata=asdict(state),
            configuration={"hnsw": {"space": "cosine"}},
        )
        def progress(processed: int) -> None:
            if on_progress is not None:
                on_progress(IndexProgress("building_vectors", processed, source.count))

        progress(0)
        stats = _copy_chunks(
            read_chunks(directory, source), target, previous, backend, batch_size, repository_id,
            progress if on_progress is not None else None,
        )
        if target.count() != source.count:
            raise ValueError("Chroma record count does not match the semantic source index.")
        # An interrupted build leaves an unreferenced collection for the next
        # build to prune. Only the atomic pointer swap makes it active.
        if on_progress is not None:
            on_progress(IndexProgress("publishing", source.count, source.count))
        publish(directory, state)
        _prune_collections(client, state.collection)
        return stats
