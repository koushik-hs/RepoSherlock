import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from reposherlock.ingestion.indexer import index_directory, index_repository
from reposherlock.ingestion.scanner import validate_repository
from reposherlock.semantic import embeddings, indexing
from reposherlock.semantic.embeddings import MiniLMEmbeddings, checked_embeddings
from reposherlock.semantic.indexing import build_semantic_index
from reposherlock.semantic.retrieval import SemanticSearch
from reposherlock.semantic.storage import load_vector_state, open_database, repository_lock


def test_minilm_offline_cache_requires_only_the_pinned_allowlist(tmp_path, monkeypatch):
    for name in embeddings.MODEL_FILES:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()

    def snapshot_download(model, **kwargs):
        assert model == embeddings.MODEL
        assert kwargs == {
            "revision": embeddings.REVISION, "allow_patterns": list(embeddings.MODEL_FILES),
            "local_files_only": True, "token": False,
        }
        return str(tmp_path)

    def sentence_transformer(path, **kwargs):
        assert path == str(tmp_path)
        assert kwargs["local_files_only"] and not kwargs["trust_remote_code"]
        assert kwargs["model_kwargs"]["use_safetensors"]
        return SimpleNamespace(max_seq_length=256, get_embedding_dimension=lambda: 384)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download))
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", SimpleNamespace(LocalEntryNotFoundError=FileNotFoundError))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=sentence_transformer))
    assert embeddings._load_model.__wrapped__(local_only=True).max_seq_length == embeddings.TOKEN_LIMIT


class CountingEmbeddings:
    identity = "test-embedding"
    dimension = 3

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [1.0, 0.0, 0.0] if "tenant" in text else
            [0.0, 1.0, 0.0] if "scan" in text else
            [0.0, 0.0, 1.0]
            for text in texts
        ]


@pytest.fixture
def search_repo(tmp_path: Path) -> Path:
    (tmp_path / "policy.py").write_bytes(b"def allow_tenant():\n    return True\n")
    (tmp_path / "scan.py").write_bytes(b"def scan_files():\n    return []\n")
    (tmp_path / "build.py").write_bytes(b"def build_index():\n    return None\n")
    return tmp_path


def test_real_database_reloads_in_another_process(search_repo: Path) -> None:
    backend = CountingEmbeddings()
    stats = build_semantic_index(search_repo, embeddings=backend, batch_size=2)
    assert [len(batch) for batch in backend.calls] == [2, 1]
    assert stats.total == stats.new == stats.embedded == 3
    code = """
import sys
from reposherlock.semantic.retrieval import SemanticSearch
class Embeddings:
    identity = "test-embedding"
    dimension = 3
    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]
service = SemanticSearch(sys.argv[1], Embeddings())
[hit] = service.search(sys.argv[2], "tenant access", 1)
print(hit.relative_path, hit.start_line, hit.end_line, hit.score)
"""
    run = subprocess.run(
        [sys.executable, "-c", code, str(search_repo), stats.repository_id],
        capture_output=True, text=True, timeout=30, check=True,
    )
    assert run.stdout.strip() == "policy.py 1 2 1.0"
    service = SemanticSearch(search_repo, backend)
    hits = service.search(stats.repository_id, "scan", 100)
    assert len(hits) == 3
    assert hits[0].symbol_name == "scan_files"
    assert hits[0].snippet == "def scan_files():\n    return []"
    assert hits[0].score > hits[1].score


def test_new_reused_updated_deleted_and_phase_one_compatibility(search_repo: Path) -> None:
    backend = CountingEmbeddings()
    legacy = index_repository(search_repo)
    initial = build_semantic_index(search_repo, embeddings=backend)
    directory = legacy.manifest_path.parent
    first_state = load_vector_state(directory)
    unchanged = build_semantic_index(search_repo, embeddings=backend)
    assert unchanged.reused == 3 and unchanged.embedded == 0
    assert load_vector_state(directory) == first_state
    assert len(backend.calls) == 1

    (search_repo / "policy.py").write_bytes(b"def allow_tenant():\n    return False\n")
    (search_repo / "build.py").unlink()
    (search_repo / "other.py").write_bytes(b"def other():\n    return 2\n")
    service = SemanticSearch(search_repo, backend)
    with pytest.raises(ValueError, match="stale"):
        service.search(initial.repository_id, "tenant")
    changed = build_semantic_index(search_repo, embeddings=backend)
    assert (changed.new, changed.reused, changed.updated, changed.removed, changed.embedded) == (
        1, 1, 1, 1, 2,
    )
    assert len(backend.calls) == 2
    state = load_vector_state(directory)
    assert state != first_state
    database = open_database(directory)
    [collection] = database.list_collections()
    assert collection.name == state.collection
    assert collection.count() == 3
    assert len(set(collection.get(include=[])["ids"])) == 3
    hits = service.search(changed.repository_id, "tenant")
    assert all(hit.relative_path != "build.py" for hit in hits)
    assert hits[0].snippet.endswith("False")
    assert json.loads(legacy.manifest_path.read_text())["schema_version"] == 1


def test_moved_symbol_reuses_its_embedding(search_repo: Path) -> None:
    backend = CountingEmbeddings()
    build_semantic_index(search_repo, embeddings=backend)
    target = search_repo / "policy.py"
    target.write_bytes(b"\n\n" + target.read_bytes())
    stats = build_semantic_index(search_repo, embeddings=backend)
    assert stats.embedded == 0
    assert len(backend.calls) == 1
    [hit] = SemanticSearch(search_repo, backend).search(stats.repository_id, "tenant", 1)
    assert hit.start_line == 3


def test_incompatible_model_and_dimension_require_rebuild(search_repo: Path) -> None:
    backend = CountingEmbeddings()
    stats = build_semantic_index(search_repo, embeddings=backend)
    different = CountingEmbeddings()
    different.identity = "different"
    with pytest.raises(ValueError, match="--rebuild"):
        build_semantic_index(search_repo, embeddings=different)
    with pytest.raises(ValueError, match="--rebuild"):
        SemanticSearch(search_repo, different).search(stats.repository_id, "tenant")
    different.identity = backend.identity
    different.dimension = 4
    with pytest.raises(ValueError, match="dimension"):
        build_semantic_index(search_repo, embeddings=different)
    different.identity = "different"
    different.dimension = 3
    rebuilt = build_semantic_index(search_repo, embeddings=different, rebuild=True)
    assert rebuilt.embedded == 3
    assert SemanticSearch(search_repo, different).search(stats.repository_id, "tenant", 1)


def test_failed_publication_preserves_previous_vectors_and_can_retry(
    search_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CountingEmbeddings()
    stats = build_semantic_index(search_repo, embeddings=backend)
    directory = index_directory(validate_repository(search_repo), stats.repository_id)
    previous = load_vector_state(directory)
    (search_repo / "scan.py").unlink()

    def fail_publication(*args: object) -> None:
        raise OSError("publication interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(indexing, "publish", fail_publication)
        with pytest.raises(OSError, match="interrupted"):
            build_semantic_index(search_repo, embeddings=backend)
    assert load_vector_state(directory) == previous
    service = SemanticSearch(search_repo, backend)
    with pytest.raises(ValueError, match="stale"):
        service.search(stats.repository_id, "tenant")
    retried = build_semantic_index(search_repo, embeddings=backend)
    assert retried.total == retried.reused == 2
    assert retried.removed == 1 and retried.embedded == 0
    assert len(open_database(directory).list_collections()) == 1


def test_repo_isolation_and_missing_index(search_repo: Path, tmp_path: Path) -> None:
    backend = CountingEmbeddings()
    first = tmp_path / "first"
    first.mkdir()
    (first / "admin.py").write_bytes(b"def tenant_admin():\n    return True\n")
    first_service = SemanticSearch(first, backend)
    with pytest.raises(ValueError, match="No semantic index"):
        first_service.search(first_service.repository_id, "tenant")
    own = tmp_path / "second"
    own.mkdir()
    (own / "scan.py").write_bytes(b"def scan():\n    return 1\n")
    a = build_semantic_index(first, embeddings=backend)
    b = build_semantic_index(own, embeddings=backend)
    assert a.repository_id != b.repository_id
    with pytest.raises(ValueError, match="Repository ID"):
        first_service.search(b.repository_id, "tenant")
    [hit] = SemanticSearch(own, backend).search(b.repository_id, "tenant")
    assert hit.relative_path == "scan.py"


def test_empty_and_invalid_queries(tmp_path: Path) -> None:
    backend = CountingEmbeddings()
    stats = build_semantic_index(tmp_path, embeddings=backend)
    service = SemanticSearch(tmp_path, backend)
    assert service.search(stats.repository_id, "anything") == []
    assert not backend.calls
    for value in (0, -1, 101):
        with pytest.raises(ValueError, match="Top-K"):
            service.search(stats.repository_id, "anything", value)
    with pytest.raises(ValueError, match="empty"):
        service.search(stats.repository_id, " \n ")


def test_search_rejects_corrupt_jsonl_and_concurrent_build(search_repo: Path) -> None:
    backend = CountingEmbeddings()
    stats = build_semantic_index(search_repo, embeddings=backend)
    directory = index_directory(validate_repository(search_repo), stats.repository_id)
    service = SemanticSearch(search_repo, backend)
    with repository_lock(directory):
        with pytest.raises(ValueError, match="busy"):
            service.search(stats.repository_id, "tenant")
    source = json.loads((directory / "semantic-manifest.json").read_text())
    with (directory / source["chunks_file"]).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="verification"):
        service.search(stats.repository_id, "tenant")


def test_embedding_validation_and_lazy_model_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeModel:
        def encode(self, texts: list[str], **options: object) -> "FakeModel":
            assert texts == ["scan files"] and options["batch_size"] == 2
            assert options["normalize_embeddings"] is True
            return self

        def tolist(self) -> list[list[float]]:
            return [[1.0] + [0.0] * 383]

    model_calls = []

    def load() -> FakeModel:
        model_calls.append(True)
        return FakeModel()

    monkeypatch.setattr(embeddings, "_load_model", load)
    backend = MiniLMEmbeddings(batch_size=2)
    assert backend.embed([]) == [] and not model_calls
    assert len(checked_embeddings(backend, ["scan files"])[0]) == 384
    assert len(model_calls) == 1
    invalid = CountingEmbeddings()
    invalid.dimension = 5
    with pytest.raises(RuntimeError, match="dimension"):
        checked_embeddings(invalid, ["scan"])
