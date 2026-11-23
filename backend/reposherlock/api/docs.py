"""Serve interactive API documentation with local-only scripts."""

import stat
from pathlib import Path
from secrets import token_urlsafe
from typing import Literal

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse

from reposherlock.api.errors import ApiError
from reposherlock.api.repositories import inspect_directory
from reposherlock.ingestion.scanner import is_link

Asset = Literal["swagger-ui-bundle.js", "swagger-ui.css"]


def add_docs(app: FastAPI, assets: Path | None) -> None:
    @app.get("/docs", include_in_schema=False)
    def swagger() -> HTMLResponse:
        if assets is None:
            return HTMLResponse(
                "<h1>RepoSherlock API</h1><p><a href='/openapi.json'>OpenAPI schema</a></p>"
                "<p>Set REPOSHERLOCK_SWAGGER_ASSETS to a trusted local Swagger UI distribution "
                "and restart to enable the interactive explorer.</p>",
                headers={"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"},
            )
        nonce = token_urlsafe(24)
        response = get_swagger_ui_html(
            openapi_url="/openapi.json", title="RepoSherlock API",
            swagger_js_url="/docs/assets/swagger-ui-bundle.js", swagger_css_url="/docs/assets/swagger-ui.css",
            swagger_favicon_url="data:,", swagger_ui_parameters={"validatorUrl": None, "persistAuthorization": False},
        )
        html = response.body.decode("utf-8").replace("<script>", f'<script nonce="{nonce}">')
        return HTMLResponse(html, headers={
            "Content-Security-Policy": f"default-src 'none'; script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
        })

    @app.get("/docs/assets/{name}", include_in_schema=False)
    def asset(name: Asset) -> FileResponse:
        missing = ApiError(404, "docs_asset_missing", "The requested local Swagger UI asset is unavailable.")
        if assets is None:
            raise missing
        try:
            directory, _ = inspect_directory(assets)
            path = directory / name
            info = path.lstat()
            if is_link(info) or not stat.S_ISREG(info.st_mode):
                raise missing
        except (ApiError, OSError):
            raise missing from None
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})
