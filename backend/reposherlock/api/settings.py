"""Process-local HTTP settings loaded from the server environment."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from reposherlock.api.errors import ApiError
from reposherlock.api.repositories import inspect_directory, local_path

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def permitted_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        host = "[::1]" if hostname == "::1" else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return (parsed.scheme in {"http", "https"} and hostname in LOCAL_HOSTS
                and origin == f"{parsed.scheme}://{host}{port}")
    except ValueError:
        return False


@dataclass(frozen=True)
class ApiSettings:
    allowed_roots: tuple[Path, ...] = ()
    frontend_origins: tuple[str, ...] = ()
    session_token: str = field(default="", repr=False)
    preview_ttl_seconds: int = 300
    swagger_assets: Path | None = None

    def __post_init__(self) -> None:
        if len(self.allowed_roots) > 16:
            raise ValueError("Configure at most 16 allowed roots.")
        for root in self.allowed_roots:
            try:
                if local_path(str(root)) == Path(root.anchor):
                    raise ValueError("Allowed roots must be narrower than a filesystem root.")
                inspect_directory(root)
            except ApiError:
                raise ValueError("Each allowed root must be a real, accessible local directory.") from None
        if len(self.frontend_origins) > 16 or not all(map(permitted_origin, self.frontend_origins)):
            raise ValueError("Frontend origins must be explicit loopback HTTP(S) origins.")
        if self.session_token and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.session_token):
            raise ValueError("The local session token must be a 32–128 character URL-safe random value.")
        if not 1 <= self.preview_ttl_seconds <= 600:
            raise ValueError("The preview lifetime must be between 1 and 600 seconds.")
        if self.swagger_assets is not None:
            try:
                inspect_directory(local_path(str(self.swagger_assets)))
            except ApiError:
                raise ValueError("The Swagger asset directory must be an accessible local directory.") from None

    @classmethod
    def from_environment(cls) -> "ApiSettings":
        values = os.environ.get("REPOSHERLOCK_ALLOWED_ROOTS")
        try:
            roots = tuple(local_path(value.strip()) for value in values.split(os.pathsep) if value.strip()) if values is not None else (
                (DEFAULT_FIXTURES,) if DEFAULT_FIXTURES.is_dir() else ()
            )
        except ApiError:
            raise ValueError("REPOSHERLOCK_ALLOWED_ROOTS must contain absolute local directories.") from None
        origins = os.environ.get("REPOSHERLOCK_FRONTEND_ORIGINS", "")
        swagger = os.environ.get("REPOSHERLOCK_SWAGGER_ASSETS", "").strip()
        return cls(
            allowed_roots=roots, frontend_origins=tuple(value.strip() for value in origins.split(",") if value.strip()),
            session_token=os.environ.get("REPOSHERLOCK_SESSION_TOKEN", ""),
            swagger_assets=Path(swagger) if swagger else None,
        )
