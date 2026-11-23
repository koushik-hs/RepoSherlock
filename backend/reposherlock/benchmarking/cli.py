"""Command-line settings for the isolated investigation benchmark."""

import argparse
from pathlib import Path

from reposherlock.benchmarking.dataset import DEFAULT_DATASET, DEFAULT_FIXTURES
from reposherlock.investigation.context import DEFAULT_CONTEXT_CHARS, DEFAULT_MAX_SOURCES, ContextLimits


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output-dir", type=Path, required=True, help="Dedicated run directory")
    parser.add_argument("--provider", choices=("mock", "gemini"), default="mock")
    parser.add_argument("--strategy", choices=("single", "agentic", "both"), default="both")
    parser.add_argument("--case", action="append", default=[], help="Select a case ID, repeatable")
    parser.add_argument("--max-cases", type=int, help="Case cap (default all offline, one for Gemini)")
    parser.add_argument("--request-budget", type=int, help="Lifetime physical Gemini attempt cap (default 9)")
    parser.add_argument("--allow-remote-code", action="store_true", help="Approve sending redacted fixture excerpts")
    parser.add_argument("--resume", action="store_true", help="Resume the same experiment with its saved budget ledger")
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    parser.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES)
    parser.add_argument("--review", type=Path, help="Import human labels without invoking a provider")
    parser.add_argument("--review-output", type=Path, help="New reviewed Markdown summary")


def execute(args: argparse.Namespace) -> None:
    if args.review is not None:
        from reposherlock.benchmarking.reporting import apply_review

        destination = args.review_output or args.output_dir / "reviewed-summary.md"
        apply_review(args.output_dir, args.review, destination)
        print(f"Reviewed summary — {destination.resolve()}")
        return
    if args.review_output is not None:
        raise ValueError("--review-output requires --review.")

    from reposherlock.benchmarking.runner import Options, run

    options = Options(
        dataset=args.dataset, fixtures=args.fixture_root, output=args.output_dir, provider=args.provider,
        strategy=args.strategy, max_cases=args.max_cases, request_budget=args.request_budget,
        allow_remote_code=args.allow_remote_code, resume=args.resume, case_ids=tuple(args.case),
        context=ContextLimits(args.context_chars, args.max_sources),
    )
    state = run(options)
    complete = sum(row["status"] == "complete" for row in state["rows"])
    expected = len(state["cases"]) * len(state["config"]["strategies"])
    print(f"Completed {complete}/{expected} investigations. Results — {args.output_dir.resolve()}")
    if complete != expected:
        raise RuntimeError("Benchmark has incomplete results. Inspect the summary and resume when ready.")
