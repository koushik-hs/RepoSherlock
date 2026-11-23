"""Select and serialize the bounded evidence registry sent to the provider."""

import io
import json
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from reposherlock.ingestion.models import SemanticChunk
from reposherlock.investigation.redaction import MASK, redact
from reposherlock.retrieval.models import RetrievalHit

MAX_ISSUE_CHARS = 12_000
DEFAULT_CONTEXT_CHARS = 16_000
DEFAULT_MAX_SOURCES = 6


@dataclass(frozen=True)
class ContextLimits:
    max_chars: int = DEFAULT_CONTEXT_CHARS
    max_sources: int = DEFAULT_MAX_SOURCES

    def __post_init__(self) -> None:
        if type(self.max_chars) is not int or not 512 <= self.max_chars <= 32_000:
            raise ValueError("Context chars must be between 512 and 32000.")
        if type(self.max_sources) is not int or not 1 <= self.max_sources <= 12:
            raise ValueError("Max sources must be between 1 and 12.")


@dataclass(frozen=True)
class Evidence:
    source_id: str
    chunk_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int
    end_line: int
    snippet: str

    def outbound(self) -> dict:
        fields = asdict(self)
        del fields["chunk_id"]
        return fields


@dataclass(frozen=True)
class InvestigationContext:
    issue: str
    sources: tuple[Evidence, ...]
    payload: str


def validate_issue(issue: str) -> str:
    issue = issue.strip()
    if not issue:
        raise ValueError("Bug report must not be empty.")
    if len(issue) > MAX_ISSUE_CHARS:
        raise ValueError(f"Bug report exceeds {MAX_ISSUE_CHARS} characters.")
    return issue


def read_issue_file(path: Path) -> str:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Issue file must be a regular UTF-8 file.")
        with path.open("rb") as stream:
            data = stream.read(MAX_ISSUE_CHARS * 4 + 4)
    except OSError:
        raise ValueError("Cannot read the issue file.") from None
    if len(data) > MAX_ISSUE_CHARS * 4 + 3:
        raise ValueError(f"Issue file exceeds {MAX_ISSUE_CHARS} characters.")
    try:
        return validate_issue(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ValueError("Issue file must contain UTF-8 text.") from None


def _offset(chunk: SemanticChunk, point: tuple[int, int], lines: list[bytes]) -> int:
    line, column = point
    index = line - chunk.start_line
    return sum(map(len, lines[:index])) + column - (chunk.start_column if index == 0 else 0)


def _mostly_covered(chunk: SemanticChunk, previous: list[SemanticChunk], *, merge_overlaps: bool) -> bool:
    start, end = (chunk.start_line, chunk.start_column), (chunk.end_line, chunk.end_column)
    lines = [line.encode("utf-8") for line in io.StringIO(chunk.content, newline="")]
    size = sum(map(len, lines))
    spans: list[tuple[int, int]] = []
    for other in previous:
        if other.relative_path != chunk.relative_path:
            continue
        left = max(start, (other.start_line, other.start_column))
        right = min(end, (other.end_line, other.end_column))
        if right <= left:
            continue
        first, last = _offset(chunk, left, lines), _offset(chunk, right, lines)
        if (last - first) * 3 >= size * 2:
            return True
        if merge_overlaps:
            spans.append((first, last))
    covered = covered_end = 0
    for first, last in sorted(spans):
        covered += max(0, last - max(first, covered_end))
        covered_end = max(covered_end, last)
    return bool(spans) and covered * 3 >= size * 2


def _serialize(issue: str, sources: list[Evidence]) -> str:
    return json.dumps(
        {"issue": issue, "sources": [source.outbound() for source in sources]},
        ensure_ascii=True, separators=(",", ":"),
    )


def _line_prefix(chunk: SemanticChunk, max_bytes: int) -> SemanticChunk | None:
    prefix: list[str] = []
    used = 0
    for line in io.StringIO(chunk.content, newline=""):
        last_size = len(line.encode("utf-8"))
        if used + last_size > max_bytes:
            break
        prefix.append(line)
        used += last_size
    if not prefix:
        return None
    return replace(
        chunk, content="".join(prefix), end_line=chunk.start_line + len(prefix) - 1,
        end_column=len(prefix[-1].encode("utf-8")) + (chunk.start_column if len(prefix) == 1 else 0),
    )


class EvidenceRegistry:
    """Append-only, redacted evidence for one verified investigation snapshot."""

    def __init__(self, issue: str, limits: ContextLimits, source_bytes: int | None) -> None:
        self.issue = redact(validate_issue(issue))
        self.limits = limits
        self.sources: list[Evidence] = []
        self.payload = _serialize(self.issue, self.sources)
        if len(self.payload) > limits.max_chars:
            raise ValueError("The bug report exceeds the context budget. Shorten it or increase --context-chars.")
        self._selected: list[SemanticChunk] = []
        self._by_id: dict[str, Evidence] = {}
        self._remaining = source_bytes * 3 // 4 if source_bytes is not None else None

    def register(
        self, chunk: SemanticChunk, *, allow_prefix: bool = True, merge_overlaps: bool = False,
    ) -> Evidence | None:
        existing = self._by_id.get(chunk.chunk_id)
        if existing is not None:
            return existing
        metadata = (chunk.relative_path, chunk.symbol_name or "", chunk.symbol_type or "")
        # A redacted path or symbol would no longer identify the source faithfully.
        if any(redact(part) != part for value in metadata for part in value.split("/")):
            return None
        if self._remaining is not None and len(chunk.content.encode("utf-8")) > self._remaining:
            if self.sources or not allow_prefix:
                return None
            chunk = _line_prefix(chunk, self._remaining)
            if chunk is None:
                return None
        for previous, evidence in zip(self._selected, self.sources, strict=True):
            if (
                previous.relative_path == chunk.relative_path
                and (previous.start_line, previous.start_column, previous.end_line, previous.end_column)
                == (chunk.start_line, chunk.start_column, chunk.end_line, chunk.end_column)
            ):
                return evidence
        snippet = redact(chunk.content)
        if not snippet.replace(MASK, "").strip() or _mostly_covered(
            chunk, self._selected, merge_overlaps=merge_overlaps,
        ):
            return None
        source = Evidence(
            f"S{len(self.sources) + 1}", chunk.chunk_id, chunk.relative_path, chunk.symbol_name, chunk.symbol_type,
            chunk.start_line, chunk.end_line, snippet,
        )
        candidate = _serialize(self.issue, [*self.sources, source])
        if len(candidate) > self.limits.max_chars:
            return None
        self.sources.append(source)
        self._selected.append(chunk)
        self._by_id[chunk.chunk_id] = source
        if self._remaining is not None:
            self._remaining -= len(chunk.content.encode("utf-8"))
        self.payload = candidate
        return source

    def seed(self, hits: tuple[RetrievalHit, ...]) -> InvestigationContext:
        for hit in hits:
            self.register(hit.chunk)
            if len(self.sources) == self.limits.max_sources:
                break
        return self.context()

    def context(self) -> InvestigationContext:
        return InvestigationContext(self.issue, tuple(self.sources), self.payload)


def select_context(
    issue: str, hits: tuple[RetrievalHit, ...], limits: ContextLimits, *, source_bytes: int | None = None,
) -> InvestigationContext:
    return EvidenceRegistry(issue, limits, source_bytes).seed(hits)
