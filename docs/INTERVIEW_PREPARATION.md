# Interview preparation

RepoSherlock helps developers move from a symptom to source they can inspect. A whole repository is expensive to send, hard to fit in a context window, and likely to include irrelevant or sensitive material. RAG limits the working set and preserves the file and line coordinates that make a hypothesis checkable.

| Topic | What to explain |
| --- | --- |
| Hybrid retrieval | BM25 rewards exact identifiers, rare terms, and paths. MiniLM embeddings find relevant code described in different words. RRF combines the order of candidates via `1 / (k + rank)` without trying to compare raw cosine and BM25 scores. A rank is a relevance signal. |
| Code-aware chunking | Tree-sitter keeps function, class, and method spans together, records symbol metadata, and retains exact source coordinates. Large spans split under a source budget. Windowed fallback keeps unsupported or unparsable files searchable. |
| Incremental indexing | Hash the embedding input. Reuse compatible vectors for matching inputs even when coordinates shift. Stage a collection, validate its count, then publish the pointer. Compare the current source and chunk digests before retrieval. |
| Single-pass and agentic | Single-pass submits the initial evidence once, with bounded transient retries. Agentic starts with the same evidence, can invoke read-only source tools, and submits a final report with the expanded evidence. Rounds, calls, context, retries, and source ranges have bounds because each adds time, cost, and possible disclosure. |
| Citations | Validate every relative path, evidence ID, range, symbol, and optional quote against the sent excerpts and current snapshot. The checks establish what was cited. A reviewer still verifies the causal reasoning and tests the fix. |
| Privacy and scope | Local preview uses redaction and a short-lived, single-use approval tied to source, context, issue, model, and strategy. Agentic approval covers bounded later excerpts from that snapshot. Browser pairing and the backend bearer are separate, and the Gemini key stays on FastAPI. |
| Insufficient evidence | Weak or empty context can produce an abstention with missing facts and uncertainty. Report validation failures and provider errors surface as failed jobs. |

## Discuss the tradeoffs

Freshness checks incur local filesystem reads. BM25 consumes memory and is rebuilt for a new snapshot. A small CPU-friendly embedding model keeps the local workflow accessible, while broader languages and larger repos still need measurement. An agent can uncover cross-file evidence at the cost of extra requests and disclosure. The scripted benchmark tests the mechanics on nine synthetic cases. The separate live CLI evidence covers one pair. Neither evaluates general model accuracy.

## Résumé bullet candidates

- Built a local-first code investigation workspace with Next.js and FastAPI, server-side session authentication, and snapshot-bound approval for source disclosure.
- Implemented Tree-sitter chunking and incremental MiniLM/ChromaDB indexing, combining semantic and BM25 ranks with reciprocal rank fusion for code search.
- Implemented and evaluated single-pass and bounded agentic RAG with source-validated citations, read-only tools, and repeatable synthetic integration tests.
