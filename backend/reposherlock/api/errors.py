"""Fixed, client-facing API errors."""

from fastapi.responses import JSONResponse

from reposherlock.api.schemas import Problem, ProblemResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.problem = Problem(code=code, message=message)

    def response(self) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if self.status == 401 else None
        return JSONResponse(ProblemResponse(detail=self.problem).model_dump(), self.status, headers)


def repository_busy() -> ApiError:
    return ApiError(409, "repository_busy", "Repository work is in progress. Retry shortly.")


def snapshot_changed() -> ApiError:
    return ApiError(409, "snapshot_changed", "The repository or initial evidence changed. Refresh the index and create a new preview.")
