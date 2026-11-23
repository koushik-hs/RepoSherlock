"""A bounded, process-local worker and job history."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import uuid4

from reposherlock.api.errors import ApiError
from reposherlock.api.schemas import (
    IndexProgressResponse, IndexResultResponse, InvestigationResponse, JobResponse, Problem, TraceResponse,
)

MAX_OUTSTANDING = 4
MAX_RETAINED = 64
Pending = ("queued", "running")
Kind = Literal["index", "investigation"]


@dataclass(frozen=True)
class InvestigationOutput:
    response: InvestigationResponse
    trace: TraceResponse


@dataclass(frozen=True)
class Job:
    view: JobResponse
    investigation: InvestigationOutput | None = None


Work = Callable[[str], IndexResultResponse | InvestigationOutput]


class Jobs:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reposherlock")
        self._lock = Lock()
        self._entries: dict[str, Job] = {}
        self._closed = False

    def submit(self, repo_id: str, kind: Kind, work: Work,
               initial: Callable[[str], InvestigationOutput] | None = None) -> str:
        with self._lock:
            if self._closed:
                raise ApiError(503, "worker_unavailable", "The local worker is shutting down.")
            pending = [job.view for job in self._entries.values() if job.view.state in Pending]
            if kind == "index" and any(job.kind == kind and job.repository_id == repo_id for job in pending):
                raise ApiError(409, "index_already_queued", "An index job is already active for this repository.")
            if len(pending) >= MAX_OUTSTANDING:
                raise ApiError(429, "job_queue_full", "The local worker queue is full. Retry after a job finishes.")
            if len(self._entries) >= MAX_RETAINED:
                oldest = next(key for key, job in self._entries.items() if job.view.state not in Pending)
                del self._entries[oldest]
            key = uuid4().hex
            view = JobResponse(job_id=key, repository_id=repo_id, kind=kind, state="queued",
                               created_at=datetime.now(UTC))
            self._entries[key] = Job(view, initial(key) if initial else None)
            self._executor.submit(self._run, key, work)
            return key

    def get(self, key: str, *, kind: Kind | None = None) -> Job:
        with self._lock:
            job = self._entries.get(key)
        if job is None or (kind and job.view.kind != kind):
            raise ApiError(404, "job_unknown", "The job is unknown or its process-local result expired.")
        return job

    def progress(self, key: str, progress: IndexProgressResponse) -> None:
        with self._lock:
            job = self._entries[key]
            if job.view.state == "running":
                self._update(job, progress=progress)

    def _update(self, job: Job, **fields) -> None:
        self._entries[job.view.job_id] = replace(job, view=job.view.model_copy(update=fields))

    def _run(self, key: str, work: Work) -> None:
        with self._lock:
            job = self._entries[key]
            if job.view.state != "queued":
                return
            self._update(job, state="running", started_at=datetime.now(UTC))
        result = error = None
        try:
            result = work(key)
            if isinstance(result, InvestigationOutput):
                error = result.response.error
        except ApiError as failure:
            error = failure.problem
        except Exception:
            error = Problem(code="job_failed", message="The local job could not be completed.")
        with self._lock:
            job = self._entries[key]
            if isinstance(result, InvestigationOutput):
                job = replace(job, investigation=result)
            self._update(job, state="failed" if error else "completed", error=error,
                         finished_at=datetime.now(UTC),
                         index_result=result if isinstance(result, IndexResultResponse) else None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for job in self._entries.values():
                if job.view.state == "queued":
                    self._update(job, state="failed", finished_at=datetime.now(UTC),
                                 error=Problem(code="server_shutdown", message="The local server shut down before the job ran."))
        self._executor.shutdown(wait=True, cancel_futures=True)
