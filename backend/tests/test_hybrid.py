import io
from collections.abc import Sequence
from math import log
from pathlib import Path

import pytest

from reposherlock.cli import main
from reposherlock.evaluation import Dataset, Location, evaluate
from reposherlock.ingestion.indexer import index_directory, index_repository
from reposherlock.ingestion.models import SemanticChunk
from reposherlock.retrieval import service as retrieval
from reposherlock.retrieval.fusion import reciprocal_rank_fusion
from reposherlock.retrieval.lexical import BM25Index
from reposherlock.retrieval.models import ScoredChunk
from reposherlock.retrieval.service import RetrievalService
from reposherlock.retrieval.tokenizer import tokenize
from reposherlock.semantic.indexing import build_semantic_index


@pytest.mark.parametrize(("text", "terms"), [
    ("getUserById", {"getuserbyid", "get", "user", "by", "id"}),
    ("tenant_id", {"tenant_id", "tenant", "id"}),
    ("TenantGuard", {"tenantguard", "tenant", "guard"}),
    ("HTTPException AUTH_FAILED", {"httpexception", "http", "exception", "auth_failed", "auth", "failed"}),
    ("src/auth/guards/tenant.guard.ts",
     {"src/auth/guards/tenant.guard.ts", "src", "auth", "guards", "tenant.guard.ts", "tenant", "guard", "ts"}),
    ("src\\scan\\scanner.py", {"src/scan/scanner.py", "src", "scan", "scanner.py", "scanner", "py"}),
    ("Cafe\u0301HTTP2", {"caféhttp2", "café", "http", "2"}),
    ("___ 🧪 ???", set()),
])
def test_tokenizer(text: str, terms: set[str]) -> None:
    tokens = list(tokenize(text))
    assert set(tokens) == terms
    assert len(tokens) == len(terms)


def chunk(identifier: str, content: str, symbol: str | None = None) -> SemanticChunk:
    return SemanticChunk(
        identifier, "source.py", "python", 1, 1, content, 0, len(content),
        "tree_sitter", symbol, "function" if symbol else None,
    )


def test_bm25_score_and_unmatched_search() -> None:
    document = chunk("a", "needle")
    index = BM25Index([document, chunk("b", "thread")])
    [match] = index.search("needle needle", 10)
    assert match.chunk == document
    assert match.score == pytest.approx(log(2))
    assert index.search("sprocket", 10) == index.search("", 10) == []
    assert BM25Index([]).search("needle", 1) == []


def test_bm25_weights_exact_symbols_and_sorts_ties() -> None:
    index = BM25Index([
        chunk("z", "validateAccessToken"),
        chunk("b", "placeholder", "validateAccessToken"),
        chunk("a", "placeholder", "validateAccessToken"),
    ])
    matches = index.search("validateAccessToken", 3)
    assert [hit.chunk.chunk_id for hit in matches] == ["a", "b", "z"]
    assert matches[0].score > matches[-1].score
    assert index.search("validate access token", 1)[0].chunk.chunk_id == "a"


def test_rrf_uses_first_rank_for_duplicates_and_keeps_method_scores() -> None:
    a, b, c = (chunk(identifier, "source") for identifier in "abc")
    results = reciprocal_rank_fusion(
        [ScoredChunk(a, 0.8), ScoredChunk(b, 0.7), ScoredChunk(a, 0.2)],
        [ScoredChunk(b, 10.0), ScoredChunk(c, 8.0), ScoredChunk(b, 7.0)], k=60,
    )
    assert [hit.chunk for hit in results] == [b, a, c]
    both = results[0]
    assert (both.semantic_score, both.semantic_rank, both.bm25_score, both.bm25_rank) == (0.7, 2, 10, 1)
    assert both.rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert results[1].bm25_rank is None and results[2].semantic_rank is None
    tied = reciprocal_rank_fusion([ScoredChunk(b, 0.9)], [ScoredChunk(a, 2)])
    assert [hit.chunk for hit in tied] == [a, b]
    assert reciprocal_rank_fusion([], []) == []


class LocalVectors:
    identity = "offline-hybrid-test"
    dimension = 2

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.0] if "tenant" in text.lower() else [0.0, 1.0] for text in texts]


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, LocalVectors, RetrievalService]:
    (tmp_path / "guard.py").write_bytes(
        "class Guard:\r\n    def checkTenant(self):\r\n        return 'tenant café'\r\n".encode("utf-8"),
    )
    (tmp_path / "export.py").write_bytes(b"def render_report():\n    return 'monthly report'\n")
    backend = LocalVectors()
    build_semantic_index(tmp_path, embeddings=backend)
    return tmp_path, backend, RetrievalService(tmp_path, backend)


def test_modes_provenance_and_source_lines(indexed) -> None:
    root, backend, service = indexed
    before = backend.calls
    lexical = service.search(service.repository_id, "checkTenant", 1, mode="lexical")
    assert backend.calls == before
    [lexical_hit] = lexical.hits
    assert lexical_hit.bm25_score and lexical_hit.semantic_score is None and lexical_hit.rrf_score is None
    hybrid = service.search(service.repository_id, "checkTenant", 1, mode="hybrid")
    [hit] = hybrid.hits
    assert hit.mode == "hybrid" and hit.repository_id == service.repository_id and hit.rank == 1
    assert hit.semantic_rank and hit.bm25_rank == 1 and hit.rrf_score
    assert backend.calls == before + 1
    assert hybrid.diagnostics.status == "candidate_evidence" and hybrid.diagnostics.exact_reference
    source = (root / hit.chunk.relative_path).read_bytes()
    lines = [line.encode("utf-8") for line in io.StringIO(source.decode(), newline="")]
    start = sum(map(len, lines[:hit.chunk.start_line - 1])) + hit.chunk.start_column
    end = sum(map(len, lines[:hit.chunk.end_line - 1])) + hit.chunk.end_column
    assert source[start:end].decode() == hit.chunk.content
    assert hit.chunk.symbol_name == "Guard.checkTenant" and hit.chunk.start_line == 2
    assert hybrid == service.search(service.repository_id, "checkTenant", 1, mode="hybrid")


def test_unsupported_queries_are_diagnostic_not_score_thresholds(indexed) -> None:
    _, _, service = indexed
    for mode in ("semantic", "lexical", "hybrid"):
        result = service.search(service.repository_id, "Vulkan shader pipeline", mode=mode)
        assert result.diagnostics.status == "no_convincing_evidence"
        assert not result.diagnostics.matched_terms
        if mode == "lexical":
            assert not result.hits
        else:
            assert result.hits[0].semantic_score == pytest.approx(1.0)


def test_lexical_search_works_without_vectors_or_model(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "guard.py").write_bytes(b"def check_tenant():\n    return True\n")
    index_repository(tmp_path, code_aware=True)

    def unexpected_model():
        pytest.fail("Lexical search initialized an embedding backend")

    monkeypatch.setattr(retrieval, "MiniLMEmbeddings", unexpected_model)
    service = RetrievalService(tmp_path)
    [hit] = service.search(service.repository_id, "check_tenant", 1, mode="lexical").hits
    assert hit.chunk.symbol_name == "check_tenant"


def test_cache_refresh_and_snapshot_consistency(indexed) -> None:
    root, backend, service = indexed
    repository_id = service.repository_id
    original = service.search(repository_id, "checkTenant", 1, mode="lexical").hits[0]
    unchanged = build_semantic_index(root, embeddings=backend)
    assert unchanged.embedded == 0
    assert service.search(repository_id, "checkTenant", 1, mode="lexical").hits[0] == original

    (root / "guard.py").write_bytes(b"def checkTenant():\n    return 'changed tenant'\n")
    (root / "extra.py").write_bytes(b"def send_notice():\n    return 1\n")
    (root / "export.py").unlink()
    for mode in ("semantic", "lexical", "hybrid"):
        with pytest.raises(ValueError, match="stale"):
            service.search(repository_id, "checkTenant", mode=mode)

    # Simulate a fresh source manifest ahead of the last committed vectors.
    index_repository(root, code_aware=True)
    updated = service.search(repository_id, "checkTenant", 1, mode="lexical").hits[0]
    assert updated.chunk.chunk_id != original.chunk.chunk_id
    assert "changed tenant" in updated.chunk.content
    with pytest.raises(ValueError, match="stale"):
        service.search(repository_id, "checkTenant", mode="hybrid")
    build_semantic_index(root, embeddings=backend, rebuild=True)
    hits = service.search(repository_id, "send_notice", mode="hybrid", top_k=100).hits
    assert hits[0].chunk.relative_path == "extra.py"
    assert all(hit.chunk.relative_path != "export.py" for hit in hits)


def test_corrupt_index_and_incompatible_vectors(indexed) -> None:
    root, backend, service = indexed
    directory = index_directory(service.root, service.repository_id)
    pointer = directory / "vectors.json"
    pointer.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid vector index"):
        service.search(service.repository_id, "tenant", mode="hybrid")
    assert service.search(service.repository_id, "tenant", mode="lexical").hits
    build_semantic_index(root, embeddings=backend, rebuild=True)
    other = LocalVectors()
    other.identity = "another-model"
    incompatible = RetrievalService(root, other)
    with pytest.raises(ValueError, match="incompatible"):
        incompatible.search(service.repository_id, "tenant", mode="hybrid")
    (directory / "semantic-manifest.json").unlink()
    with pytest.raises(ValueError, match="No semantic source"):
        service.search(service.repository_id, "tenant", mode="lexical")


def test_isolation_empty_index_and_invalid_input(indexed, tmp_path: Path) -> None:
    _, backend, service = indexed
    empty = tmp_path / "empty"
    empty.mkdir()
    other = RetrievalService(empty, backend)
    with pytest.raises(ValueError, match="Repository ID"):
        other.search(service.repository_id, "tenant", mode="lexical")
    with pytest.raises(ValueError, match="No semantic index"):
        other.search(other.repository_id, "tenant", mode="lexical")
    build_semantic_index(empty, embeddings=backend)
    assert not other.search(other.repository_id, "tenant", mode="hybrid").hits
    for query, limit, error in [(" ", 5, "empty"), ("tenant", 0, "Top-K"), ("tenant", 101, "Top-K")]:
        with pytest.raises(ValueError, match=error):
            service.search(service.repository_id, query, limit)


def test_cli_default_and_hybrid_provenance(indexed, monkeypatch, capsys) -> None:
    root, backend, _ = indexed
    monkeypatch.setattr(retrieval, "MiniLMEmbeddings", lambda: backend)
    args = ["search", "--path", str(root), "--query", "checkTenant", "--top-k", "1"]
    assert main(args) == 0
    semantic = capsys.readouterr().out
    assert "Semantic search" in semantic and "cosine similarity" in semantic
    assert main([*args, "--mode", "hybrid"]) == 0
    hybrid = capsys.readouterr().out
    assert "Guard.checkTenant" in hybrid and "BM25" in hybrid and "RRF" in hybrid


def test_search_cli_distinguishes_low_overlap_from_no_matches(indexed, monkeypatch, capsys) -> None:
    root, backend, _ = indexed
    monkeypatch.setattr(retrieval, "MiniLMEmbeddings", lambda: backend)
    args = ["search", "--path", str(root), "--query", "vulkan shader renderer"]
    assert main([*args, "--mode", "hybrid"]) == 0
    hybrid = capsys.readouterr().out
    assert "Low lexical overlap" in hybrid and "No matching source excerpts" not in hybrid
    assert main([*args, "--mode", "lexical"]) == 0
    assert "No matching source excerpts were retrieved." in capsys.readouterr().out


def test_evaluation_metrics_and_missing_ground_truth(indexed) -> None:
    _, _, service = indexed
    dataset = Dataset.model_validate({
        "positives": [{"id": "a", "category": "identifier", "query": "checkTenant",
                       "relevant": [{"path": "guard.py", "symbol": "Guard.checkTenant"}]}],
        "negatives": [{"id": "b", "category": "unsupported", "query": "Vulkan shader"}],
    })
    report = evaluate(service, dataset)
    for mode in ("semantic", "lexical", "hybrid"):
        metrics = report["modes"][mode]
        assert metrics["recall_at_1"] == metrics["recall_at_5"] == metrics["mrr_at_5"] == 1.0
        assert metrics["negatives_flagged"] == 1
        assert metrics["median_ms"] >= 0
    dataset.positives[0].relevant[:] = [Location(path="missing.py")]
    with pytest.raises(ValueError, match="labels missing"):
        evaluate(service, dataset)
