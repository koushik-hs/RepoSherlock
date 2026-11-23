"""Search one locked source snapshot using the selected retrieval method."""

from pathlib import Path

from reposherlock.ingestion.indexer import repository_identifier
from reposherlock.ingestion.scanner import validate_repository
from reposherlock.retrieval.evidence import assess_evidence
from reposherlock.retrieval.fusion import reciprocal_rank_fusion
from reposherlock.retrieval.lexical import BM25Index
from reposherlock.retrieval.models import (
    DEFAULT_CANDIDATES, DEFAULT_RRF_K, EVIDENCE_DEPTH, MODES,
    RankedChunk, RetrievalHit, RetrievalMode, RetrievalResponse,
)
from reposherlock.semantic.embeddings import Embeddings, MiniLMEmbeddings
from reposherlock.semantic.retrieval import search_vectors, validate_query
from reposherlock.semantic.storage import SourceSnapshot, read_chunks, verified_snapshot


class RetrievalService:
    def __init__(self, path: Path | str, embeddings: Embeddings | None = None) -> None:
        self.root = validate_repository(path)
        self.repository_id = repository_identifier(self.root)
        self.embeddings = embeddings
        self._lexical_cache: tuple[SourceSnapshot, BM25Index] | None = None

    def _lexical(self, directory: Path, source: SourceSnapshot) -> BM25Index:
        cache = self._lexical_cache
        if cache is None or cache[0] != source:
            cache = (source, BM25Index(read_chunks(directory, source)))
            self._lexical_cache = cache
        return cache[1]

    def search(
        self, repository_id: str, query: str, top_k: int = 5, *,
        mode: RetrievalMode = "semantic", candidate_k: int = DEFAULT_CANDIDATES,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> RetrievalResponse:
        query = validate_query(query, top_k)
        if repository_id != self.repository_id:
            raise ValueError("Repository ID does not match this checkout.")
        if mode not in MODES:
            raise ValueError("Search mode must be semantic, lexical, or hybrid.")
        if type(candidate_k) is not int or not 1 <= candidate_k <= 100:
            raise ValueError("Candidate depth must be between 1 and 100.")
        if type(rrf_k) is not int or rrf_k < 1:
            raise ValueError("RRF k must be a positive integer.")

        depth = max(top_k, EVIDENCE_DEPTH, candidate_k if mode == "hybrid" else 1)
        with verified_snapshot(self.root, repository_id) as (directory, source):
            semantic = None
            if mode in ("semantic", "hybrid"):
                if self.embeddings is None:
                    self.embeddings = MiniLMEmbeddings()
                semantic = search_vectors(directory, source, self.embeddings, query, depth)

            lexical = None
            if mode in ("lexical", "hybrid"):
                index = self._lexical(directory, source)
                if semantic is not None and any(
                    index.chunks.get(hit.chunk.chunk_id) != hit.chunk for hit in semantic
                ):
                    raise ValueError("Vector record disagrees with source JSONL. Run semantic-index --rebuild.")
                lexical = index.search(query, depth)

            if semantic is not None and lexical is not None:
                ranking = reciprocal_rank_fusion(semantic, lexical, k=rrf_k)
                shared = sum(hit.semantic_rank is not None and hit.bm25_rank is not None for hit in ranking)
            elif semantic is not None:
                expected = {hit.chunk.chunk_id: hit.chunk for hit in semantic}
                actual = {chunk.chunk_id: chunk for chunk in read_chunks(directory, source)
                          if chunk.chunk_id in expected} if expected else {}
                if actual != expected:
                    raise ValueError("Vector record disagrees with source JSONL. Run semantic-index --rebuild.")
                ranking = [
                    RankedChunk(hit.chunk, semantic_score=hit.score, semantic_rank=rank)
                    for rank, hit in enumerate(semantic, 1)
                ]
                shared = None
            else:
                ranking = [
                    RankedChunk(hit.chunk, bm25_score=hit.score, bm25_rank=rank)
                    for rank, hit in enumerate(lexical or [], 1)
                ]
                shared = None

            diagnostics = assess_evidence(
                query, ranking, semantic_candidates=None if semantic is None else len(semantic),
                lexical_candidates=None if lexical is None else len(lexical), shared_candidates=shared,
            )
            hits = tuple(
                RetrievalHit(**vars(hit), repository_id=repository_id, mode=mode, rank=rank)
                for rank, hit in enumerate(ranking[:top_k], 1)
            )
            return RetrievalResponse(
                repository_id, mode, hits, diagnostics, source.source_sha256, source.chunks_sha256,
                source.indexed_source_bytes,
            )
