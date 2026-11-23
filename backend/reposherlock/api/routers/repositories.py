"""Repository registration and repository-scoped operations."""

from fastapi import APIRouter, Depends

from reposherlock.api.dependencies import workspace
from reposherlock.api.schemas import (
    IndexRequest, JobAccepted, RepositoryId, RepositoryRequest, RepositoryResponse, SearchRequest, SearchResponse,
)
from reposherlock.api.workspace import Workspace

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post("")
def register(body: RepositoryRequest, service: Workspace = Depends(workspace)) -> RepositoryResponse:
    service.privacy.reject_credentials(body.path)
    return service.describe(service.repositories.add(body.path))


@router.get("/{repository_id}")
def status(repository_id: RepositoryId, service: Workspace = Depends(workspace)) -> RepositoryResponse:
    return service.describe(service.repositories.get(repository_id))


@router.post("/{repository_id}/index", status_code=202)
def index(repository_id: RepositoryId, body: IndexRequest,
          service: Workspace = Depends(workspace)) -> JobAccepted:
    return JobAccepted(job_id=service.index(service.repositories.get(repository_id), body))


@router.post("/{repository_id}/search")
def search(repository_id: RepositoryId, body: SearchRequest,
           service: Workspace = Depends(workspace)) -> SearchResponse:
    return service.search(service.repositories.get(repository_id), body)
