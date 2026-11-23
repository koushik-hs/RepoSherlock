"""HTTP entry point for the local RepoSherlock backend."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from reposherlock.api.docs import add_docs
from reposherlock.api.errors import ApiError
from reposherlock.api.investigations import Investigations
from reposherlock.api.jobs import Jobs
from reposherlock.api.routers import investigations, jobs, repositories
from reposherlock.api.schemas import ProblemResponse
from reposherlock.api.security import LocalRequests, require_session
from reposherlock.api.settings import ApiSettings
from reposherlock.api.workspace import Workspace
from reposherlock.investigation.provider import GeminiProvider
from reposherlock.semantic.embeddings import Embeddings


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["reposherlock"] = "reposherlock"


def create_app(settings: ApiSettings | None = None, *, embeddings: Embeddings | None = None,
               provider_factory: Callable[[], GeminiProvider] | None = None) -> FastAPI:
    settings = settings if settings is not None else ApiSettings.from_environment()
    factory = provider_factory if provider_factory is not None else GeminiProvider.from_environment

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        manager = Jobs()
        try:
            workspace = Workspace(settings, manager, embeddings)
            application.state.jobs = manager
            application.state.workspace = workspace
            application.state.investigations = Investigations(workspace, settings.preview_ttl_seconds, factory)
            yield
        finally:
            await run_in_threadpool(manager.close)

    application = FastAPI(title="RepoSherlock", lifespan=lifespan, docs_url=None, redoc_url=None)
    application.state.settings = settings
    application.add_middleware(LocalRequests, settings=settings)
    if settings.frontend_origins:
        application.add_middleware(
            CORSMiddleware, allow_origins=list(settings.frontend_origins), allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"], allow_credentials=False,
        )

    @application.exception_handler(ApiError)
    async def api_error(_: Request, error: ApiError):
        return error.response()

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError):
        return ApiError(422, "invalid_request", "The JSON or route parameters do not match the API schema.").response()

    @application.get("/api/health")
    async def health() -> HealthResponse:
        return HealthResponse()

    secure = APIRouter(
        prefix="/api", dependencies=[Depends(require_session)],
        responses={status: {"model": ProblemResponse} for status in (400, 401, 403, 404, 409, 410, 413, 415, 422, 429, 503)},
    )
    for router in (repositories.router, jobs.router, investigations.router):
        secure.include_router(router)
    application.include_router(secure)
    add_docs(application, settings.swagger_assets)
    return application


app = create_app()
