"""Access the process-local API services."""

from fastapi import Request

from reposherlock.api.investigations import Investigations
from reposherlock.api.jobs import Jobs
from reposherlock.api.workspace import Workspace


def workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def jobs(request: Request) -> Jobs:
    return request.app.state.jobs


def investigations(request: Request) -> Investigations:
    return request.app.state.investigations
