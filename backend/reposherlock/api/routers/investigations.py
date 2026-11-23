"""Investigate only after preview and explicit approval."""

from fastapi import APIRouter, Depends

from reposherlock.api.dependencies import investigations, jobs
from reposherlock.api.investigations import Investigations
from reposherlock.api.jobs import Jobs
from reposherlock.api.schemas import (
    ApprovalRequest, InvestigationAccepted, InvestigationResponse, PreviewRequest, PreviewResponse,
    RecordId, TraceResponse,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("/preview")
def preview(body: PreviewRequest, service: Investigations = Depends(investigations)) -> PreviewResponse:
    return service.preview(body)


@router.post("", status_code=202)
def start(body: ApprovalRequest, service: Investigations = Depends(investigations)) -> InvestigationAccepted:
    identifier = service.start(body)
    return InvestigationAccepted(job_id=identifier, investigation_id=identifier)


@router.get("/{investigation_id}")
def result(investigation_id: RecordId, service: Jobs = Depends(jobs)) -> InvestigationResponse:
    job = service.get(investigation_id, kind="investigation")
    assert job.investigation is not None
    return job.investigation.response.model_copy(update={"state": job.view.state, "error": job.view.error})


@router.get("/{investigation_id}/trace")
def trace(investigation_id: RecordId, service: Jobs = Depends(jobs)) -> TraceResponse:
    job = service.get(investigation_id, kind="investigation")
    assert job.investigation is not None
    return job.investigation.trace.model_copy(update={"state": job.view.state})
