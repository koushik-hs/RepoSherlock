"""Local origin, session, JSON and error-handling boundaries."""

from hmac import compare_digest

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from reposherlock.api.errors import ApiError
from reposherlock.api.settings import ApiSettings, permitted_origin

MAX_BODY_BYTES = 64 * 1024
bearer = HTTPBearer(auto_error=False, scheme_name="LocalSession")


def require_session(request: Request,
                    credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    configured = request.app.state.settings.session_token
    if not configured:
        raise ApiError(503, "local_session_unconfigured", "Configure a server-side local session token.")
    if (credentials is None or len(request.headers.getlist("authorization")) != 1
            or not compare_digest(credentials.credentials.encode("utf-8"), configured.encode("ascii"))):
        raise ApiError(401, "local_session_required", "Supply the configured local session bearer token.")


class LocalRequests:
    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_checked(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if scope["path"].startswith("/api/") and scope["path"] != "/api/health":
                    message = {**message, "headers": [*message["headers"], (b"cache-control", b"no-store")]}
            await send(message)

        try:
            headers = Headers(scope=scope)
            same_origin = f"{scope['scheme']}://{headers.get('host', '')}"
            if scope["path"] != "/api/health":
                if len(headers.getlist("host")) != 1 or not permitted_origin(same_origin):
                    raise ApiError(400, "invalid_host", "Use a loopback API host.")
            if scope["path"].startswith("/api/") and scope["path"] != "/api/health":
                origins = headers.getlist("origin")
                if (len(origins) > 1 or (origins and origins[0] not in (same_origin, *self.settings.frontend_origins))
                        or (not origins and headers.get("sec-fetch-site") == "cross-site")):
                    raise ApiError(403, "origin_forbidden", "The browser origin is outside the API allowlist.")
                if scope["method"] == "POST":
                    receive = await _bounded_json(headers, receive)
            await self.app(scope, receive, send_checked)
        except ApiError as error:
            if not started:
                await error.response()(scope, receive, send_checked)
        except Exception:
            # Exception strings can carry source text, host paths and credentials.
            if not started:
                error = ApiError(500, "internal_error", "The local operation could not be completed.")
                await error.response()(scope, receive, send_checked)


async def _bounded_json(headers: Headers, receive: Receive) -> Receive:
    content_type = headers.getlist("content-type")
    if (len(content_type) != 1 or content_type[0].split(";", 1)[0].strip().lower() != "application/json"
            or headers.get("content-encoding", "identity").lower() != "identity"):
        raise ApiError(415, "json_required", "Send an uncompressed application/json request body.")
    lengths = headers.getlist("content-length")
    if lengths:
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal() or len(lengths[0]) > 10:
            raise ApiError(400, "invalid_request", "The request has an invalid body length.")
        if int(lengths[0]) > MAX_BODY_BYTES:
            raise ApiError(413, "request_too_large", "JSON request bodies are limited to 64 KiB.")
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise ApiError(400, "invalid_request", "The request body did not complete.")
        data = message.get("body", b"")
        if len(body) + len(data) > MAX_BODY_BYTES:
            raise ApiError(413, "request_too_large", "JSON request bodies are limited to 64 KiB.")
        body.extend(data)
        if not message.get("more_body", False):
            break
    try:
        if b"\x00" in body:
            raise UnicodeError
        body.decode("utf-8")
    except UnicodeError:
        raise ApiError(400, "invalid_request", "The JSON request body must use UTF-8.") from None

    sent = False

    async def replay() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            data = bytes(body)
            body.clear()
            return {"type": "http.request", "body": data, "more_body": False}
        return await receive()

    return replay
