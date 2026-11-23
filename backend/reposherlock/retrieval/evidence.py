"""Conservative query-to-source overlap diagnostics for ranked candidates."""

from collections.abc import Sequence

from reposherlock.retrieval.models import EVIDENCE_DEPTH, EvidenceDiagnostics, RankedChunk
from reposherlock.retrieval.tokenizer import normalized_exact, query_terms, tokenize

# These words describe the search itself and carry little evidence on their own.
GENERIC_TERMS = frozenset({"code", "file", "find", "function", "repository", "system"})


def assess_evidence(
    query: str, ranking: Sequence[RankedChunk], *,
    semantic_candidates: int | None, lexical_candidates: int | None,
    shared_candidates: int | None,
) -> EvidenceDiagnostics:
    terms = set(query_terms(query)) - GENERIC_TERMS
    query_reference = normalized_exact(query)
    matched: set[str] = set()
    strongest_overlap = 0
    exact = False
    for hit in ranking[:EVIDENCE_DEPTH]:
        chunk = hit.chunk
        path = normalized_exact(chunk.relative_path)
        symbol = normalized_exact(chunk.symbol_name or "")
        references = {path, path.rsplit("/", 1)[-1], symbol, symbol.rsplit(".", 1)[-1]}
        exact = exact or query_reference in references
        tokens = set(tokenize(f"{chunk.relative_path}\n{chunk.symbol_name or ''}\n{chunk.content}"))
        overlap = terms & tokens
        matched.update(overlap)
        strongest_overlap = max(strongest_overlap, len(overlap))

    enough_terms = bool(terms) and (
        strongest_overlap >= min(2, len(terms)) and len(matched) * 2 >= len(terms)
    )
    return EvidenceDiagnostics(
        "candidate_evidence" if exact or enough_terms else "no_convincing_evidence",
        tuple(sorted(terms)), tuple(sorted(matched)), exact,
        semantic_candidates, lexical_candidates, shared_candidates,
    )
