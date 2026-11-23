"""Allowlisted, registered local repository paths."""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from unicodedata import category

from reposherlock.api.errors import ApiError, snapshot_changed
from reposherlock.core.paths import filesystem_path
from reposherlock.ingestion.indexer import repository_identifier
from reposherlock.ingestion.scanner import EXCLUDED_DIRECTORIES, is_link, validate_repository
from reposherlock.investigation.tools import RESERVED_PATH

MAX_REPOSITORIES = 64


def local_path(raw: str) -> Path:
    invalid = ApiError(400, "invalid_path", "Supply an absolute local directory without traversal or device paths.")
    if not raw or raw != raw.strip() or any(category(char).startswith("C") for char in raw):
        raise invalid
    if os.name == "nt":
        normalized = raw.replace("\\", "/")
        if not re.match(r"^[A-Za-z]:/(?!/)", normalized):
            raise invalid
        tail = normalized[3:]
    else:
        if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
            raise invalid
        normalized, tail = raw, raw[1:]
    parts = tail.removesuffix("/").split("/") if tail else []
    if any(
        part in ("", ".", "..") or part.endswith((".", " "))
        or any(char in ':*?"<>|' for char in part)
        or RESERVED_PATH.fullmatch(part.split(".", 1)[0].rstrip(" "))
        for part in parts
    ):
        raise invalid
    return Path(normalized)


def inspect_directory(path: Path, *, check_readable: bool = False) -> tuple[Path, tuple[int, int]]:
    try:
        for part in (*reversed(path.parents), path):
            info = filesystem_path(part).lstat()
            if is_link(info):
                raise ApiError(403, "unsafe_path", "Linked directories and junctions are outside the API path policy.")
            if not stat.S_ISDIR(info.st_mode):
                raise ApiError(400, "invalid_path", "The repository path must name a directory.")
        root = validate_repository(path)
        if check_readable:
            with os.scandir(filesystem_path(path)) as entries:
                next(entries, None)
        return root, (info.st_dev, info.st_ino)
    except FileNotFoundError:
        raise ApiError(404, "repository_missing", "The local repository directory is missing.") from None
    except (OSError, ValueError):
        raise ApiError(403, "repository_inaccessible", "The server cannot access the repository directory.") from None


@dataclass(frozen=True)
class RegisteredRepository:
    id: str
    path: Path
    identity: tuple[int, int]
    root: Path
    label: str


class Repositories:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        self._roots = roots
        self._entries: dict[str, RegisteredRepository] = {}
        self._lock = Lock()

    def add(self, raw: str) -> RegisteredRepository:
        path = local_path(raw)
        permitted = [(position, root) for position, root in enumerate(self._roots, 1) if path.is_relative_to(root)]
        if not permitted:
            raise ApiError(403, "repository_outside_allowed_roots", "The repository must be inside a configured allowed root.")
        position, root = max(permitted, key=lambda entry: len(entry[1].parts))
        suffix = path.relative_to(root)
        if any(part.casefold() in EXCLUDED_DIRECTORIES for part in suffix.parts):
            raise ApiError(403, "unsafe_path", "The repository path enters an excluded directory.")
        canonical, identity = inspect_directory(path, check_readable=True)
        allowed, _ = inspect_directory(root)
        if not canonical.is_relative_to(allowed):
            raise ApiError(403, "repository_outside_allowed_roots", "The repository must be inside a configured allowed root.")
        identifier = repository_identifier(canonical)
        label = "/".join((f"root-{position}", *suffix.parts))
        entry = RegisteredRepository(identifier, path, identity, root, label)
        with self._lock:
            if identifier not in self._entries and len(self._entries) >= MAX_REPOSITORIES:
                raise ApiError(429, "registry_full", "The process-local repository registry is full.")
            previous = self._entries.setdefault(identifier, entry)
            if previous.identity != identity:
                raise snapshot_changed()
            return previous

    def get(self, identifier: str) -> RegisteredRepository:
        with self._lock:
            entry = self._entries.get(identifier)
        if entry is None:
            raise ApiError(404, "repository_unknown", "Register the repository before using it.")
        return entry

    def checked_path(self, entry: RegisteredRepository) -> Path:
        canonical, identity = inspect_directory(entry.path)
        allowed, _ = inspect_directory(entry.root)
        if (identity != entry.identity or repository_identifier(canonical) != entry.id
                or not canonical.is_relative_to(allowed)):
            raise snapshot_changed()
        return canonical
