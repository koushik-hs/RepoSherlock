"""Reuse the scanner, semantic index and retrieval pipeline for HTTP jobs."""

from contextlib import contextmanager
from dataclasses import asdict
from threading import Lock
from time import perf_counter

from reposherlock.api.errors import ApiError, repository_busy
from reposherlock.api.jobs import Jobs
from reposherlock.api.privacy import Privacy
from reposherlock.api.repositories import RegisteredRepository, Repositories
from reposherlock.api.schemas import (
    IndexProgressResponse, IndexRequest, IndexResultResponse, RepositoryResponse, SearchHit, SearchRequest,
    SearchResponse, Snapshot, SnapshotStatus,
)
from reposherlock.api.settings import ApiSettings
from reposherlock.ingestion.indexer import index_directory
from reposherlock.retrieval.service import RetrievalService
from reposherlock.semantic.embeddings import Embeddings, MiniLMEmbeddings
from reposherlock.semantic.storage import collection_for, load_vector_state, open_database, verified_snapshot

MAX_SEARCH_SNIPPET_CHARS = 6000


class Workspace:
    def __init__(self, settings: ApiSettings, jobs: Jobs, embeddings: Embeddings | None) -> None:
        self.repositories = Repositories(settings.allowed_roots)
        self.embeddings = embeddings if embeddings is not None else MiniLMEmbeddings(local_only=True)
        self.privacy = Privacy(settings.session_token)
        self.jobs = jobs
        self._lane = Lock()

    @contextmanager
    def lane(self, *, wait: bool = False):
        if not self._lane.acquire(blocking=wait):
            raise repository_busy()
        try:
            yield
        finally:
            self._lane.release()

    def snapshot_status(self, entry: RegisteredRepository) -> tuple[SnapshotStatus, Snapshot | None]:
        root = self.repositories.checked_path(entry)
        try:
            directory = index_directory(root, entry.id, create=False)
            if load_vector_state(directory) is None:
                return "not_indexed", None
            with verified_snapshot(root, entry.id) as (directory, source):
                state = load_vector_state(directory)
                if state is None:
                    return "not_indexed", None
                if not state.matches(source):
                    return "stale", None
                if not state.compatible(self.embeddings):
                    return "invalid", None
                collection_for(open_database(directory), state)
                return "ready", Snapshot(source_sha256=source.source_sha256, chunks_sha256=source.chunks_sha256,
                                         indexed_chunks=source.count)
        except FileNotFoundError:
            return "not_indexed", None
        except ValueError as error:
            if str(error).startswith("Semantic index is busy"):
                return "busy", None
            return ("stale" if str(error).startswith("Semantic index is stale.") else "invalid"), None
        except (OSError, RuntimeError):
            return "invalid", None

    def require_snapshot(self, entry: RegisteredRepository) -> Snapshot:
        status, snapshot = self.snapshot_status(entry)
        if status == "busy":
            raise repository_busy()
        if snapshot is None:
            raise ApiError(409, f"index_{status}", "Build or refresh the repository's semantic index first.")
        return snapshot

    def describe(self, entry: RegisteredRepository) -> RepositoryResponse:
        try:
            with self.lane():
                status, snapshot = self.snapshot_status(entry)
        except ApiError as error:
            if error.problem.code != "repository_busy":
                raise
            status, snapshot = "busy", None
        return RepositoryResponse(
            repository_id=entry.id, display_name=self.privacy.text(entry.path.name), path=self.privacy.text(entry.label),
            index_available=None if status == "busy" else status == "ready", snapshot_status=status, snapshot=snapshot,
        )

    def index(self, entry: RegisteredRepository, request: IndexRequest) -> str:
        self.repositories.checked_path(entry)

        def work(key: str) -> IndexResultResponse:
            with self.lane(wait=True):
                from reposherlock.semantic.indexing import build_semantic_index

                path = self.repositories.checked_path(entry)
                try:
                    stats = build_semantic_index(
                        path, embeddings=self.embeddings, rebuild=request.rebuild,
                        on_progress=lambda update: self.jobs.progress(key, IndexProgressResponse(**asdict(update))),
                    )
                except (ValueError, OSError, RuntimeError) as error:
                    if str(error).startswith("Semantic index is busy"):
                        raise repository_busy() from None
                    raise ApiError(409, "index_failed", "Indexing failed. Check the local model cache, repository access, and index state.") from None
                return IndexResultResponse(**asdict(stats))

        return self.jobs.submit(entry.id, "index", work)

    def search(self, entry: RegisteredRepository, request: SearchRequest) -> SearchResponse:
        self.privacy.reject_credentials(request.query)
        query = self.privacy.text(request.query)
        with self.lane():
            snapshot = self.require_snapshot(entry)
            root = self.repositories.checked_path(entry)
            started = perf_counter()
            try:
                result = RetrievalService(root, self.embeddings).search(
                    entry.id, query, request.top_k, mode=request.mode,
                )
            except (OSError, ValueError, RuntimeError):
                raise ApiError(409, "search_failed", "Search could not read the current verified index. Check repository status and retry.") from None
            if snapshot.digests != (result.source_sha256, result.chunks_sha256):
                raise ApiError(409, "index_changed", "The index changed during search. Retry against the current index.")
            hits = []
            for hit in result.hits:
                chunk = hit.chunk
                snippet = self.privacy.text(chunk.content)
                hits.append(SearchHit(
                    chunk_id=chunk.chunk_id, rank=hit.rank, language=chunk.language,
                    file_path=self.privacy.text(chunk.relative_path),
                    symbol_name=self.privacy.text(chunk.symbol_name) if chunk.symbol_name else None,
                    symbol_type=self.privacy.text(chunk.symbol_type) if chunk.symbol_type else None,
                    start_line=chunk.start_line, end_line=chunk.end_line, snippet=snippet[:MAX_SEARCH_SNIPPET_CHARS],
                    snippet_truncated=len(snippet) > MAX_SEARCH_SNIPPET_CHARS,
                    semantic_score=hit.semantic_score, semantic_rank=hit.semantic_rank,
                    bm25_score=hit.bm25_score, bm25_rank=hit.bm25_rank, rrf_score=hit.rrf_score,
                ))
            return SearchResponse(
                repository_id=entry.id, mode=result.mode, snapshot=snapshot, hits=hits,
                diagnostics=self.privacy.data(asdict(result.diagnostics)),
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
