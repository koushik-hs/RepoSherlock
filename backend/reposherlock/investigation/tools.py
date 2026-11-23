"""The read-only tool allowlist for one verified repository snapshot."""

import hashlib
import io
import json
import re
from dataclasses import dataclass
from itertools import islice
from typing import Annotated
from unicodedata import category

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator

from reposherlock.ingestion.models import SemanticChunk, SourceFile
from reposherlock.retrieval.models import RetrievalMode, RetrievalResponse
from reposherlock.retrieval.service import RetrievalService
from reposherlock.semantic.storage import SourceSnapshot, read_chunks, verified_snapshot

MAX_READ_CHARS = 6000
MAX_TOOL_RESULTS = 5
RESERVED_PATH = re.compile(r"(?i)(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[0-9¹²³])$")


class ToolRejected(ValueError):
    """A tool request was invalid or could not be fulfilled safely."""


class ToolArguments(BaseModel, strict=True, extra="forbid"):
    pass


class SearchCode(ToolArguments):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    mode: RetrievalMode = "hybrid"
    top_k: int = Field(default=3, ge=1, le=MAX_TOOL_RESULTS)


class ReadCodeLines(ToolArguments):
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1, le=1_000_000)
    end_line: int = Field(ge=1, le=1_000_000)

    @field_validator("path")
    @classmethod
    def _safe_path(cls, path: str) -> str:
        if (
            any(char in '\\:*?"<>|' or category(char).startswith("C") for char in path)
            or re.search(r"%[0-9A-Fa-f]{2}", path)
            or any(
                part in ("", ".", "..") or part.endswith((".", " "))
                or RESERVED_PATH.fullmatch(part.split(".", 1)[0].rstrip(" "))
                for part in path.split("/")
            )
        ):
            raise ValueError("Use the exact indexed relative path with forward slashes.")
        return path


class FindSymbol(ToolArguments):
    symbol: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]

    @field_validator("symbol")
    @classmethod
    def _safe_symbol(cls, symbol: str) -> str:
        if any(category(char).startswith("C") for char in symbol):
            raise ValueError("Use a literal symbol name.")
        return symbol


TOOL_MODELS: dict[str, type[ToolArguments]] = {
    "search_code": SearchCode,
    "read_code_lines": ReadCodeLines,
    "find_symbol": FindSymbol,
}
TOOL_DESCRIPTIONS = {
    "search_code": "Find indexed code with hybrid, lexical, or semantic search. Return up to five excerpts.",
    "read_code_lines": (
        "Read an inclusive line range from an exact indexed relative path using forward slashes. "
        "If the end passes EOF, return the available lines and their actual range."
    ),
    "find_symbol": "Locate an exact qualified symbol or an unqualified name. Multiple matches may be returned.",
}


@dataclass(frozen=True)
class ToolEvidence:
    chunks: tuple[SemanticChunk, ...]
    ambiguous: bool = False
    truncated: bool = False
    returned_range: tuple[int, int] | None = None


def validate_tool(name: str, arguments: object) -> ToolArguments:
    model = TOOL_MODELS.get(name)
    if model is None:
        raise ToolRejected("tool_not_allowed")
    try:
        return model.model_validate(arguments)
    except ValidationError:
        raise ToolRejected("invalid_arguments") from None


class SourceTools:
    def __init__(self, retriever: RetrievalService, baseline: RetrievalResponse, max_read_lines: int) -> None:
        if type(max_read_lines) is not int or not 1 <= max_read_lines <= 150:
            raise ValueError("Tool line limit must be between 1 and 150.")
        if baseline.repository_id != retriever.repository_id:
            raise ValueError("The tool snapshot belongs to a different repository.")
        self.retriever = retriever
        self.baseline = baseline
        self.max_read_lines = max_read_lines
        self.retrievals = 0

    def _check(self, snapshot: SourceSnapshot | RetrievalResponse) -> None:
        if (snapshot.repository_id, snapshot.source_sha256, snapshot.chunks_sha256) != (
            self.baseline.repository_id, self.baseline.source_sha256, self.baseline.chunks_sha256,
        ):
            raise ValueError("The investigation snapshot changed. Refresh the index and retry.")

    def dispatch(self, arguments: ToolArguments) -> ToolEvidence:
        if isinstance(arguments, SearchCode):
            return self._search(arguments)
        if isinstance(arguments, ReadCodeLines):
            return self._read(arguments)
        if isinstance(arguments, FindSymbol):
            return self._find(arguments)
        raise ToolRejected("tool_not_allowed")

    def _search(self, args: SearchCode) -> ToolEvidence:
        self.retrievals += 1
        results = self.retriever.search(
            self.baseline.repository_id, args.query, args.top_k, mode=args.mode,
        )
        self._check(results)
        return ToolEvidence(tuple(hit.chunk for hit in results.hits))

    def _read(self, args: ReadCodeLines) -> ToolEvidence:
        count = args.end_line - args.start_line + 1
        if not 1 <= count <= self.max_read_lines:
            raise ToolRejected("line_limit")
        captured: list[SourceFile] = []
        with verified_snapshot(
            self.retriever.root, self.baseline.repository_id, capture_path=args.path, captured=captured,
        ) as (directory, snapshot):
            self._check(snapshot)
            if not captured:
                raise ToolRejected("path_not_indexed")
            source = captured[0]
            lines = list(islice(io.StringIO(source.content, newline=""), args.start_line - 1, args.end_line))
            if not lines:
                raise ToolRejected("range_outside_source")
            end_line = args.start_line + len(lines) - 1
            content = "".join(lines)
            if len(content) > MAX_READ_CHARS:
                raise ToolRejected("character_limit")
            symbols = {
                (chunk.symbol_name, chunk.symbol_type)
                for chunk in read_chunks(directory, snapshot)
                if chunk.relative_path == args.path and chunk.symbol_name
                and chunk.start_line <= args.start_line <= end_line <= chunk.end_line
            }
        name, kind = next(iter(symbols)) if len(symbols) == 1 else (None, None)
        identity = json.dumps([args.path, args.start_line, end_line, content]).encode("ascii")
        return ToolEvidence((SemanticChunk(
            hashlib.sha256(identity).hexdigest(), args.path, source.language, args.start_line, end_line,
            content, 0, len(lines[-1].encode("utf-8")), "verified_lines", name, kind,
        ),), truncated=end_line < args.end_line, returned_range=(args.start_line, end_line))

    def _find(self, args: FindSymbol) -> ToolEvidence:
        matched: list[SemanticChunk] = []
        names: set[tuple[str, str]] = set()
        truncated = False
        with verified_snapshot(self.retriever.root, self.baseline.repository_id) as (directory, snapshot):
            self._check(snapshot)
            for chunk in read_chunks(directory, snapshot):
                name = chunk.symbol_name
                if not name or (name != args.symbol and name.rsplit(".", 1)[-1] != args.symbol):
                    continue
                if len(names) < 2:
                    names.add((chunk.relative_path, name))
                if len(matched) < MAX_TOOL_RESULTS:
                    matched.append(chunk)
                else:
                    truncated = True
        return ToolEvidence(tuple(matched), ambiguous=len(names) > 1, truncated=truncated)
