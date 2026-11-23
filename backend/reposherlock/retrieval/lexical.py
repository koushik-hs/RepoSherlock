"""In-memory BM25 over code, symbol names, and repository-relative paths."""

from collections import Counter
from collections.abc import Iterable
from heapq import nsmallest
from math import log1p

from reposherlock.ingestion.models import SemanticChunk
from reposherlock.retrieval.models import ScoredChunk
from reposherlock.retrieval.tokenizer import query_terms, tokenize


class BM25Index:
    def __init__(self, chunks: Iterable[SemanticChunk]) -> None:
        self.chunks: dict[str, SemanticChunk] = {}
        self._postings: dict[str, dict[str, int]] = {}
        lengths: dict[str, int] = {}
        for chunk in chunks:
            if chunk.chunk_id in self.chunks:
                raise ValueError("Duplicate chunk ID in semantic JSONL. Run semantic-index again.")
            self.chunks[chunk.chunk_id] = chunk
            terms = Counter(tokenize(chunk.content))
            terms.update(dict.fromkeys(tokenize(chunk.relative_path), 1))
            if chunk.symbol_name:
                terms.update(dict.fromkeys(tokenize(chunk.symbol_name), 3))
            lengths[chunk.chunk_id] = terms.total()
            for term, frequency in terms.items():
                self._postings.setdefault(term, {})[chunk.chunk_id] = frequency

        total = len(self.chunks)
        average = lengths and sum(lengths.values()) / total or 1.0
        self._length_norm = {
            chunk_id: 1.2 * (0.25 + 0.75 * length / average)
            for chunk_id, length in lengths.items()
        }
        self._idf = {
            term: log1p((total - len(posting) + 0.5) / (len(posting) + 0.5))
            for term, posting in self._postings.items()
        }

    def search(self, query: str, top_k: int) -> list[ScoredChunk]:
        scores: dict[str, float] = {}
        for term in query_terms(query):
            posting = self._postings.get(term)
            if posting is None:
                continue
            weight = self._idf[term] * 2.2
            for chunk_id, frequency in posting.items():
                score = weight * frequency / (frequency + self._length_norm[chunk_id])
                scores[chunk_id] = scores.get(chunk_id, 0.0) + score
        best = nsmallest(top_k, scores.items(), key=lambda entry: (-entry[1], entry[0]))
        return [ScoredChunk(self.chunks[chunk_id], score) for chunk_id, score in best]
