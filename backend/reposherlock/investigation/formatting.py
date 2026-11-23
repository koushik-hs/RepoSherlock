"""Render redacted evidence and validated findings for the command line."""

from unicodedata import category

from reposherlock.investigation.report import Citation
from reposherlock.investigation.service import InvestigationResult


def _reference(citation: Citation) -> str:
    symbol = f" ({citation.symbol_name})" if citation.symbol_name else ""
    return f"{citation.source_id} {citation.file_path}:{citation.start_line}-{citation.end_line}{symbol}"


def _citations(citations: list[Citation]) -> list[str]:
    lines = []
    for citation in citations:
        lines.append(f"  Evidence {_reference(citation)}")
        if citation.quote is not None:
            lines.extend(f"    {line}" for line in citation.quote.splitlines())
    return lines


def format_investigation(result: InvestigationResult) -> str:
    if result.status == "dry_run":
        lines = ["Dry run. No Gemini request was made."]
    elif result.status == "insufficient_evidence":
        lines = ["Insufficient evidence for a suspected cause."]
    else:
        lines = ["Investigation hypotheses from static evidence."]
    if result.provider is not None:
        lines.append(f"Provider {result.provider}, model {result.model}.")
    lines.append("Verification status: not run.")
    diagnostics = result.diagnostics
    lines.append(
        f"Hybrid retrieval matched {len(diagnostics.matched_terms)}/{len(diagnostics.query_terms)} evidence terms"
        f" with {diagnostics.shared_candidates or 0} shared candidates."
    )
    context = result.context
    if not context.sources:
        lines.append("No usable source excerpts were selected.")
    elif diagnostics.status == "no_convincing_evidence":
        lines.append("Low lexical overlap in initial retrieval. Relevant code may still appear in the excerpts.")
    lines.append(f"Selected evidence: {len(context.sources)} excerpts, {len(context.payload)} JSON characters.")
    for source in context.sources:
        symbol = f" ({source.symbol_name})" if source.symbol_name else ""
        lines.append(f"\n{source.source_id} {source.file_path}:{source.start_line}-{source.end_line}{symbol}")
        lines.extend(f"  {line}" for line in source.snippet.splitlines())

    document = result.document
    if document is not None:
        lines.extend(["", "Reported problem", document.problem_summary])
        if document.observations:
            lines.append("\nSource observations")
        for observation in document.observations:
            lines.append(f"- {observation.statement}")
            lines.extend(_citations(observation.citations))
        if document.hypotheses:
            lines.append("\nSuspected causes")
        for hypothesis in document.hypotheses:
            lines.extend([f"- {hypothesis.suspected_cause}", f"  {hypothesis.reasoning}"])
            lines.extend(_citations(hypothesis.citations))
            lines.extend(f"  Suggested fix: {value}" for value in hypothesis.suggested_fixes)
            lines.extend(f"  Proposed regression test: {value}" for value in hypothesis.verification_tests)
        for header, values in (
            ("Uncertainty", document.uncertainties), ("Missing evidence", document.missing_evidence),
        ):
            if values:
                lines.append(f"\n{header}")
                lines.extend(f"- {value}" for value in values)
    summary = result.trace.usage_summary if result.trace is not None else None
    if summary is not None and summary.request_count:
        label = "Cumulative token usage" if result.trace.strategy == "agentic" else "Token usage"
        counts = []
        for name, tally in (("total", summary.total), ("input", summary.input), ("output", summary.output)):
            amount = tally.tokens if tally.tokens is not None else "unavailable"
            counts.append(
                f"{name} {amount} ({tally.status}, {tally.reported_requests}/{summary.request_count} requests)"
            )
        lines.append(f"\n{label}: " + ", ".join(counts))
    elif result.usage is not None:
        usage = result.usage
        counts = [
            f"{label} {value}" for label, value in (
                ("input", usage.input_tokens), ("output", usage.output_tokens), ("total", usage.total_tokens),
            ) if value is not None
        ]
        if counts:
            lines.append("\nToken usage: " + ", ".join(counts))

    text = "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")
    # Escape terminal controls and directional markers in untrusted source and model text.
    return "".join(
        character if character in "\n\t" or category(character) not in ("Cc", "Cf", "Cs")
        else ascii(character)[1:-1]
        for character in text
    )
