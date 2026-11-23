"""Retrieve source chunks ranked by cosine similarity."""

from dataclasses import dataclass
from pathlib import Path

from reposherlock.ingestion.indexer import repository_identifier
from reposherlock.ingestion.models import SemanticChunk
from reposherlock.ingestion.scanner import validate_repository
from reposherlock.retrieval.models import ScoredChunk
from reposherlock.semantic.embeddings import Embeddings, MiniLMEmbeddings, checked_embeddings
from reposherlock.semantic.storage import (
    SourceSnapshot,
    collection_for,
    load_vector_state,
    open_database,
    verified_snapshot,
)


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    relative_path: str
    language: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    symbol_name: str | None
    symbol_type: str | None
    strategy: str
    snippet: str
    score: float


def validate_query(query: str, top_k: int) -> str:
    query = query.strip()
    if not query:
        raise ValueError("Search query must not be empty.")
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("Top-K must be between 1 and 100.")
    return query


def search_vectors(
    directory: Path, source: SourceSnapshot, embeddings: Embeddings, query: str, top_k: int,
) -> list[ScoredChunk]:
    """Query a committed vector collection for the caller's locked snapshot."""
    state = load_vector_state(directory)
    if state is None:
        raise ValueError("No committed vector index. Run semantic-index first.")
    if not state.compatible(embeddings):
        raise ValueError("Embedding model or dimension is incompatible. Run semantic-index --rebuild.")
    if not state.matches(source):
        raise ValueError("Semantic index is stale. Run semantic-index again.")
    try:
        client = open_database(directory)
    except FileNotFoundError as error:
        raise ValueError("Vector database is missing. Run semantic-index --rebuild.") from error
    collection = collection_for(client, state)
    if state.count == 0:
        return []

    vector = checked_embeddings(embeddings, [query])
    results = collection.query(
        query_embeddings=vector, n_results=min(top_k, state.count),
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for chunk_id, meta, document, distance in zip(
        results["ids"][0], results["metadatas"][0],
        results["documents"][0], results["distances"][0], strict=True,
    ):
        if meta.get("repository_id") != source.repository_id:
            raise ValueError("Vector record belongs to a different repository. Run semantic-index --rebuild.")
        chunk = SemanticChunk(
            chunk_id=chunk_id, relative_path=meta["relative_path"], language=meta["language"],
            start_line=meta["start_line"], end_line=meta["end_line"], content=document,
            start_column=meta["start_column"], end_column=meta["end_column"],
            strategy=meta["strategy"], symbol_name=meta.get("symbol_name"),
            symbol_type=meta.get("symbol_type"),
        )
        hits.append(ScoredChunk(chunk, 1.0 - distance))
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))


class SemanticSearch:
    def __init__(self, path: Path | str, embeddings: Embeddings | None = None) -> None:
        self.root = validate_repository(path)
        self.repository_id = repository_identifier(self.root)
        self.embeddings = embeddings if embeddings is not None else MiniLMEmbeddings()

    def search(self, repository_id: str, query: str, top_k: int = 5) -> list[SearchHit]:
        query = validate_query(query, top_k)
        if repository_id != self.repository_id:
            raise ValueError("Repository ID does not match this checkout.")

        with verified_snapshot(self.root, repository_id) as (directory, source):
            return [
                SearchHit(
                    chunk.chunk_id, chunk.relative_path, chunk.language, chunk.start_line,
                    chunk.end_line, chunk.start_column, chunk.end_column,
                    chunk.symbol_name, chunk.symbol_type, chunk.strategy, chunk.content, hit.score,
                )
                for hit in search_vectors(directory, source, self.embeddings, query, top_k)
                for chunk in (hit.chunk,)
            ]
