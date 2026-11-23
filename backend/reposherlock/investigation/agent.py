"""Bounded planning, local evidence tools, and a separate grounded report request."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from reposherlock.investigation.agent_provider import (
    AgenticProvider, GeminiAgentProvider, InvalidPlanningResponse, PlanningTurn, ToolExchange, ToolRequest,
)
from reposherlock.investigation.context import ContextLimits, EvidenceRegistry, validate_issue
from reposherlock.investigation.provider import ProviderError, ProviderReply, require_remote_permission
from reposherlock.investigation.redaction import redact
from reposherlock.investigation.report import InvestigationDocument, InvalidInvestigation, provider_schema, validate_report
from reposherlock.investigation.service import (
    InvestigationResult, SYSTEM_INSTRUCTION, SnapshotChanged, _check_snapshot, check_initial_context,
    guard_gemini_snapshot,
)
from reposherlock.investigation.tools import TOOL_MODELS, SourceTools, ToolRejected, validate_tool
from reposherlock.investigation.trace import ModelStep, ToolStep, build_trace
from reposherlock.retrieval.models import RetrievalResponse
from reposherlock.retrieval.service import RetrievalService
from reposherlock.semantic.embeddings import Embeddings

AGENT_CONTEXT_CHARS = 24_000
MAX_EXCERPTS = 32
MAX_IDLE_ROUNDS = 2


@dataclass(frozen=True)
class AgentLimits:
    max_rounds: int = 3
    max_tool_calls: int = 8
    max_calls_per_round: int = 4
    max_read_lines: int = 150
    max_model_requests: int = 5

    def __post_init__(self) -> None:
        for name, minimum, maximum in (
            ("max_rounds", 0, 6), ("max_tool_calls", 0, 16), ("max_calls_per_round", 1, 4),
            ("max_read_lines", 1, 150), ("max_model_requests", 1, 8),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name.replace('_', '-')} must be between {minimum} and {maximum}.")


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _source_chars(registry: EvidenceRegistry) -> int:
    return sum(len(source.snippet) for source in registry.sources)


class _ToolSession:
    def __init__(self, tools: SourceTools, registry: EvidenceRegistry) -> None:
        self.tools = tools
        self.registry = registry
        self.steps: list[ToolStep] = []
        self.seen: dict[str, dict] = {}

    def execute(self, request: ToolRequest, round_number: int) -> dict:
        started = perf_counter()
        name = request.name if request.name in TOOL_MODELS else "<unregistered>"
        parameters = None
        signature = None
        try:
            arguments = validate_tool(name, request.arguments)
            values = arguments.model_dump()
            parameters = {key: redact(value) if isinstance(value, str) else value for key, value in values.items()}
            if name == "search_code":
                # Search text can contain a verbatim source excerpt.
                parameters["query"] = {"characters": len(values["query"]), "text_omitted": True}
            signature = json.dumps([name, values], sort_keys=True, ensure_ascii=True)
            cached = self.seen.get(signature)
            if cached is not None:
                result = {**cached, "status": "duplicate", "cached_status": cached["status"],
                          "new_evidence_ids": (), "sources": []}
            else:
                before = len(self.registry.sources)
                matches = self.tools.dispatch(arguments)
                registered = {}
                for chunk in matches.chunks:
                    if len(self.registry.sources) >= MAX_EXCERPTS:
                        break
                    evidence = self.registry.register(chunk, allow_prefix=False, merge_overlaps=True)
                    if evidence is not None:
                        registered[evidence.source_id] = evidence
                fresh = self.registry.sources[before:]
                result = {
                    "status": "ok" if fresh else ("no_new_evidence" if matches.chunks else "no_match"),
                    "evidence_ids": tuple(registered),
                    "new_evidence_ids": tuple(source.source_id for source in fresh),
                    "sources": [source.outbound() for source in fresh],
                    "ambiguous": matches.ambiguous, "truncated": matches.truncated,
                }
                if matches.returned_range is not None:
                    start_line, end_line = matches.returned_range
                    result["returned_range"] = {"start_line": start_line, "end_line": end_line}
        except ToolRejected as error:
            result = {"status": str(error)}
        if signature is not None and result["status"] != "duplicate":
            self.seen[signature] = {
                key: value for key, value in result.items() if key not in ("sources", "new_evidence_ids")
            }
        self.steps.append(ToolStep(
            round_number, len(self.steps) + 1, name, parameters, result["status"],
            result.get("evidence_ids", ()), _elapsed(started), result.get("new_evidence_ids", ()),
            result.get("returned_range"), result.get("truncated"),
        ))
        return result


def _gather(
    provider: AgenticProvider, session: _ToolSession, limits: AgentLimits, requests: list[ModelStep],
    *, consent: bool, max_context: int,
) -> str:
    initial = session.registry.payload
    wire_chars = len(initial)
    history: list[ToolExchange] = []
    idle_rounds = 0
    # Reserve one logical request for validated final generation.
    for round_number in range(1, limits.max_rounds + 1):
        if len(requests) + 1 >= limits.max_model_requests:
            return "model_request_limit"
        if len(session.steps) >= limits.max_tool_calls:
            return "tool_call_limit"
        if wire_chars > max_context + 6000:
            return "conversation_limit"
        if history:
            _check_snapshot(session.tools.retriever, session.tools.baseline)
        started = perf_counter()
        source_chars = _source_chars(session.registry)
        try:
            turn = provider.plan(initial, tuple(history), allow_remote_code=consent)
            if not isinstance(turn, PlanningTurn) or any(
                not isinstance(call, ToolRequest) or not isinstance(call.name, str) for call in turn.calls
            ):
                raise InvalidPlanningResponse("Invalid planning response.", usage=getattr(turn, "usage", None))
        except InvalidPlanningResponse as error:
            requests.append(ModelStep("plan", "invalid", _elapsed(started), source_chars, error.usage))
            return "invalid_planning_response"
        except ProviderError as error:
            requests.append(ModelStep("plan", "failed", _elapsed(started), source_chars, error.usage,
                                      error_code=error.code))
            return "snapshot_changed" if isinstance(error, SnapshotChanged) else "planner_provider_failure"
        requests.append(ModelStep("plan", "ok", _elapsed(started), source_chars, turn.usage))
        if not turn.calls:
            return "model_ready"
        available = limits.max_tool_calls - len(session.steps)
        allowed = min(available, limits.max_calls_per_round)
        results = tuple(session.execute(call, round_number) for call in turn.calls[:allowed])
        if len(turn.calls) > allowed:
            return "tool_call_limit" if available <= limits.max_calls_per_round else "per_round_tool_limit"
        if all(result["status"] == "duplicate" for result in results):
            return "repeated_calls"
        if len(session.steps) >= limits.max_tool_calls:
            return "tool_call_limit"
        idle_rounds = 0 if any(result.get("new_evidence_ids") for result in results) else idle_rounds + 1
        if idle_rounds >= MAX_IDLE_ROUNDS and round_number < limits.max_rounds:
            return "no_progress"
        # Preserve complete assistant/tool pairs for any subsequent planner request.
        history.append(ToolExchange(turn, results))
        wire_chars += turn.replay_chars + len(json.dumps(results, ensure_ascii=True))
    return "round_limit"


def investigate_agentic(
    path: Path | str, issue: str, *, dry_run: bool = False, allow_remote_code: bool = False,
    context_limits: ContextLimits = ContextLimits(max_chars=AGENT_CONTEXT_CHARS),
    limits: AgentLimits = AgentLimits(), provider: AgenticProvider | None = None,
    embeddings: Embeddings | None = None, expected_snapshot: tuple[str, str] | None = None,
    on_retrieval: Callable[[RetrievalResponse], None] | None = None,
    expected_context: str | None = None,
    resolve_repository: Callable[[], Path] | None = None,
) -> InvestigationResult:
    started = perf_counter()
    issue = validate_issue(issue)
    if not dry_run:
        require_remote_permission(allow_remote_code)
        if provider is None:
            provider = GeminiAgentProvider.from_environment()

    retriever = RetrievalService(path, embeddings)
    baseline = retriever.search(retriever.repository_id, issue, context_limits.max_sources * 3, mode="hybrid")
    retrieval_ms = _elapsed(started)
    registry = EvidenceRegistry(issue, context_limits, baseline.indexed_source_bytes)
    initial = registry.seed(baseline.hits)
    _check_snapshot(retriever, baseline, expected_snapshot)
    check_initial_context(initial, expected_context)
    if on_retrieval is not None:
        on_retrieval(baseline)

    tools = SourceTools(retriever, baseline, limits.max_read_lines)
    session = _ToolSession(tools, registry)
    requests: list[ModelStep] = []
    model = redact(provider.model) if provider is not None and not dry_run else None
    document = reply = None
    if dry_run:
        stop = "dry_run"
    elif not registry.sources:
        stop = "no_evidence"
    else:
        assert provider is not None
        if isinstance(provider, GeminiAgentProvider):
            provider = GeminiAgentProvider(guard_gemini_snapshot(provider.base, retriever, baseline, resolve_repository))
        stop = _gather(provider, session, limits, requests, consent=allow_remote_code,
                       max_context=context_limits.max_chars)
        if stop != "snapshot_changed":
            _check_snapshot(retriever, baseline)
        if stop not in ("planner_provider_failure", "snapshot_changed"):
            document, reply, failure = _final_report(
                provider, registry, requests, consent=allow_remote_code,
            )
            stop = failure or stop
            if stop != "snapshot_changed":
                _check_snapshot(retriever, baseline)

    context = registry.context()
    if not dry_run and document is None:
        document = InvestigationDocument(
            status="insufficient_evidence", problem_summary=context.issue[:2000].strip(),
            observations=[], hypotheses=[], uncertainties=[],
            missing_evidence=["No validated report was available for this investigation."],
        )
    trace = build_trace(
        "agentic", model, stop, context, _elapsed(started), retrieval_ms,
        retrieval_operations=1 + tools.retrievals, tools=tuple(session.steps), requests=tuple(requests),
    )
    if stop == "snapshot_changed":
        error = SnapshotChanged()
        error.trace = trace
        raise error
    return InvestigationResult(
        "dry_run" if document is None else document.status, context, baseline.diagnostics,
        document, reply.provider if reply else None, reply.model if reply else None,
        reply.usage if reply else None, trace,
    )


def _final_report(
    provider: AgenticProvider, registry: EvidenceRegistry, requests: list[ModelStep], *, consent: bool,
) -> tuple[InvestigationDocument | None, ProviderReply | None, str | None]:
    started = perf_counter()
    chars = _source_chars(registry)
    try:
        reply = provider.generate(
            registry.payload, instruction=SYSTEM_INSTRUCTION, schema=provider_schema(), allow_remote_code=consent,
        )
    except ProviderError as error:
        requests.append(ModelStep("report", "failed", _elapsed(started), chars, error.usage,
                                  error_code=error.code))
        return None, None, "snapshot_changed" if isinstance(error, SnapshotChanged) else "report_provider_failure"
    try:
        document = validate_report(reply.text, tuple(registry.sources))
    except InvalidInvestigation as error:
        requests.append(ModelStep("report", "invalid", _elapsed(started), chars, reply.usage,
                                  error.invalid_citations, error.valid_citations))
        return None, None, "invalid_report"
    requests.append(ModelStep("report", "ok", _elapsed(started), chars, reply.usage))
    return document, reply, None
