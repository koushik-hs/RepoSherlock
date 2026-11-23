"""Command-line entry point for local repository indexing."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from reposherlock.core.config import ChunkSettings, ScanLimits, SEMANTIC_CHUNK_SETTINGS
from reposherlock.ingestion.indexer import index_repository
from reposherlock.investigation.context import DEFAULT_CONTEXT_CHARS, DEFAULT_MAX_SOURCES
from reposherlock.retrieval.models import DEFAULT_CANDIDATES, DEFAULT_RRF_K, MODES
from reposherlock.semantic.embeddings import DEFAULT_BATCH_SIZE


def _index_options(index: argparse.ArgumentParser, chunks: ChunkSettings) -> None:
    scan = ScanLimits()
    index.add_argument("--path", required=True, type=Path, help="Local repository directory")
    index.add_argument("--max-file-bytes", type=int, default=scan.max_file_bytes, help="Bytes per file")
    index.add_argument("--max-files", type=int, default=scan.max_files, help="Accepted source files")
    index.add_argument(
        "--max-total-bytes", type=int, default=scan.max_total_bytes, help="Bytes of accepted source"
    )
    index.add_argument("--chunk-lines", type=int, default=chunks.chunk_lines, help="Lines per chunk")
    index.add_argument(
        "--overlap-lines", type=int, default=chunks.overlap_lines, help="Lines shared between chunks"
    )


def _semantic_command(args: argparse.Namespace) -> None:
    if args.command == "semantic-index":
        from reposherlock.semantic.indexing import build_semantic_index

        stats = build_semantic_index(
            args.path, batch_size=args.batch_size,
            scan_limits=ScanLimits(args.max_file_bytes, args.max_files, args.max_total_bytes),
            chunk_settings=ChunkSettings(args.chunk_lines, args.overlap_lines),
            rebuild=args.rebuild,
        )
        print(f"Semantic index contains {stats.total} chunks for {stats.repository_id}.")
        print(f"New {stats.new}, reused {stats.reused}, updated {stats.updated}, "
              f"removed {stats.removed}, embeddings generated {stats.embedded}.")
    else:
        from reposherlock.retrieval.service import RetrievalService

        service = RetrievalService(args.path)
        results = service.search(
            service.repository_id, args.query, args.top_k, mode=args.mode,
            candidate_k=args.candidate_k, rrf_k=args.rrf_k,
        )
        diagnostics = results.diagnostics
        if not results.hits:
            print("No matching source excerpts were retrieved.")
        elif diagnostics.status == "no_convincing_evidence":
            print("Low lexical overlap. Ranked candidates follow for review.")
        agreement = (f", {diagnostics.shared_candidates} shared candidates"
                     if diagnostics.shared_candidates is not None else "")
        print(f"{args.mode.capitalize()} search, {len(diagnostics.matched_terms)}/"
              f"{len(diagnostics.query_terms)} evidence terms matched{agreement}.")
        for hit in results.hits:
            chunk = hit.chunk
            symbol = f"  {chunk.symbol_name}" if chunk.symbol_name else ""
            scores = []
            if hit.semantic_score is not None:
                scores.append(f"cosine similarity {hit.semantic_score:.4f} (semantic #{hit.semantic_rank})")
            if hit.bm25_score is not None:
                scores.append(f"BM25 {hit.bm25_score:.4f} (lexical #{hit.bm25_rank})")
            if hit.rrf_score is not None:
                scores.append(f"RRF {hit.rrf_score:.6f}")
            print(f"{hit.rank}. {chunk.relative_path}:{chunk.start_line}-{chunk.end_line}{symbol}"
                  f"  {' | '.join(scores)}")
            lines = chunk.content.splitlines()
            snippet = "\n".join(lines[:8])
            print(snippet[:480] + ("..." if len(lines) > 8 or len(snippet) > 480 else ""))


def _investigation_command(args: argparse.Namespace) -> None:
    from reposherlock.investigation.context import ContextLimits, read_issue_file
    from reposherlock.investigation.formatting import format_investigation
    from reposherlock.investigation.provider import require_remote_permission
    from reposherlock.investigation.service import investigate
    from reposherlock.investigation.trace import save_trace, validate_trace_destination

    if not args.dry_run:
        require_remote_permission(args.allow_remote_code)
    issue = args.issue if args.issue_file is None else read_issue_file(args.issue_file)
    if args.trace_output:
        validate_trace_destination(args.trace_output, args.path)
    if args.strategy == "agentic":
        from reposherlock.investigation.agent import AGENT_CONTEXT_CHARS, AgentLimits, investigate_agentic
        from reposherlock.investigation.tools import MAX_READ_CHARS, TOOL_MODELS

        budget = AgentLimits(
            args.max_rounds, args.max_tool_calls, args.max_calls_per_round,
            args.max_read_lines, args.max_model_requests,
        )
        context = ContextLimits(
            max_chars=AGENT_CONTEXT_CHARS if args.context_chars is None else args.context_chars,
            max_sources=args.max_sources,
        )
        result = investigate_agentic(
            args.path, issue, dry_run=args.dry_run, allow_remote_code=args.allow_remote_code,
            context_limits=context, limits=budget,
        )
        print("Agentic strategy")
        print("Read-only tools: " + ", ".join(TOOL_MODELS))
        print(f"Budgets: {budget.max_rounds} rounds, {budget.max_tool_calls} tool calls"
              f" ({budget.max_calls_per_round} per round), {budget.max_model_requests} model requests,"
              f" {budget.max_read_lines} lines and {MAX_READ_CHARS} characters per line read,"
              f" {context.max_chars} context characters, {context.max_sources} initial excerpts.")
        print(f"Stop reason: {result.trace.stop_reason}")
    else:
        context_chars = DEFAULT_CONTEXT_CHARS if args.context_chars is None else args.context_chars
        result = investigate(
            args.path, issue, dry_run=args.dry_run, allow_remote_code=args.allow_remote_code,
            limits=ContextLimits(max_chars=context_chars, max_sources=args.max_sources),
        )
    if args.trace_output and result.trace is not None:
        save_trace(args.trace_output, args.path, result.trace)
    print(format_investigation(result))


def main(argv: Sequence[str] | None = None) -> int:
    from reposherlock.benchmarking.cli import add_arguments as add_benchmark_arguments

    parser = argparse.ArgumentParser(description="Index, search, and investigate local source with RepoSherlock.")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser(
        "index", help="Build a line-based JSONL index",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _index_options(index, ChunkSettings())
    semantic = commands.add_parser(
        "semantic-index", help="Build or refresh the local Chroma index",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _index_options(semantic, SEMANTIC_CHUNK_SETTINGS)
    semantic.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                          help="Source chunks per embedding batch")
    semantic.add_argument("--rebuild", action="store_true", help="Build a fresh vector collection")
    search = commands.add_parser("search", help="Search source with semantic, lexical, or hybrid ranking")
    search.add_argument("--path", required=True, type=Path, help="Local repository directory")
    search.add_argument("--query", required=True, help="Natural-language query")
    search.add_argument("--top-k", type=int, default=5, help="Maximum results, between 1 and 100")
    search.add_argument("--mode", choices=MODES, default="semantic", help="Retrieval mode (default: semantic)")
    search.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATES,
                        help="Hybrid candidate depth per method, between 1 and 100")
    search.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K, help="Positive RRF rank constant")
    investigation = commands.add_parser("investigate", help="Build a source-grounded bug investigation")
    investigation.add_argument("--path", required=True, type=Path, help="Indexed local repository directory")
    issue = investigation.add_mutually_exclusive_group(required=True)
    issue.add_argument("--issue", help="Bug report or error description")
    issue.add_argument("--issue-file", type=Path, help="UTF-8 text file containing a bug report")
    investigation.add_argument("--provider", choices=("gemini",), default="gemini")
    investigation.add_argument("--strategy", choices=("single", "agentic"), default="single")
    investigation.add_argument("--dry-run", action="store_true", help="Preview the selected excerpts locally")
    investigation.add_argument("--allow-remote-code", action="store_true",
                               help="Allow this live request to send the issue and redacted excerpts to Gemini")
    investigation.add_argument("--context-chars", type=int,
                               help="Maximum payload characters (single 16000, agentic 24000)")
    investigation.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES,
                               help="Maximum initial source excerpts")
    investigation.add_argument("--max-rounds", type=int, default=3, help="Agent planning rounds")
    investigation.add_argument("--max-tool-calls", type=int, default=8, help="Agent tool-call budget")
    investigation.add_argument("--max-calls-per-round", type=int, default=4, help="Agent calls per round")
    investigation.add_argument("--max-read-lines", type=int, default=150, help="Lines per agent code read")
    investigation.add_argument("--max-model-requests", type=int, default=5, help="Agent logical model requests")
    investigation.add_argument("--trace-output", type=Path, help="Write an exclusive metadata-only JSON trace")
    benchmark = commands.add_parser("benchmark", help="Compare investigations on labeled bug reports")
    add_benchmark_arguments(benchmark)
    args = parser.parse_args(argv)

    if args.command != "index":
        try:
            if args.command == "investigate":
                _investigation_command(args)
            elif args.command == "benchmark":
                from reposherlock.benchmarking.cli import execute

                execute(args)
            else:
                _semantic_command(args)
        except ModuleNotFoundError as error:
            parser.exit(1, f"reposherlock {args.command}: Install the [semantic,embeddings] extras. {error}\n")
        except Exception as error:
            parser.exit(1, f"reposherlock {args.command}: {error}\n")
        return 0

    try:
        result = index_repository(
            args.path,
            scan_limits=ScanLimits(args.max_file_bytes, args.max_files, args.max_total_bytes),
            chunk_settings=ChunkSettings(args.chunk_lines, args.overlap_lines),
        )
    except (OSError, ValueError) as error:
        index.exit(1, f"reposherlock index: {error}\n")

    print(f"Indexed {result.indexed_files} files into {result.total_chunks} chunks.")
    print(f"Scanned {result.scanned_files} files and skipped {result.skipped_files}.")
    print(f"Chunks:   {result.chunks_path}")
    print(f"Manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
