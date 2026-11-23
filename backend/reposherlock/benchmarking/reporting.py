"""Produce reproducible machine and human review artifacts."""

import hashlib
import os
from html import escape
from pathlib import Path
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from reposherlock.benchmarking.metrics import compare
from reposherlock.benchmarking.runner import RESULTS_NAME, _read_results, _serialize, atomic_write
from reposherlock.investigation.redaction import MASK, redact
from reposherlock.semantic.storage import repository_lock

Judgment = Literal["Correct", "Partially correct", "Incorrect", "Insufficient evidence"]


class ReviewRow(BaseModel, strict=True, extra="forbid"):
    case_id: str
    strategy: Literal["single", "agentic"]
    diagnosis: Judgment | None = None
    fix: Judgment | None = None
    regression: Judgment | None = None
    unsupported_claims: int | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=4000)


class Review(BaseModel, strict=True, extra="forbid"):
    dataset_sha256: str
    results_sha256: str
    rows: list[ReviewRow] = Field(max_length=200)


def _safe(value: object) -> str:
    text = escape(redact(str(value))).replace("\n", " ").replace("\r", " ")
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _values(rows: list[dict], metric: str) -> list:
    return [row["metrics"][metric] for row in rows if row["metrics"][metric] is not None]


def _mean(rows: list[dict], metric: str) -> str:
    values = _values(rows, metric)
    return f"{sum(values) / len(values):.1%} ({len(values)})" if values else "—"


def _median(rows: list[dict], metric: str) -> str:
    values = _values(rows, metric)
    return f"{median(values):,.2f}" if values else "—"


def _total(rows: list[dict], metric: str) -> str:
    values = _values(rows, metric)
    return str(sum(values)) if values else "—"


def _tokens(rows: list[dict], key: str) -> str:
    totals = [row["metrics"]["token_usage"][key] for row in rows if row["metrics"]["token_usage"]]
    present = [field for field in totals if field["tokens"] is not None]
    if not present:
        return "—"
    total = sum(field["tokens"] for field in present)
    complete = sum(field["status"] == "complete" for field in present)
    return f"{total:,} ({complete}/{len(rows)} rows complete)"


def _citation_rate(rows: list[dict]) -> str:
    valid = sum(_values(rows, "valid_citations"))
    invalid = sum(_values(rows, "invalid_citations"))
    return f"{valid / (valid + invalid):.1%} ({valid}/{valid + invalid})" if valid + invalid else "—"


def _group_lines(groups: dict[str, list[str]]) -> list[str]:
    return [f"- {label.replace('_', ' ').capitalize()} — {', '.join(map(_safe, ids)) or 'none'}"
            for label, ids in groups.items()]


def summary(state: dict) -> str:
    config = state["config"]
    rows = state["rows"]
    completed = [row for row in rows if row["status"] == "complete"]
    paired_case_ids = {row["case_id"] for row in completed
                       if sum(other["case_id"] == row["case_id"] for other in completed) == 2}
    paired = [row for row in completed if row["case_id"] in paired_case_ids]
    case_count = len(state["cases"]) * len(config["strategies"])
    lines = [
        "# Bug investigation benchmark", "",
        f"- Provider and model — {_safe(config['provider'])} / {_safe(config['model'])}",
        f"- Results — {len(completed)} complete, {len(rows) - len(completed)} incomplete, "
        f"{case_count - len(rows)} pending",
        f"- Gemini physical attempts reserved — {state['requests_used']} of {state['request_budget']}", "",
    ]
    if config["provider"] == "mock":
        lines += ["The scripted provider verifies the investigation pipeline. Diagnosis accuracy requires "
                  "human review of real model answers. Token figures are simulated.", ""]
    else:
        lines += ["Token sums use available provider metadata. Missing usage and retries can leave costs unreported.", ""]
    lines += [
        "## Localization and evidence", "",
        "| Strategy | File | Faulty function | Recall@K | Initial context recall | "
        "Final context recall | Citation validity | Invalid citations | Unsupported abstention |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in config["strategies"]:
        scored = [row for row in completed if row["strategy"] == strategy]
        attempted = [row for row in rows if row["strategy"] == strategy]
        keys = ("file_localized", "faulty_function_identified", "recall_at_k", "initial_evidence_recall",
                "final_evidence_recall")
        columns = [_mean(scored, key) for key in keys]
        columns += [_citation_rate(attempted), _total(attempted, "invalid_citations"),
                    _mean(scored, "unsupported_abstained")]
        lines.append("| " + " | ".join([strategy, *columns]) + " |")
    lines += ["", "Percentages include the number of eligible cases in parentheses. A citation check validates "
              "reference identity and any quote. Claim support is a separate review judgment.", "",
              "### Paired faulty function localization", ""]
    lines += _group_lines(compare(rows, [case for case in state["cases"] if case["defect"]],
                                  "faulty_function_identified"))
    lines += ["", "### Paired unsupported reports", ""]
    lines += _group_lines(compare(rows, [case for case in state["cases"] if not case["defect"]],
                                  "unsupported_abstained"))
    lines += ["", "### Cross-file evidence", "",
              "| Case | Strategy | Initial context recall | Final context recall | Newly covered locations |",
              "|---|---|---:|---:|---|"]
    for case in state["cases"]:
        if not case["cross_file"]:
            continue
        for row in [item for item in rows if item["case_id"] == case["id"]]:
            metrics = row["metrics"]
            new = ", ".join(f"{_safe(loc['path'])}:{loc['start_line']}–{loc['end_line']}"
                            for loc in metrics["new_relevant_locations"]) or "—"
            lines.append(f"| {_safe(case['id'])} | {row['strategy']} | "
                         f"{_mean([row], 'initial_evidence_recall')} | "
                         f"{_mean([row], 'final_evidence_recall')} | {new} |")
    same_selection = sum(
        all(group[0][field] == group[1][field] for field in ("retrieved_sources", "initial_evidence"))
        for case in paired_case_ids if len(group := [row for row in paired if row["case_id"] == case]) == 2
    )
    lines += ["", "## Efficiency", "",
              f"Completed pairs with identical initial ranked retrieval and selected context — "
              f"{same_selection}/{len(paired_case_ids)}.", "",
              "| Strategy | Cases | Median ms | Median retrieval ms | Retrieval operations | "
              "Tools | Tools with no new labelled coverage | New excerpts | Logical requests | "
              "Physical attempts | Input tokens | Output tokens | Total tokens | Median logical source chars |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for strategy in config["strategies"]:
        pool = paired if len(config["strategies"]) == 2 else completed
        group = [row for row in pool if row["strategy"] == strategy]
        columns = [strategy, str(len(group)), _median(group, "duration_ms"), _median(group, "retrieval_ms")]
        columns += [_total(group, key) for key in (
            "retrieval_operations", "tool_calls", "tool_calls_without_new_labelled_coverage",
            "new_evidence_items", "logical_model_requests", "physical_model_attempts",
        )]
        columns += [_tokens(group, key) for key in ("input", "output", "total")]
        columns.append(_median(group, "logical_source_chars"))
        lines.append("| " + " | ".join(columns) + " |")
    lines += ["", "Paired runs report efficiency over completed pairs. Raw rows and attempt history retain "
              "partial work. Logical source characters approximate source exposure, including planning replay.", "",
              "## Per-case outcomes", "", "| Case | Strategy | Status | Stop reason |",
              "|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {_safe(row['case_id'])} | {row['strategy']} | {row['status']} | {_safe(row['stop_reason'])} |")
    return "\n".join(lines) + "\n"


def _label(location: dict) -> str:
    return f"{_safe(location['path'])}:{location['start_line']}–{location['end_line']} ({_safe(location['symbol'])})"


def review_sheet(state: dict) -> str:
    lines = [
        "# Investigation human review", "",
        f"Provider — {_safe(state['config']['provider'])}.",
        "Review diagnosis, fix and regression independently. Choose Correct, Partially correct, Incorrect, "
        "or Insufficient evidence. Count unsupported source-specific claims separately. "
        "See the benchmark guide for the rubric.", "",
    ]
    rows = {(row["case_id"], row["strategy"]): row for row in state["rows"]}
    for case in state["cases"]:
        lines += [f"## {_safe(case['id'])}", "", f"Bug report — {_safe(case['issue'])}", ""]
        if case["defect"]:
            lines += [f"- Defect — {_label(case['defect'])}", f"- Cause — {_safe(case['diagnosis'])}",
                      f"- Expected correction — {_safe(case['fix'])}",
                      f"- Expected regression — {_safe(case['regression'])}"]
            if case["support"]:
                lines.append("- Supporting locations — " + ", ".join(map(_label, case["support"])))
        lines += [f"- Inclusion rationale — {_safe(case['rationale'])}", ""]
        for strategy in state["config"]["strategies"]:
            row = rows.get((case["id"], strategy), {})
            report = row.get("report")
            lines += [f"### {strategy}", "", f"Outcome — {_safe((report or row).get('status', 'pending'))}"]
            if report:
                for item in report["hypotheses"]:
                    lines += [
                        f"- Hypothesis — {_safe(item['suspected_cause'])}",
                        f"- Explanation — {_safe(item['reasoning'])}",
                        "- Proposed correction — " + ", ".join(map(_safe, item["suggested_fixes"])),
                        "- Proposed regression — " + ", ".join(map(_safe, item["verification_tests"])),
                    ]
                    for citation in item["citations"]:
                        lines.append(f"- Predicted source — {_citation(citation)}")
                for item in report["observations"]:
                    lines.append(f"- Observation — {_safe(item['statement'])}")
                    lines.extend(f"- Supporting citation — {_citation(citation)}" for citation in item["citations"])
                lines.extend(f"- Missing evidence — {_safe(item)}" for item in report["missing_evidence"])
            lines += ["", "- Diagnosis ___", "- Fix ___", "- Regression ___",
                      "- Unsupported claims ___", "- Reviewer notes ___", ""]
    return "\n".join(lines) + "\n"


def _citation(citation: dict) -> str:
    return _label({**citation, "path": citation["file_path"], "symbol": citation["symbol_name"]}) + (
        f" [{_safe(citation['source_id'])}]"
    )


def _review_hash(state: dict) -> str:
    return hashlib.sha256(_serialize(state["rows"]).encode("utf-8")).hexdigest()


def write_reports(output: Path, state: dict) -> None:
    template = Review(
        dataset_sha256=state["config"]["dataset_sha256"], results_sha256=_review_hash(state),
        rows=[ReviewRow(case_id=row["case_id"], strategy=row["strategy"])
              for row in state["rows"] if row["status"] == "complete"],
    )
    atomic_write(output / "summary.md", summary(state))
    atomic_write(output / "review.md", review_sheet(state))
    atomic_write(output / "review-template.json", _serialize(template.model_dump()))


def apply_review(output: Path, path: Path, destination: Path) -> None:
    if destination.resolve() in {path.resolve(), (output / RESULTS_NAME).resolve()}:
        raise ValueError("Choose a new path for the reviewed summary.")
    with repository_lock(output):
        state = _read_results(output / RESULTS_NAME)
        with path.open("rb") as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("The human review exceeds 2 MB.")
        try:
            review = Review.model_validate_json(data)
        except ValidationError:
            raise ValueError("Invalid human review format.") from None
        if (review.dataset_sha256 != state["config"]["dataset_sha256"]
                or review.results_sha256 != _review_hash(state)):
            raise ValueError("The review belongs to a different version of the benchmark results.")
        rows = {(row["case_id"], row["strategy"]): row for row in state["rows"] if row["status"] == "complete"}
        secret = os.environ.get("GEMINI_API_KEY", "").strip()
        seen = set()
        notes = []
        for item in review.rows:
            pair = (item.case_id, item.strategy)
            if pair in seen or pair not in rows:
                raise ValueError("The review contains a duplicate or unavailable case and strategy.")
            seen.add(pair)
            metrics = rows[pair]["metrics"]
            metrics.update({key: getattr(item, key) for key in ("diagnosis", "fix", "regression", "unsupported_claims")})
            metrics["diagnosis_correct"] = item.diagnosis == "Correct" if item.diagnosis is not None else None
            clean = item.notes.replace(secret, MASK) if secret else item.notes
            notes.append(f"| {_safe(item.case_id)} | {item.strategy} | {item.diagnosis or '—'} | "
                         f"{item.fix or '—'} | {item.regression or '—'} | "
                         f"{item.unsupported_claims if item.unsupported_claims is not None else '—'} | {_safe(clean)} |")
        findings = "\n".join(_group_lines(compare(state["rows"], [case for case in state["cases"] if case["defect"]],
                                                  "diagnosis_correct")))
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(summary(state) + "\n## Paired human diagnosis judgments\n\n" + findings
                         + "\n\n| Case | Strategy | Diagnosis | Fix | Regression | Unsupported claims | Notes |\n"
                         "|---|---|---|---|---|---:|---|\n" + "\n".join(notes) + "\n")
