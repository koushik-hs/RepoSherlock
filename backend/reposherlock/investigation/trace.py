"""Metadata-only investigation traces."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from reposherlock.ingestion.scanner import validate_repository
from reposherlock.investigation.context import InvestigationContext
from reposherlock.investigation.provider import TokenUsage


@dataclass(frozen=True)
class ToolStep:
    round: int
    sequence: int
    name: str
    parameters: dict | None
    status: str
    evidence_ids: tuple[str, ...]
    duration_ms: float
    new_evidence_ids: tuple[str, ...] = ()
    returned_range: dict[str, int] | None = None
    truncated: bool | None = None


@dataclass(frozen=True)
class ModelStep:
    kind: Literal["plan", "report"]
    status: str
    duration_ms: float
    source_chars: int
    usage: TokenUsage | None = None
    invalid_citations: int | None = None
    valid_citations: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ReportedTokens:
    tokens: int | None
    reported_requests: int
    status: Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True)
class CumulativeUsage:
    request_count: int
    input: ReportedTokens
    output: ReportedTokens
    total: ReportedTokens


def _cumulative_usage(requests: tuple[ModelStep, ...]) -> CumulativeUsage:
    count = len(requests)

    def tally(field: str) -> ReportedTokens:
        values = [getattr(step.usage, field) for step in requests if step.usage is not None]
        reported = [value for value in values if value is not None]
        if not reported:
            return ReportedTokens(None, 0, "unavailable")
        return ReportedTokens(sum(reported), len(reported), "complete" if len(reported) == count else "partial")

    return CumulativeUsage(count, tally("input_tokens"), tally("output_tokens"), tally("total_tokens"))


@dataclass(frozen=True)
class InvestigationTrace:
    strategy: Literal["single", "agentic"]
    model: str | None
    stop_reason: str
    duration_ms: float
    retrieval_operations: int
    initial_retrieval_ms: float
    orchestration_ms: float
    selected_source_chars: int
    logical_source_chars_sent: int
    context_chars: int
    evidence: tuple[dict, ...]
    tools: tuple[ToolStep, ...]
    model_requests: tuple[ModelStep, ...]
    usage_summary: CumulativeUsage


def build_trace(
    strategy: Literal["single", "agentic"], model: str | None, stop_reason: str,
    context: InvestigationContext, duration_ms: float, initial_retrieval_ms: float,
    *, retrieval_operations: int = 1, tools: tuple[ToolStep, ...] = (),
    requests: tuple[ModelStep, ...] = (),
) -> InvestigationTrace:
    evidence = tuple({
        "source_id": source.source_id, "file_path": source.file_path,
        "symbol_name": source.symbol_name, "start_line": source.start_line, "end_line": source.end_line,
    } for source in context.sources)
    overhead = duration_ms - initial_retrieval_ms - sum(step.duration_ms for step in (*tools, *requests))
    return InvestigationTrace(
        strategy, model, stop_reason, round(duration_ms, 3), retrieval_operations,
        round(initial_retrieval_ms, 3), round(max(0.0, overhead), 3),
        sum(len(source.snippet) for source in context.sources),
        sum(step.source_chars for step in requests), len(context.payload), evidence, tools, requests,
        _cumulative_usage(requests),
    )


def validate_trace_destination(path: Path, repository: Path) -> None:
    """Validate the destination before provider work; actual writes are exclusive."""
    root = validate_repository(repository)
    destination = path.parent.resolve(strict=True) / path.name
    if destination.is_relative_to(root) and not destination.is_relative_to(root / ".reposherlock"):
        raise ValueError("Place trace output outside the source tree or under its .reposherlock directory.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Trace output already exists. Choose a new path.")


def save_trace(path: Path, repository: Path, trace: InvestigationTrace) -> None:
    validate_trace_destination(path, repository)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(asdict(trace), stream, indent=2)
        stream.write("\n")
