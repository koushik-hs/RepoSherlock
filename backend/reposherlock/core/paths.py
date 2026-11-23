"""Filesystem paths for long Windows checkouts."""

import os
from pathlib import Path


WINDOWS_DIRECTORY_PATH_LIMIT = 248


def extended_path(path: Path) -> Path:
    """Give Win32 APIs an absolute path that can exceed MAX_PATH."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def filesystem_path(path: Path) -> Path:
    if len(str(path)) >= WINDOWS_DIRECTORY_PATH_LIMIT:
        return extended_path(path)
    return path
