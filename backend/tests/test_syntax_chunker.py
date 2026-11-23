import io

import pytest

from reposherlock.ingestion.models import SemanticChunk, SourceFile
from reposherlock.ingestion.syntax_chunker import MAX_CHUNK_BYTES, chunk_source


def source(path: str, content: str) -> SourceFile:
    return SourceFile(path, "test", content, len(content.encode("utf-8")))


def original_slice(content: str, chunk: SemanticChunk) -> str:
    lines = [line.encode("utf-8") for line in io.StringIO(content, newline="")]
    index = chunk.start_line - 1
    if chunk.start_line == chunk.end_line:
        return lines[index][chunk.start_column:chunk.end_column].decode("utf-8")
    return b"".join([
        lines[index][chunk.start_column:],
        *lines[chunk.start_line:chunk.end_line - 1],
        lines[chunk.end_line - 1][:chunk.end_column],
    ]).decode("utf-8")


def test_python_nested_symbols_exact_and_stable() -> None:
    text = (
        "import os\r\n\r\n"
        "class Store:\r\n"
        "    size = 1\r\n"
        "    @staticmethod\r\n"
        "    def read():\r\n"
        "        def café():\r\n"
        "            return 'ação'\r\n"
        "        return café()\r\n"
        "\r\nprint(os.name)"
    )
    file = source("store.py", text)
    chunks = list(chunk_source(file))
    assert chunks == list(chunk_source(file))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert "".join(chunk.content for chunk in chunks) == text
    for chunk in chunks:
        assert chunk.content == original_slice(text, chunk)
    named = {chunk.symbol_name: chunk for chunk in reversed(chunks)}
    assert named["Store"].symbol_type == "class"
    method = named["Store.read"]
    assert method.symbol_type == "method" and method.start_line == 5
    assert method.content.startswith("@staticmethod\r\n")
    assert named["Store.read.café"].symbol_type == "function"
    assert named["Store.read.café"].start_line == 7
    assert named["Store.read.café"].end_line == 8
    assert any("print(os.name)" in chunk.content and not chunk.symbol_name for chunk in chunks)


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("auth.js", "export class Client { check() { return true; } }\n",
         {"Client": "class", "Client.check": "method"}),
        ("parse.jsx", "const View = () => <div />;\n", {"View": "function"}),
        ("parse.ts", "interface Doc { id: string }\nfunction parse(x: Doc) { return x.id; }\n",
         {"Doc": "interface", "parse": "function"}),
        ("parse.tsx", "export function View(): JSX.Element { return <main />; }\n",
         {"View": "function"}),
    ],
)
def test_javascript_typescript_and_tsx(path: str, text: str, expected: dict[str, str]) -> None:
    chunks = list(chunk_source(source(path, text)))
    assert {chunk.symbol_name: chunk.symbol_type for chunk in chunks if chunk.symbol_name} == expected
    assert all(chunk.strategy == "tree_sitter" for chunk in chunks)
    assert all(chunk.content == original_slice(text, chunk) for chunk in chunks)


@pytest.mark.parametrize(("path", "text"), [
    ("broken.py", "def unfinished(:\n    return 1\n"),
    ("broken.ts", "function strange( {\n"),
    ("module.go", "package main\n\nfunc main() {}\n"),
])
def test_parser_fallback(path: str, text: str) -> None:
    [chunk] = chunk_source(source(path, text))
    assert chunk.strategy == "lines"
    assert chunk.symbol_name is None and chunk.symbol_type is None
    assert chunk.content == text
    assert chunk.start_line == 1


def test_large_function_splits_without_cutting_utf8_characters() -> None:
    text = "def calculate():\r    return '" + "界" * 1100 + "'"
    chunks = list(chunk_source(source("calc.py", text)))
    assert len(chunks) > 2
    assert all(chunk.symbol_name == "calculate" for chunk in chunks)
    assert all(len(chunk.content.encode("utf-8")) <= MAX_CHUNK_BYTES for chunk in chunks)
    assert all(chunk.content == original_slice(text, chunk) for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == text
