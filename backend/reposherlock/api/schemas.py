"""Typed browser requests, job state, and repository responses."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from reposherlock.investigation.agent import AGENT_CONTEXT_CHARS, AgentLimits
from reposherlock.investigation.context import DEFAULT_CONTEXT_CHARS, DEFAULT_MAX_SOURCES, MAX_ISSUE_CHARS, ContextLimits
from reposherlock.investigation.report import InvestigationDocument
from reposherlock.investigation.trace import CumulativeUsage, InvestigationTrace
from reposherlock.retrieval.models import EvidenceDiagnostics, RetrievalMode

RepositoryId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
RecordId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
Strategy = Literal["single", "agentic"]
SnapshotStatus = Literal["not_indexed", "ready", "stale", "invalid", "busy"]
JobState = Literal["queued", "running", "completed", "failed"]


class Input(BaseModel, strict=True, extra="forbid", frozen=True):
    pass


class Problem(BaseModel):
    code: str
    message: str


class ProblemResponse(BaseModel):
    detail: Problem


class RepositoryRequest(Input):
    path: str = Field(min_length=1, max_length=2048)


class Snapshot(BaseModel):
    source_sha256: str
    chunks_sha256: str
    indexed_chunks: int | None = None

    @property
    def digests(self) -> tuple[str, str]:
        return self.source_sha256, self.chunks_sha256


class RepositoryResponse(BaseModel):
    repository_id: str
    display_name: str
    path: str
    index_available: bool | None
    snapshot_status: SnapshotStatus
    snapshot: Snapshot | None = None


class IndexRequest(Input):
    rebuild: bool = False


class IndexProgressResponse(BaseModel):
    stage: Literal["scanning", "building_vectors", "publishing", "unchanged"]
    chunks_processed: int | None = None
    chunks_total: int | None = None


class IndexResultResponse(BaseModel):
    repository_id: str
    total: int
    new: int
    reused: int
    updated: int
    removed: int
    embedded: int


class SearchRequest(Input):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    mode: RetrievalMode = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)


class SourceSpan(BaseModel):
    file_path: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int
    end_line: int
    snippet: str


class SearchHit(SourceSpan):
    chunk_id: str
    rank: int
    language: str
    snippet_truncated: bool
    semantic_score: float | None
    semantic_rank: int | None
    bm25_score: float | None
    bm25_rank: int | None
    rrf_score: float | None


class SearchResponse(BaseModel):
    repository_id: str
    mode: RetrievalMode
    snapshot: Snapshot
    duration_ms: float
    hits: list[SearchHit]
    diagnostics: EvidenceDiagnostics


class AgentOptions(Input):
    max_rounds: int = Field(default=3, ge=0, le=6)
    max_tool_calls: int = Field(default=8, ge=0, le=16)
    max_calls_per_round: int = Field(default=4, ge=1, le=4)
    max_read_lines: int = Field(default=150, ge=1, le=150)
    max_model_requests: int = Field(default=5, ge=1, le=8)


class InvestigationConfig(Input):
    context_chars: int | None = Field(default=None, ge=512, le=32000)
    max_sources: int = Field(default=DEFAULT_MAX_SOURCES, ge=1, le=12)
    agent: AgentOptions | None = None

    def effective(self, strategy: Strategy) -> "InvestigationConfig":
        limit = DEFAULT_CONTEXT_CHARS if strategy == "single" else AGENT_CONTEXT_CHARS
        return self.model_copy(update={"context_chars": self.context_chars or limit,
                                       "agent": (self.agent or AgentOptions()) if strategy == "agentic" else None})

    def context_limits(self) -> ContextLimits:
        return ContextLimits(self.context_chars or DEFAULT_CONTEXT_CHARS, self.max_sources)

    def agent_limits(self) -> AgentLimits:
        return AgentLimits(**self.agent.model_dump()) if self.agent else AgentLimits()


class StrategyInput(Input):
    strategy: Strategy
    configuration: InvestigationConfig

    @model_validator(mode="after")
    def check_strategy(self):
        if self.strategy == "single" and self.configuration.agent is not None:
            raise ValueError("Agent options require the agentic strategy.")
        return self


class PreviewRequest(StrategyInput):
    configuration: InvestigationConfig = InvestigationConfig()
    repository_id: RepositoryId
    issue: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ISSUE_CHARS)]


class PreviewSource(SourceSpan):
    source_id: str


class PreviewResponse(BaseModel):
    preview_id: RecordId
    expires_at: datetime
    repository_id: RepositoryId
    snapshot: Snapshot
    strategy: Strategy
    configuration: InvestigationConfig
    model: str
    issue: str
    sources: list[PreviewSource]
    source_chars: int
    source_bytes: int
    context_chars: int
    additional_disclosure_possible: bool
    disclosure_notice: str
    diagnostics: EvidenceDiagnostics


class ApprovalRequest(StrategyInput):
    preview_id: RecordId
    allow_remote_code: bool


class JobAccepted(BaseModel):
    job_id: RecordId
    state: Literal["queued"] = "queued"


class InvestigationAccepted(JobAccepted):
    investigation_id: RecordId


class JobResponse(BaseModel):
    job_id: RecordId
    repository_id: RepositoryId
    kind: Literal["index", "investigation"]
    state: JobState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: Problem | None = None
    progress: IndexProgressResponse | None = None
    index_result: IndexResultResponse | None = None


class InvestigationMetrics(BaseModel):
    duration_ms: float
    initial_retrieval_ms: float
    retrieval_operations: int
    model_request_count: int
    tool_call_count: int
    usage_summary: CumulativeUsage
    valid_citations: int | None
    invalid_citations: int | None


class InvestigationResponse(BaseModel):
    investigation_id: RecordId
    repository_id: RepositoryId
    snapshot: Snapshot
    state: JobState
    issue_summary: str
    strategy: Strategy
    model: str
    error: Problem | None = None
    report: InvestigationDocument | None = None
    diagnostics: EvidenceDiagnostics | None = None
    stop_reason: str | None = None
    metrics: InvestigationMetrics | None = None


class TraceResponse(BaseModel):
    investigation_id: RecordId
    state: JobState
    trace: InvestigationTrace | None = None
