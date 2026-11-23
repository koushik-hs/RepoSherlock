"""Split source text into deterministic overlapping line windows."""

import hashlib
import io
import json
from collections.abc import Iterator

from reposherlock.core.config import ChunkSettings
from reposherlock.ingestion.models import CodeChunk, SourceFile


def chunk_file(
    source: SourceFile, settings: ChunkSettings = ChunkSettings()
) -> Iterator[CodeChunk]:
    # Universal line boundaries with original line endings preserved.
    lines = list(io.StringIO(source.content, newline=""))
    stride = settings.chunk_lines - settings.overlap_lines
    for start in range(0, len(lines), stride):
        end = min(start + settings.chunk_lines, len(lines))
        content = "".join(lines[start:end])
        identity = json.dumps(
            [source.relative_path, start + 1, end, content], separators=(",", ":")
        )
        yield CodeChunk(
            chunk_id=hashlib.sha256(identity.encode("ascii")).hexdigest(),
            relative_path=source.relative_path,
            language=source.language,
            start_line=start + 1,
            end_line=end,
            content=content,
        )
        if end == len(lines):
            break
