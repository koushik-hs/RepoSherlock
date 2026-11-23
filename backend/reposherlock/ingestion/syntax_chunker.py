"""Partition source by Tree-sitter symbols, retaining the text between them."""

import hashlib
import io
import json
import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from reposherlock.core.config import ChunkSettings, SEMANTIC_CHUNK_SETTINGS
from reposherlock.ingestion.chunker import chunk_file
from reposherlock.ingestion.models import SemanticChunk, SourceFile


MAX_CHUNK_BYTES = 900
STRATEGY_VERSION = 1
GRAMMARS = {
    ".py": tree_sitter_python.language,
    ".js": tree_sitter_javascript.language,
    ".jsx": tree_sitter_javascript.language,
    ".ts": tree_sitter_typescript.language_typescript,
    ".tsx": tree_sitter_typescript.language_tsx,
}
SYMBOL_TYPES = {
    "class_definition": "class",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class": "class",
    "function_definition": "function",
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function"}


@lru_cache(maxsize=len(GRAMMARS))
def _language(extension: str) -> Language:
    return Language(GRAMMARS[extension]())


@dataclass(frozen=True)
class _Symbol:
    name: str | None
    kind: str


def _symbol(node: Node, raw: bytes, owner: _Symbol | None) -> tuple[Node, _Symbol | None]:
    definition = node
    if node.type in ("decorated_definition", "export_statement"):
        field = "definition" if node.type == "decorated_definition" else "declaration"
        definition = node.child_by_field_name(field) or node
    kind = SYMBOL_TYPES.get(definition.type)
    name = definition.child_by_field_name("name")
    if definition.type == "variable_declarator":
        value = definition.child_by_field_name("value")
        if value is not None:
            if value.type in FUNCTION_VALUES:
                kind = "function"
            elif value.type == "class":
                kind = "class"
            if kind:
                definition = value
    if kind is None:
        return node, None
    if definition.type == "function_definition" and owner and owner.kind == "class":
        kind = "method"
    local_name = raw[name.start_byte:name.end_byte].decode("utf-8") if name else None
    qualified = f"{owner.name}.{local_name}" if owner and owner.name and local_name else local_name
    return definition, _Symbol(qualified, kind)


def _ranges(root: Node, raw: bytes) -> Iterator[tuple[int, int, _Symbol | None]]:
    # Visit only named nodes. Enter and leave events delimit each symbol's
    # exclusive text, so nested declarations never duplicate their parents.
    owner: _Symbol | None = None
    cursor = 0
    pending = [(iter(root.named_children), len(raw), None)]
    while pending:
        children, end, parent = pending[-1]
        node = next(children, None)
        if node is None:
            pending.pop()
            if end is not None:
                if cursor < end:
                    yield cursor, end, owner
                cursor, owner = end, parent
            continue
        definition, symbol = _symbol(node, raw, owner)
        if symbol is None:
            pending.append((iter(node.named_children), None, owner))
            continue
        if cursor < node.start_byte:
            yield cursor, node.start_byte, owner
        cursor = node.start_byte
        pending.append((iter(definition.named_children), node.end_byte, owner))
        owner = symbol


def _bounded(raw: bytes, start: int, end: int) -> Iterator[tuple[int, int]]:
    while end - start > MAX_CHUNK_BYTES:
        cut = start + MAX_CHUNK_BYTES
        # Prefer an intact line near the byte cap, then split long lines at a
        # UTF-8 code point boundary.
        newline = raw.rfind(b"\n", start + MAX_CHUNK_BYTES // 2, cut)
        if newline >= 0:
            cut = newline + 1
        else:
            while raw[cut] & 0xC0 == 0x80 or raw[cut - 1:cut + 1] == b"\r\n":
                cut -= 1
        yield start, cut
        start = cut
    if start < end:
        yield start, end


def chunk_source(
    source: SourceFile, settings: ChunkSettings = SEMANTIC_CHUNK_SETTINGS
) -> Iterator[SemanticChunk]:
    raw = source.content.encode("utf-8")
    line_starts = [0]
    for line in io.StringIO(source.content, newline=""):
        line_starts.append(line_starts[-1] + len(line.encode("utf-8")))

    extension = Path(source.relative_path).suffix.casefold()
    tree = None
    if extension in GRAMMARS:
        try:
            # Tree-sitter counts LF rows. Replacing bare CR preserves byte offsets.
            tree = Parser(_language(extension)).parse(re.sub(rb"\r(?!\n)", b"\n", raw))
        except (ValueError, RuntimeError):
            pass
    if tree is not None and not tree.root_node.has_error:
        ranges = _ranges(tree.root_node, raw)
        strategy = "tree_sitter"
    else:
        ranges = (
            (line_starts[part.start_line - 1], line_starts[part.end_line], None)
            for part in chunk_file(source, settings)
        )
        strategy = "lines"

    seen: set[str] = set()
    for start, end, symbol in ranges:
        # Keep whitespace gaps inside their enclosing span. Ignore isolated
        # whitespace-only spans to avoid indexing meaningless records.
        if not raw[start:end].strip():
            continue
        for first, last in _bounded(raw, start, end):
            start_line = bisect_right(line_starts, first)
            end_line = bisect_right(line_starts, last - 1)
            start_column = first - line_starts[start_line - 1]
            end_column = last - line_starts[end_line - 1]
            content = raw[first:last].decode("utf-8")
            name = symbol.name if symbol else None
            kind = symbol.kind if symbol else None
            identity = [source.relative_path, start_column,
                        start_line, end_line, end_column, content, strategy, name, kind]
            chunk_id = hashlib.sha256(json.dumps(identity).encode("ascii")).hexdigest()
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            yield SemanticChunk(
                chunk_id, source.relative_path, source.language, start_line, end_line,
                content, start_column, end_column, strategy, name, kind,
            )
