"""Run paired investigations against immutable, locally prepared fixture snapshots."""

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from reposherlock.benchmarking.dataset import Case, load_dataset, resolve_fixture, review_truth
from reposherlock.benchmarking.metrics import FAILURE_STOPS, evaluate
from reposherlock.benchmarking.mock import ScriptedProvider
from reposherlock.ingestion.fingerprint import source_fingerprint
from reposherlock.ingestion.models import SourceFile
from reposherlock.ingestion.scanner import is_link, scan_repository, validate_repository
from reposherlock.investigation.agent import AgentLimits, investigate_agentic
from reposherlock.investigation.agent_provider import GeminiAgentProvider
from reposherlock.investigation.context import ContextLimits
from reposherlock.investigation.provider import MAX_ATTEMPTS, GeminiProvider, ProviderError, require_remote_permission
from reposherlock.investigation.redaction import redact
from reposherlock.investigation.report import InvalidInvestigation
from reposherlock.investigation.service import SnapshotChanged, investigate
from reposherlock.retrieval.models import DEFAULT_CANDIDATES, DEFAULT_RRF_K, RetrievalResponse
from reposherlock.retrieval.service import RetrievalService
from reposherlock.semantic.embeddings import Embeddings, MiniLMEmbeddings
from reposherlock.semantic.indexing import build_semantic_index
from reposherlock.semantic.storage import read_chunks, repository_lock, verified_snapshot

RESULTS_NAME = "results.json"
STRATEGIES = ("single", "agentic")
RUN_VERSION = 1
DEFAULT_LIVE_BUDGET = 9


@dataclass(frozen=True)
class Options:
    dataset: Path
    fixtures: Path
    output: Path
    provider: str = "mock"
    strategy: str = "both"
    max_cases: int | None = None
    request_budget: int | None = None
    allow_remote_code: bool = False
    resume: bool = False
    case_ids: tuple[str, ...] = ()
    context: ContextLimits = ContextLimits()
    agent: AgentLimits = AgentLimits()


class BudgetExhausted(ProviderError):
    """No further Gemini attempts were authorized."""


def atomic_write(path: Path, contents: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _serialize(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def _read_results(path: Path) -> dict:
    if is_link(path.lstat()) or not path.is_file():
        raise ValueError("Expected a regular benchmark results file.")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state["version"] != RUN_VERSION or type(state["requests_used"]) is not int
                or state["requests_used"] < 0):
            raise ValueError
        return state
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid or unsupported benchmark checkpoint.") from None


def _fixture_snapshot(source: Path, staged: Path, cases: list[Case], backend: Embeddings,
                      known: dict | None) -> dict:
    entries = [item for item in scan_repository(source) if isinstance(item, SourceFile)]
    input_digest = source_fingerprint(entries)
    if known and input_digest != known["source_sha256"]:
        raise ValueError("The original fixture changed since this benchmark started.")
    if not known:
        staged.mkdir(exist_ok=True)
        validate_repository(staged)
        for entry in entries:
            destination = staged / entry.relative_path
            for parent in reversed(destination.parents):
                if parent != staged and parent.is_relative_to(staged):
                    parent.mkdir(exist_ok=True)
                    validate_repository(parent)
            if destination.exists() or destination.is_symlink():
                if is_link(destination.lstat()) or not destination.is_file():
                    raise ValueError("Invalid file in the fixture snapshot staging directory.")
                continue
            with destination.open("xb") as stream:
                stream.write(entry.content.encode("utf-8"))
        if source_fingerprint(scan_repository(staged)) != input_digest:
            raise ValueError("Staged source does not match the original fixture.")
        build_semantic_index(staged, embeddings=backend)
    service = RetrievalService(staged, backend)
    with verified_snapshot(service.root, service.repository_id) as (directory, snapshot):
        record = {field: getattr(snapshot, field) for field in ("repository_id", "source_sha256", "chunks_sha256")}
        if record["source_sha256"] != input_digest or (known and record != known):
            raise ValueError("The benchmark fixture snapshot does not match the checkpoint.")
        pending = {label for case in cases for label in case.locations}
        for chunk in read_chunks(directory, snapshot):
            excerpt = {"file_path": chunk.relative_path, "symbol_name": chunk.symbol_name,
                       "start_line": chunk.start_line, "end_line": chunk.end_line}
            pending = {label for label in pending if not label.covers(excerpt, require_symbol=True)}
        if pending:
            labels = ", ".join(sorted(f"{redact(label.path)}:{label.start_line}–{label.end_line}"
                                      for label in pending))
            raise ValueError(f"Benchmark ground truth is missing from the source index: {labels}.")
    return record


def _safe_reference(hit) -> dict:
    chunk = hit.chunk
    return {"rank": hit.rank, "file_path": redact(chunk.relative_path),
            "symbol_name": redact(chunk.symbol_name) if chunk.symbol_name else None,
            "start_line": chunk.start_line, "end_line": chunk.end_line}


def _run_case(case: Case, strategy: str, root: Path, snapshot: dict, backend: Embeddings,
              options: Options, provider, state: dict, budget: int,
              budget_denied: Callable[[], bool]) -> dict:
    started = perf_counter()
    start_requests = state["requests_used"]
    retrieved = None

    def capture(response: RetrievalResponse) -> None:
        nonlocal retrieved
        retrieved = [_safe_reference(hit) for hit in response.hits]

    common = dict(allow_remote_code=True, embeddings=backend,
                  expected_snapshot=(snapshot["source_sha256"], snapshot["chunks_sha256"]),
                  on_retrieval=capture)
    result = document = trace = None
    status, stop = "failed", "local_investigation_failure"
    try:
        if strategy == "single":
            result = investigate(root, case.issue, limits=options.context, provider=provider, **common)
        else:
            agent = GeminiAgentProvider(provider) if options.provider == "gemini" else provider
            result = investigate_agentic(root, case.issue, context_limits=options.context,
                                         limits=options.agent, provider=agent, **common)
        document, trace = result.document, result.trace
        if trace is not None:
            stop = trace.stop_reason
            status = "failed" if stop in FAILURE_STOPS else "complete"
    except SnapshotChanged as error:
        trace = getattr(error, "trace", None)
        stop = "snapshot_changed"
    except (ProviderError, InvalidInvestigation) as error:
        trace = getattr(error, "trace", None)
        stop = trace.stop_reason if trace else "provider_failure"
    except (OSError, ValueError, RuntimeError):
        # Local exceptions can contain paths or source text. Keep the row resumable.
        pass
    if budget_denied():
        status, stop = "budget_exhausted", "physical_request_budget"
    attempts = state["requests_used"] - start_requests if options.provider == "gemini" else None
    data = evaluate(case, document, trace, state=status, depth=options.context.max_sources,
                    provider=options.provider, attempts=attempts, retrieved=retrieved)
    return {
        "case_id": case.id, "strategy": strategy, "model": redact(provider.model), "provider": options.provider,
        "snapshot": snapshot, "stop_reason": stop, "request_budget": budget if options.provider == "gemini" else None,
        "elapsed_ms": round((perf_counter() - started) * 1000, 3), **data,
    }


def run(options: Options, *, embeddings: Embeddings | None = None,
        mock_factory=ScriptedProvider, announce: Callable[[str], None] = print) -> dict:
    if options.provider not in ("mock", "gemini") or options.strategy not in (*STRATEGIES, "both"):
        raise ValueError("Invalid benchmark provider or strategy.")
    live = options.provider == "gemini"
    if live:
        require_remote_permission(options.allow_remote_code)
    elif options.request_budget is not None:
        raise ValueError("The physical request budget applies to Gemini runs only.")

    dataset = load_dataset(options.dataset)
    unknown = set(options.case_ids) - {case.id for case in dataset.cases}
    if unknown or len(set(options.case_ids)) != len(options.case_ids):
        raise ValueError("Unknown or duplicate selected benchmark case ID.")
    available = [case for case in dataset.cases if not options.case_ids or case.id in options.case_ids]
    maximum = options.max_cases if options.max_cases is not None else (1 if live else len(available))
    if type(maximum) is not int or not 1 <= maximum <= 100:
        raise ValueError("The maximum case count must be between 1 and 100.")
    cases = available[:maximum]
    strategies = STRATEGIES if options.strategy == "both" else (options.strategy,)
    estimated = len(cases) * sum(
        MAX_ATTEMPTS + (options.agent.max_rounds if strategy == "agentic" else 0) for strategy in strategies
    ) if live else 0
    budget = options.request_budget if options.request_budget is not None else (DEFAULT_LIVE_BUDGET if live else 0)
    if live and (type(budget) is not int or not 1 <= budget <= 1000):
        raise ValueError("The Gemini request budget must be between 1 and 1000.")

    base = GeminiProvider.from_environment() if live else mock_factory()
    secret = base.api_key if live else None
    fixture_root = validate_repository(options.fixtures)
    roots = {case.fixture: resolve_fixture(fixture_root, case.fixture) for case in cases}
    output = options.output
    if output.is_symlink() or (output.exists() and is_link(output.lstat())):
        raise ValueError("The benchmark output must be a regular directory.")
    output = output.resolve()
    if output.is_relative_to(fixture_root) or fixture_root.is_relative_to(output):
        raise ValueError("Use a dedicated output directory outside the original fixtures.")
    backend = embeddings if embeddings is not None else MiniLMEmbeddings(local_only=True)
    config = {
        "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode("utf-8")).hexdigest(),
        "fixture_root": str(fixture_root), "selected_cases": [case.id for case in cases],
        "strategies": list(strategies), "provider": options.provider, "model": base.model,
        "embeddings": backend.identity, "embedding_dimension": backend.dimension, "context": asdict(options.context),
        "agent": asdict(options.agent), "pair_order": "alternate",
        "retrieval": {"mode": "hybrid", "top_k": options.context.max_sources * 3,
                                                      "candidate_k": DEFAULT_CANDIDATES, "rrf_k": DEFAULT_RRF_K},
    }
    if secret and any(secret in value for value in (dataset.model_dump_json(), _serialize(config))):
        raise ValueError("Benchmark inputs contain the configured Gemini credential.")
    if options.resume:
        validate_repository(output)
    else:
        output.mkdir(parents=True)
    with repository_lock(output):
        return _execute(options, cases, strategies, roots, output, backend, base, budget,
                        estimated, config, announce)


def _execute(options, cases, strategies, roots, output, backend, base, budget, estimated, config, announce):
    from reposherlock.benchmarking.reporting import write_reports

    live = options.provider == "gemini"
    path = output / RESULTS_NAME
    if options.resume:
        state = _read_results(path)
        if state["config"] != config:
            raise ValueError("Benchmark configuration changed. Use a new output directory.")
        if budget < state["request_budget"]:
            raise ValueError("A resumed benchmark cannot lower its physical request budget.")
    else:
        state = {"version": RUN_VERSION, "config": config, "cases": [
            {**review_truth(case), "issue": redact(case.issue)} for case in cases
        ], "snapshots": {}, "setup_ms": {}, "rows": [], "history": [], "requests_used": 0, "active": None}
    state.update(request_budget=budget, expected_uninterrupted_physical_upper_bound=estimated)

    def persist() -> None:
        serialized = _serialize(state)
        if live and base.api_key in serialized:
            raise ValueError("Refusing to persist a benchmark record containing the configured credential.")
        atomic_write(path, serialized)

    if state["active"]:
        active = state["active"]
        state["history"].append({**active, "status": "interrupted", "physical_model_attempts":
                                 state["requests_used"] - active["start_requests"] if live else None})
        state["active"] = None
    persist()
    announce(f"{options.provider} benchmark, {len(cases)} case(s), {len(strategies)} strategy run(s) per case. "
             f"Physical Gemini upper bound for an uninterrupted pass: {estimated}. "
             f"Lifetime budget: {budget}; attempts already reserved: {state['requests_used']}.")
    staging = output / "snapshots"
    staging.mkdir(exist_ok=True)
    validate_repository(staging)
    for name, root in roots.items():
        started = perf_counter()
        known = state["snapshots"].get(name)
        state["snapshots"][name] = _fixture_snapshot(
            root, staging / name, [case for case in cases if case.fixture == name],
            backend, known,
        )
        if known is None:
            state["setup_ms"][name] = round((perf_counter() - started) * 1000, 3)
        persist()

    denied = False

    def reserve() -> None:
        nonlocal denied
        if state["requests_used"] >= budget:
            denied = True
            raise BudgetExhausted("The benchmark physical request budget is exhausted.")
        state["requests_used"] += 1
        persist()

    provider = replace(base, before_request=reserve) if live else base
    pairs = {(row["case_id"], row["strategy"]): row for row in state["rows"]}
    for index, case in enumerate(cases):
        order = strategies if index % 2 == 0 else reversed(strategies)
        for strategy in order:
            key = (case.id, strategy)
            previous = pairs.get(key)
            if (previous and previous["status"] == "complete") or (live and state["requests_used"] >= budget):
                continue
            denied = False
            state["active"] = {"case_id": case.id, "strategy": strategy, "start_requests": state["requests_used"]}
            persist()
            row = _run_case(case, strategy, staging / case.fixture, state["snapshots"][case.fixture],
                            backend, options, provider, state, budget, lambda: denied)
            if previous:
                state["history"].append(previous)
            pairs[key] = row
            state["rows"] = list(pairs.values())
            state["active"] = None
            persist()
    write_reports(output, state)
    return state
