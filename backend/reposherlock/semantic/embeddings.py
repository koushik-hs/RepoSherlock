"""Lazy, CPU-only sentence embeddings shared by indexing and querying."""

import math
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from reposherlock.ingestion.models import SemanticChunk


MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "ee8519ce6a28a61c9190da0a16e199d262197a1b"
DIMENSION = 384
TOKEN_LIMIT = 256
DEFAULT_BATCH_SIZE = 16
MODEL_FILES = (
    "1_Pooling/config.json", "config.json", "config_sentence_transformers.json",
    "model.safetensors", "modules.json", "sentence_bert_config.json",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt",
)


class Embeddings(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@lru_cache(maxsize=1)
def _load_model(*, local_only: bool = False):
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError("Install RepoSherlock's [embeddings] extra to load MiniLM.") from error

    try:
        cached = None
        allowed_files = list(MODEL_FILES)
        try:
            cached = Path(snapshot_download(
                MODEL, revision=REVISION, allow_patterns=allowed_files, local_files_only=True, token=False,
            ))
        except LocalEntryNotFoundError:
            pass
        if cached is None or not all((cached / file).is_file() for file in MODEL_FILES):
            if local_only:
                raise ValueError("The pinned MiniLM revision is missing from the local cache.")
            cached = Path(snapshot_download(
                MODEL, revision=REVISION, allow_patterns=allowed_files, token=False,
            ))
        model = SentenceTransformer(
            str(cached), device="cpu", local_files_only=True, trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
        )
        dimension = getattr(model, "get_embedding_dimension", None)
        if dimension is None:
            dimension = model.get_sentence_embedding_dimension
        if dimension() != DIMENSION:
            raise ValueError(f"Expected {DIMENSION} embedding dimensions")
        if model.max_seq_length < TOKEN_LIMIT:
            raise ValueError(f"Expected support for {TOKEN_LIMIT} tokens")
        model.max_seq_length = TOKEN_LIMIT
        return model
    except Exception as error:
        raise RuntimeError(
            f"Cannot load {MODEL} on CPU. Check the first-run model download or local cache: {error}"
        ) from error


class MiniLMEmbeddings:
    identity = f"{MODEL}@{REVISION}/cosine-256-v1"
    dimension = DIMENSION

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE, *, local_only: bool = False) -> None:
        validate_batch_size(batch_size)
        self.batch_size = batch_size
        self.local_only = local_only

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # MiniLM's tokenizer truncates at TOKEN_LIMIT, including special tokens.
        model = _load_model(local_only=True) if self.local_only else _load_model()
        return model.encode(
            list(texts), batch_size=self.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        ).tolist()


def validate_batch_size(batch_size: int) -> None:
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("Embedding batch size must be between 1 and 128.")


def code_embedding_text(chunk: SemanticChunk) -> str:
    symbol = f"\nSymbol: {chunk.symbol_name[-140:]}" if chunk.symbol_name else ""
    return f"File: {chunk.relative_path[-140:]}{symbol}\n{chunk.content}"


def checked_embeddings(backend: Embeddings, texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors = backend.embed(texts)
    if len(vectors) != len(texts) or any(
        len(vector) != backend.dimension
        or not all(math.isfinite(value) for value in vector)
        or not any(vector)
        for vector in vectors
    ):
        raise RuntimeError("Embedding backend returned invalid vectors or an incompatible dimension.")
    return vectors
