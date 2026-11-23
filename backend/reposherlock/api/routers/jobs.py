"""Poll background work."""

from fastapi import APIRouter, Depends

from reposherlock.api.dependencies import jobs
from reposherlock.api.jobs import Jobs
from reposherlock.api.schemas import JobResponse, RecordId

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def status(job_id: RecordId, service: Jobs = Depends(jobs)) -> JobResponse:
    return service.get(job_id).view
