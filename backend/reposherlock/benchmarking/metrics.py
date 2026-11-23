"""Score source references and accounting without inferring diagnosis quality."""

from dataclasses import asdict

from reposherlock.benchmarking.dataset import Case, Label
from reposherlock.investigation.report import InvestigationDocument
from reposherlock.investigation.trace import InvestigationTrace

FAILURE_STOPS = {"planner_provider_failure", "report_provider_failure", "invalid_report",
                 "invalid_planning_response"}


def _found(locations: list[Label], references: list[dict]) -> set[Label]:
    return {label for label in locations if any(label.covers(ref) for ref in references)}


def evaluate(case: Case, document: InvestigationDocument | None, trace: InvestigationTrace | None,
             *, state: str, depth: int, provider: str, attempts: int | None,
             retrieved: list[dict] | None = None) -> dict:
    fresh_ids = {source for tool in trace.tools for source in tool.new_evidence_ids} if trace else set()
    evidence = list(trace.evidence) if trace else []
    initial = [entry for entry in evidence if entry["source_id"] not in fresh_ids][:depth]
    locations = case.locations
    covered_initial = _found(locations, initial)
    covered_final = _found(locations, evidence)
    newly_covered = covered_final - covered_initial
    report = document.model_dump(mode="json") if document and state == "complete" else None
    citations = []
    valid = invalid = None
    if report:
        for item in (*report["hypotheses"], *report["observations"]):
            for citation in item["citations"]:
                citation.pop("quote", None)
        citations = [citation for item in report["hypotheses"] for citation in item["citations"]]
        valid = len(citations) + sum(len(item["citations"]) for item in report["observations"])
        invalid = 0
    elif trace and trace.model_requests:
        final = trace.model_requests[-1]
        valid, invalid = final.valid_citations, final.invalid_citations
    defect = case.defect
    has_valid_report = report is not None
    usage = asdict(trace.usage_summary) if trace else None
    useful_ids = {
        item["source_id"] for item in evidence if item["source_id"] in fresh_ids
        and any(label.covers(item) for label in newly_covered)
    }
    return {
        "status": state,
        "report": report,
        "initial_evidence": initial,
        "retrieved_sources": retrieved,
        "final_evidence": evidence,
        "metrics": {
            "file_localized": any(ref["file_path"] == defect.path for ref in citations)
            if defect and has_valid_report else None,
            "faulty_function_identified": any(defect.covers(ref, require_symbol=True) for ref in citations)
            if defect and has_valid_report else None,
            "recall_k": depth,
            "recall_at_k": len(_found(locations, retrieved[:depth])) / len(locations)
            if locations and retrieved is not None else None,
            "initial_evidence_recall": len(covered_initial) / len(locations) if locations and trace else None,
            "final_evidence_recall": len(covered_final) / len(locations) if locations and trace else None,
            "new_relevant_locations": [label.model_dump() for label in locations if label in newly_covered],
            "cross_file_gain": bool(newly_covered) if case.cross_file and trace else None,
            "valid_citations": valid,
            "invalid_citations": invalid,
            "valid_citation_rate": valid / (valid + invalid) if valid is not None and invalid is not None
            and valid + invalid else None,
            "unsupported_abstained": report["status"] == "insufficient_evidence"
            if not defect and has_valid_report else None,
            "diagnosis": None,
            "fix": None,
            "regression": None,
            "unsupported_claims": None,
            "tool_calls": len(trace.tools) if trace else None,
            "tool_calls_without_new_evidence": sum(not tool.new_evidence_ids for tool in trace.tools)
            if trace else None,
            "tool_calls_without_new_labelled_coverage": sum(
                not useful_ids.intersection(tool.new_evidence_ids) for tool in trace.tools
            ) if trace else None,
            "new_evidence_items": len(fresh_ids) if trace else None,
            "retrieval_operations": trace.retrieval_operations if trace else None,
            "initial_retrieval_ms": trace.initial_retrieval_ms if trace else None,
            "retrieval_ms": round(trace.initial_retrieval_ms + sum(
                tool.duration_ms for tool in trace.tools
                if tool.name == "search_code" and tool.status in {"ok", "no_new_evidence", "no_match"}
            ), 3) if trace else None,
            "duration_ms": trace.duration_ms if trace else None,
            "logical_model_requests": len(trace.model_requests) if trace else None,
            "physical_model_attempts": attempts,
            "token_usage": usage,
            "usage_kind": ("simulated" if provider == "mock" else "provider_reported")
            if usage and any(usage[key]["tokens"] is not None for key in ("input", "output", "total"))
            else "unavailable",
            "context_chars": trace.context_chars if trace else None,
            "logical_source_chars": trace.logical_source_chars_sent if trace else None,
        },
        "trace": asdict(trace) if trace else None,
    }


def compare(rows: list[dict], cases: list[dict], metric: str) -> dict[str, list[str]]:
    by_pair = {(row["case_id"], row["strategy"]): row for row in rows if row["status"] == "complete"}
    groups = {key: [] for key in ("both", "single_only", "agentic_only", "neither", "unpaired_or_unscored")}
    outcomes = {(True, True): "both", (True, False): "single_only",
                (False, True): "agentic_only", (False, False): "neither"}
    for case in cases:
        values = tuple(
            by_pair.get((case["id"], strategy), {}).get("metrics", {}).get(metric)
            for strategy in ("single", "agentic")
        )
        group = outcomes.get(values, "unpaired_or_unscored")
        groups[group].append(case["id"])
    return groups
