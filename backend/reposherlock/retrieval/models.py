"""Structured rankings and evidence diagnostics."""

from dataclasses import dataclass
from typing import Literal

from reposherlock.ingestion.models import SemanticChunk

RetrievalMode = Literal["semantic", "lexical", "hybrid"]
MODES = ("semantic", "lexical", "hybrid")
DEFAULT_CANDIDATES = 20
DEFAULT_RRF_K = 60
EVIDENCE_DEPTH = 5


@dataclass(frozen=True)
class ScoredChunk:
    chunk: SemanticChunk
    score: float


@dataclass(frozen=True)
class RankedChunk:
    chunk: SemanticChunk
    semantic_score: float | None = None
    semantic_rank: int | None = None
    bm25_score: float | None = None
    bm25_rank: int | None = None
    rrf_score: float | None = None


@dataclass(frozen=True, kw_only=True)
class RetrievalHit(RankedChunk):
    repository_id: str
    mode: RetrievalMode
    rank: int


@dataclass(frozen=True)
class EvidenceDiagnostics:
    status: Literal["candidate_evidence", "no_convincing_evidence"]
    query_terms: tuple[str, ...]
    matched_terms: tuple[str, ...]
    exact_reference: bool
    semantic_candidates: int | None
    lexical_candidates: int | None
    shared_candidates: int | None


@dataclass(frozen=True)
class RetrievalResponse:
    repository_id: str
    mode: RetrievalMode
    hits: tuple[RetrievalHit, ...]
    diagnostics: EvidenceDiagnostics
    source_sha256: str = ""
    chunks_sha256: str = ""
    indexed_source_bytes: int = 0
