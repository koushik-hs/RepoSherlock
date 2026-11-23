"""Reciprocal rank fusion for ranked lists with incomparable score scales."""

from collections.abc import Sequence
from dataclasses import replace

from reposherlock.retrieval.models import DEFAULT_RRF_K, RankedChunk, ScoredChunk


def reciprocal_rank_fusion(
    semantic: Sequence[ScoredChunk], lexical: Sequence[ScoredChunk], *, k: int = DEFAULT_RRF_K
) -> list[RankedChunk]:
    if type(k) is not int or k < 1:
        raise ValueError("RRF k must be a positive integer.")

    merged: dict[str, RankedChunk] = {}
    for rank, hit in enumerate(semantic, start=1):
        merged.setdefault(hit.chunk.chunk_id, RankedChunk(
            hit.chunk, semantic_score=hit.score, semantic_rank=rank, rrf_score=1 / (k + rank),
        ))
    for rank, hit in enumerate(lexical, start=1):
        existing = merged.get(hit.chunk.chunk_id)
        if existing is None:
            merged[hit.chunk.chunk_id] = RankedChunk(
                hit.chunk, bm25_score=hit.score, bm25_rank=rank, rrf_score=1 / (k + rank),
            )
        elif existing.bm25_rank is None:
            merged[hit.chunk.chunk_id] = replace(
                existing, bm25_score=hit.score, bm25_rank=rank,
                rrf_score=(existing.rrf_score or 0.0) + 1 / (k + rank),
            )
    return sorted(merged.values(), key=lambda hit: (-(hit.rrf_score or 0.0), hit.chunk.chunk_id))
