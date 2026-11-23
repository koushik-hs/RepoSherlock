"""Discover supported local text files without following filesystem links."""

import re
import stat
from collections.abc import Iterator
from os import stat_result
from pathlib import Path

from reposherlock.core.config import ScanLimits
from reposherlock.core.paths import filesystem_path
from reposherlock.ingestion.models import SkippedEntry, SourceFile


LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c-header",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".sql": "sql",
    ".json": "json",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
}

EXCLUDED_DIRECTORIES = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    "coverage", ".next", ".reposherlock", ".pytest_cache", ".ssh", ".aws", ".gnupg",
})

SENSITIVE_NAMES = frozenset({
    ".env", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
    "credentials", "secrets", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "kubeconfig",
})
SENSITIVE_STEMS = frozenset({
    "credentials", "credential", "secrets", "secret", "service-account",
    "service_account", "serviceaccount", "application_default_credentials",
    "client_secret",
})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".ppk", ".p12", ".pfx", ".jks"})
BINARY_CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")
PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def is_link(info: stat_result) -> bool:
    """Also catch Windows junctions and other reparse points."""
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def validate_repository(path: Path | str) -> Path:
    path = Path(path).expanduser().absolute()
    try:
        access_path = filesystem_path(path)
        info = access_path.lstat()
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Repository path must be a real directory: {path}")
        return access_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"Repository path does not exist: {path}") from error
    except OSError as error:
        raise ValueError(f"Cannot access repository path {path}: {error}") from error


def _is_sensitive(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in SENSITIVE_NAMES
        or name.startswith((".env.", "credentials.", "secrets.", "client_secret_"))
        or (name.startswith("ssh_host_") and path.stem.casefold().endswith("_key"))
        or path.stem.casefold() in SENSITIVE_STEMS
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
    )


def _walk(root: Path) -> Iterator[tuple[Path, stat_result] | SkippedEntry]:
    pending = [iter((root,))]
    while pending:
        path = next(pending[-1], None)
        if path is None:
            pending.pop()
            continue
        relative_path = path.relative_to(root).as_posix()
        try:
            access_path = filesystem_path(path)
            info = access_path.lstat()
        except OSError:
            if path == root:
                raise
            yield SkippedEntry(relative_path, "unreadable_entry")
            continue
        if path == root and (is_link(info) or not stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Repository path must be a real directory: {root}")
        if is_link(info):
            yield SkippedEntry(relative_path, "symlink_or_reparse_point")
            continue
        if not stat.S_ISDIR(info.st_mode):
            yield path, info
            continue
        if path != root and path.name.casefold() in EXCLUDED_DIRECTORIES:
            yield SkippedEntry(relative_path, "excluded_directory", is_directory=True)
            continue
        try:
            children = sorted(
                (path / child.name for child in access_path.iterdir()),
                key=lambda child: child.name,
            )
            pending.append(iter(children))
        except OSError:
            if path == root:
                raise
            yield SkippedEntry(relative_path, "unreadable_directory", is_directory=True)


def scan_repository(
    path: Path | str, limits: ScanLimits = ScanLimits()
) -> Iterator[SourceFile | SkippedEntry]:
    root = validate_repository(path)
    return _scan(root, limits)


def _scan(root: Path, limits: ScanLimits) -> Iterator[SourceFile | SkippedEntry]:
    indexed_files = total_bytes = 0
    for entry in _walk(root):
        if isinstance(entry, SkippedEntry):
            yield entry
            continue
        path, info = entry
        relative_path = path.relative_to(root).as_posix()
        language = LANGUAGES.get(path.suffix.casefold())
        remaining_bytes = limits.max_total_bytes - total_bytes

        if not stat.S_ISREG(info.st_mode):
            reason = "non_regular_file"
        elif _is_sensitive(path):
            reason = "sensitive_filename"
        elif language is None:
            reason = "unsupported_extension"
        elif info.st_size > limits.max_file_bytes:
            reason = "file_size_limit"
        elif indexed_files >= limits.max_files:
            reason = "file_count_limit"
        elif info.st_size > remaining_bytes:
            reason = "total_byte_limit"
        else:
            try:
                # One extra byte detects growth without reading past the size budget.
                with filesystem_path(path).open("rb") as source:
                    raw = source.read(info.st_size + 1)
                if len(raw) > limits.max_file_bytes:
                    reason = "file_size_limit"
                elif len(raw) > remaining_bytes:
                    reason = "total_byte_limit"
                elif len(raw) != info.st_size:
                    reason = "file_changed_during_scan"
                elif PRIVATE_KEY_HEADER.match(raw.lstrip()):
                    reason = "private_key_content"
                elif BINARY_CONTROL_BYTES.search(raw):
                    reason = "binary_content"
                else:
                    content = raw.decode("utf-8")
                    indexed_files += 1
                    total_bytes += len(raw)
                    yield SourceFile(relative_path, language, content, len(raw))
                    continue
            except UnicodeDecodeError:
                reason = "invalid_utf8"
            except OSError:
                reason = "unreadable_file"
        yield SkippedEntry(relative_path, reason)
