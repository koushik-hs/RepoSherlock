"""Run a labelled retrieval benchmark against an existing repository index."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from pydantic import BaseModel, Field

from reposherlock.ingestion.models import SemanticChunk
from reposherlock.retrieval.models import MODES, RetrievalHit, RetrievalMode
from reposherlock.retrieval.service import RetrievalService
from reposherlock.semantic.storage import read_chunks, verified_snapshot

BENCHMARK_DEPTH = 5


class Location(BaseModel, frozen=True, extra="forbid"):
    path: str = Field(min_length=1)
    symbol: str | None = None
    anchor: str | None = None

    def matches(self, chunk: SemanticChunk) -> bool:
        return (
            chunk.relative_path == self.path
            and (self.symbol is None or chunk.symbol_name == self.symbol)
            and (self.anchor is None or self.anchor in chunk.content)
        )


class Query(BaseModel, extra="forbid"):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=1)


class PositiveQuery(Query):
    relevant: list[Location] = Field(min_length=1)


class Dataset(BaseModel, extra="forbid"):
    positives: list[PositiveQuery] = Field(min_length=1)
    negatives: list[Query] = Field(min_length=1)


def verify_labels(service: RetrievalService, dataset: Dataset) -> None:
    pending = {location for query in dataset.positives for location in query.relevant}
    with verified_snapshot(service.root, service.repository_id) as (directory, source):
        for chunk in read_chunks(directory, source):
            pending = {location for location in pending if not location.matches(chunk)}
    if pending:
        invalid = sorted(f"{location.path} / {location.symbol} / {location.anchor}" for location in pending)
        raise ValueError("Benchmark labels missing from the source index: " + "; ".join(invalid))


def positive_metrics(hits: Sequence[RetrievalHit], relevant: Sequence[Location]) -> tuple[float, float, float]:
    first: set[Location] = set()
    found: set[Location] = set()
    reciprocal = 0.0
    for hit in hits[:BENCHMARK_DEPTH]:
        matching = {location for location in relevant if location.matches(hit.chunk)}
        if matching and not reciprocal:
            reciprocal = 1 / hit.rank
        if hit.rank == 1:
            first = matching
        found.update(matching)
    total = len(set(relevant))
    return len(first) / total, len(found) / total, reciprocal


def evaluate(service: RetrievalService, dataset: Dataset) -> dict:
    ids = [query.id for query in (*dataset.positives, *dataset.negatives)]
    if len(set(ids)) != len(ids):
        raise ValueError("Benchmark query IDs must be unique.")
    verify_labels(service, dataset)
    report = {}
    for mode in MODES:
        start = perf_counter()
        service.search(service.repository_id, dataset.positives[0].query, BENCHMARK_DEPTH, mode=mode)
        first_ms = (perf_counter() - start) * 1000
        records = {}
        for query in (*dataset.positives, *dataset.negatives):
            records[query.id] = _evaluate_query(service, mode, query)
        positive_records = [records[query.id] for query in dataset.positives]
        negative_records = [records[query.id] for query in dataset.negatives]
        report[mode] = {
            "recall_at_1": mean(record["recall_at_1"] for record in positive_records),
            "recall_at_5": mean(record["recall_at_5"] for record in positive_records),
            "mrr_at_5": mean(record["reciprocal_rank"] for record in positive_records),
            "median_ms": median(record["latency_ms"] for record in records.values()),
            "first_ms": first_ms,
            "negatives_flagged": sum(record["unsupported"] for record in negative_records),
            "positives_flagged": sum(record["unsupported"] for record in positive_records),
            "queries": records,
        }
    return {
        "repository_id": service.repository_id, "positive_count": len(dataset.positives),
        "negative_count": len(dataset.negatives), "modes": report,
    }


def _evaluate_query(service: RetrievalService, mode: RetrievalMode, query: Query) -> dict:
    start = perf_counter()
    response = service.search(service.repository_id, query.query, BENCHMARK_DEPTH, mode=mode)
    elapsed_ms = (perf_counter() - start) * 1000
    record = {
        "query": query.query,
        "category": query.category,
        "latency_ms": elapsed_ms,
        "unsupported": response.diagnostics.status == "no_convincing_evidence",
        "diagnostics": asdict(response.diagnostics),
        "hits": [],
    }
    relevant = query.relevant if isinstance(query, PositiveQuery) else ()
    for hit in response.hits:
        fields = asdict(hit)
        chunk = fields.pop("chunk")
        chunk.pop("content")
        record["hits"].append({
            **fields, **chunk, "label_match": any(label.matches(hit.chunk) for label in relevant),
        })
    if relevant:
        first, five, reciprocal = positive_metrics(response.hits, relevant)
        record.update(recall_at_1=first, recall_at_5=five, reciprocal_rank=reciprocal)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate all RepoSherlock retrieval modes.")
    parser.add_argument("--path", required=True, type=Path, help="Indexed repository")
    parser.add_argument("--dataset", required=True, type=Path, help="Labelled JSON benchmark")
    parser.add_argument("--output", type=Path, help="Write per-query results as JSON")
    args = parser.parse_args(argv)
    try:
        dataset = Dataset.model_validate_json(args.dataset.read_bytes())
        report = evaluate(RetrievalService(args.path), dataset)
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        parser.exit(1, f"reposherlock evaluation: {error}\n")

    print(f'{report["positive_count"]} positive and {report["negative_count"]} negative queries')
    print("| Mode | Recall@1 | Recall@5 | MRR@5 | Median ms | First ms | Negatives flagged | Positives flagged |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for mode, metrics in report["modes"].items():
        print(
            f'| {mode} | {metrics["recall_at_1"]:.3f} | {metrics["recall_at_5"]:.3f} '
            f'| {metrics["mrr_at_5"]:.3f} | {metrics["median_ms"]:.2f} | {metrics["first_ms"]:.2f} '
            f'| {metrics["negatives_flagged"]} | {metrics["positives_flagged"]} |'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
