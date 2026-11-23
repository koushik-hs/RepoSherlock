# Build log

## Phase 7B · 2026-11-23

- Prepared the Windows setup, architecture diagram, release checklist, interview notes, and a deterministic interactive demo. Captured seven product screenshots from the running browser workflow with a synthetic receipt checkout. [Setup and screenshots](../README.md) · [Demo](DEMO_GUIDE.md)
- Verified a clean Windows source export with fresh Python and npm installs, an initially empty MiniLM cache, and offline hybrid search. Verified PowerShell pairing and shutdown on disposable loopback ports while the normal ports were occupied.
- Reviewed release-candidate files for credentials, retaining only intentional test fixtures. The checkout had no Git history to audit. The release checklist records the history and license steps that remain. [Release checklist](RELEASE_CHECKLIST.md)
- Recorded the Phase 5B scripted benchmark, historical live CLI sample, and intercepted browser evidence separately. Live browser provider access remains unverified. [Benchmark scope](INVESTIGATION_BENCHMARK.md#recorded-results-and-scope)

## Phase 7A · 2026-11-23

- Moved browser API traffic behind a restricted Next.js Node bridge. The launcher hands the operator a separate pairing code. FastAPI uses a server-side bearer, and the Gemini key stays with FastAPI.
- Added a safe provider authentication status, session and stale-index recovery in the workspace, and expandable citations matched to approved excerpts or validated quotes.
- Verified the offline receipt workflow with the installed Google SDK and an intercepted HTTP transport. Both strategies completed, citations opened from vetted evidence, and Agentic rendered a read-only lookup in its trace. Live account and model verification remains open.
- Backend baseline had 210 passing tests. The Phase 7A suite passed 215 tests with one Windows symlink privilege skip. Ten frontend tests, lint, type checking, production build, Python compilation, and dependency checks passed.
- Audited the latest monochrome design at 1440×900, 1920×1080, 768×1024, and 390×844. Captured each viewport and checked navigation, overflow, keyboard focus, reduced motion, readable source, and consent. [Validation and measurements](END_TO_END_TESTING.md)
- Production-build smoke completed on separate loopback ports. It recorded four intercepted SDK requests and 522.0 MiB peak backend working set. Cold process readiness finished in 3.44 seconds, the receipt index took 17.82 seconds, and warm semantic and hybrid retrieval medians were 45.80 ms and 38.61 ms. An earlier run took 64.23 seconds to index. These are local synthetic measurements.

## Phase 6A · 2026-11-22

- Added local FastAPI routes for repository registration and status, incremental indexing jobs, search, investigation previews and approvals, reports, and traces. Kept the existing health response and CLI entry points.
- Added a loopback host and origin policy, a local bearer token, strict JSON request limits, and path validation that checks every ancestor for links and junctions. Added single-use approvals bound to the verified snapshot and exact initial redacted context. The Gemini transport rechecks the snapshot before every physical attempt.
- The existing indexer now emits scan, vector chunk, and publish progress. The API runs one worker, rejects duplicate active index jobs for a repository, and retains a bounded process-local history. Provider and validation failures appear as failed jobs with safe errors and available metadata.
- The baseline passed 193 tests. The final suite passed 210 tests with one Windows symlink privilege skip. Dependency and Python compilation checks passed. Offline tests covered the API security boundary, exact preview disclosure, stale snapshots, queue limits, dynamic agentic disclosure, Gemini quota and timeout handling, and metadata-only trace output.
- Started the real Uvicorn entry point on loopback and verified health, all ten OpenAPI routes, and both local Swagger assets. Registered a copy of the receipt fixture, indexed four chunks with cached CPU MiniLM, retrieved `receipt_total` at rank one, and previewed three sources. Denied approval produced no model traffic. Explicit approval returned a job that could be polled while its stub model request was running. The job completed with one validated citation. The smoke test intercepted one request at the Gemini SDK transport and made zero real Gemini requests.
- Published the [API guide and frontend contract](HTTP_API.md). Phase 6B remains unstarted.

## Phase 5B · 2026-11-22

- Added a nine-case investigation suite across four isolated Python fixtures. The baseline passed 163 tests before changes. The final suite passed 193 tests with the same Windows symlink skip. Dependency and compilation checks passed. Transport tests used the Google SDK with a local stub and made zero Gemini requests.
- Ran all 18 scripted investigations with real cached CPU MiniLM and Chroma on Windows. Both strategies received identical initial selections on all nine pairs. Initial Recall@6 was 92.9% for each. Both cited the expected function and range on six positive cases. The agent alone did so on `dispatch-window`. Both abstained on both unsupported reports. All 18 emitted citations passed the reference checks.
- Both shipping locations were already available to each strategy. On the dispatch case, `dashboard.py:4–6` ranked first and the conversion helper was outside the initial context. One `find_symbol` lookup added `timing.py:1–3`, covering the labelled lines 2–3. Dispatch context recall increased from 50% to 100%. This was the only tool call in the scripted run.
- Single-pass used nine logical requests and 4,339 simulated tokens. Agentic used 19 logical requests and 8,098 simulated tokens. Median investigation time was 33.37 ms and 36.03 ms respectively. These measurements reflect local retrieval, orchestration and scripted replies.
- The benchmark invocation took 14.43 seconds with two CPU math threads and peaked at 520.0 MiB of working set. The first fixture setup took 11.04 seconds including cached model initialization. The other three setups took 0.83 seconds combined. Median warm initial retrieval was 27.02 ms single-pass and 25.73 ms agentic. The resulting directory used 1.70 MiB.
- Repeated the full offline experiment in a fresh directory. All 18 reports, ranked references, evidence selections, and non-timing metrics matched. Source digests and checkout-independent chunk contents matched across all four fixtures.
- Added a resumable physical request ledger, review worksheet and import, and a [benchmark guide](INVESTIGATION_BENCHMARK.md). Model diagnosis accuracy, live costs and live latency remain to be measured with human-reviewed Gemini samples.

## Phase 5A stabilization · 2026-11-22

- Inspected the saved live Gemini 3.5 Flash-Lite receipt trace. Two searches returned existing evidence and a line read failed because it extended beyond EOF. The searches did not hit the exact-argument cache, and the loop had no check for rounds that returned zero new evidence. It reached the round cap. Reported request totals were 754, 828, 865, and 1,298 tokens, summing to 3,745. The terminal previously displayed the final request alone.
- Added a standalone planner finish signal, retained completion without a tool call, and added a two-round no-progress stop while keeping all hard budgets. The tool cache now returns existing evidence references and read metadata. Tool responses distinguish new IDs from familiar IDs. Bounded reads can return a truncated range at EOF without changing citation coordinates.
- Added trace and terminal usage summaries with per-field reporting coverage. The low lexical-overlap message describes term coverage and leaves useful candidate excerpts visible. Empty context and report abstention have separate messages. Sentence Transformers 5.7.0 uses its current dimension getter, with a fallback for older releases.
- Offline verification used real CPU MiniLM retrieval with mocked responses. The receipt completed with zero tools and two logical requests. A scripted replay of unproductive lookups stopped after two tool rounds and three logical requests. Its 1–25 read reported actual lines 1–12 and no new evidence. The shipping investigation added `S2` for `rates.py:4–8` and `S3` for `rates.py:1`, then finished and cited both alongside `S1`. The single-pass receipt generated a validated mock report in one request. An actual CLI dry run made zero model requests.
- The baseline passed 150 tests. The final suite passed 163 tests with one skipped Windows symlink test. Dependency and compilation checks passed. The real local retrieval demonstrations ran with FutureWarnings promoted to errors. Live Gemini behavior after the stabilization remains unverified.

## Phase 5A · 2026-11-22

- Preserved single-pass investigation as the default and added an agentic strategy with three bounded, read-only repository tools. The evidence registry extends existing source IDs and rejects excerpts outside the verified snapshot.
- Verified manual function calls through an intercepted Google Gen AI SDK 2.34.0 transport. The continuation preserved function call IDs and the bytes of the thought signature. Final structured generation uses a separate request and the existing citation validator. No live Gemini requests were made.
- Corrected fixture copying to exclude generated index caches after an existing receipt fixture index triggered Windows path errors. The original baseline returned to 102 passing tests. The final suite passed 150 tests with one Windows symlink skip. A loopback-only Python socket guard observed zero external DNS or connection attempts. Dependency and compilation checks passed.
- Used real local MiniLM with a scripted offline provider for both fixture investigations. The receipt case ranked `receipt_total` at `receipts.py:10–12` first and needed zero tools. The cross-file shipping case started at `checkout.py:4–6`, found `standard_fee` at `rates.py:4–8`, and then read the threshold constant at `rates.py:1`. Inspection shows that `>` excludes equality at 5,000 cents. The mock cited all three sources. Fixture code and proposed tests were never executed.
- Confirmed that a real agentic CLI dry run lists budgets and tools and produces an empty model and tool trace. Traces record metadata without storing source snippets or complete prompts.

### Offline demonstration measurements

Windows, Python 3.12, cached CPU MiniLM with four math threads, and mocked provider responses. Investigation timing excludes fixture indexing and model load. Logical source characters include conversation replay.

| Fixture | Hybrid searches | Tool calls | Logical model requests | Selected source chars | Logical source chars sent | Total | Orchestration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Receipt | 1 | 0 | 2 | 293 | 586 | 49.772 ms | 7.580 ms |
| Shipping | 1 | 2 | 4 | 397 | 1,330 | 46.686 ms | 14.423 ms |

The shipping demonstration process peaked at 475.1 MiB of working set. Its two tool calls took 3.280 ms and 3.028 ms. Timings reflect local retrieval, orchestration, and scripted replies.

## Phase 4 · 2026-11-22

- Added a single-pass investigation service and a Google Gen AI SDK adapter. Live calls require explicit opt-in. Dry run selects and displays the same redacted excerpts locally. The adapter defaults to `gemini-3.8-flash` and reads credentials from the process environment.
- Added a bounded evidence registry, exact citation and quote validation, stale-snapshot checks around the provider call, and explicit insufficient-evidence output. The report records verification as unperformed and retains hybrid retrieval warnings.
- Verified the SDK wire format with `google-genai` 2.34.0 and an intercepted HTTP transport. The tests covered the credential header, structured schema, timeout, bounded retries, sanitized errors, and no request without consent. No live Gemini request was made.
- Indexed the seeded receipt fixture with cached CPU MiniLM. The real CLI dry run ranked `receipt_total` at `receipts.py:10–12` as S1 and selected three excerpts using 807 JSON characters. It sent no provider request and selected 293 of 432 accepted source bytes.
- Ran an offline mock report against the real fixture retrieval. The mock cited the sum that omits quantity, suggested multiplying unit price by quantity, and proposed a three-unit regression test. The fixture code and proposed test were not executed.
- Verified 102 passing tests and one skipped Windows symlink test. Pip reported no broken requirements, compilation passed, and all 25 unique Phase 3 benchmark labels still matched current source chunks.

## Phase 3 · 2026-11-22

- Added BM25, code-aware tokenization, reciprocal rank fusion, per-method scores and ranks, and a conservative evidence diagnostic. The default CLI mode is still semantic. Lexical mode ran without loading Chroma or Torch.
- Added a [27-query benchmark](../benchmarks/reposherlock.json) against the backend package. It includes 22 labelled positive queries and five unsupported queries. The runner checked all positive labels against the indexed source before scoring.
- Verified 76 passing tests and one skipped Windows symlink test. The suite covered snapshot refresh, additions and deletions, corrupt metadata, rebuilds, and Phase 1 and 2 behavior. Pip reported no broken requirements.
- Ran all three modes for the five demonstration queries and checked exact source slices for all 172 indexed chunks. The scanning query ranked index statistics first in BM25 and hybrid, with the actual excluded-directory check second. The oversized-function, embedding reuse, and exact-symbol queries found labelled source at rank one in all modes. The authorization query produced no BM25 hits. Semantic and hybrid showed weak candidates with an evidence warning.

### Measured retrieval results

Windows, Python 3.12.14, cached CPU MiniLM, four CPU math threads, 172 chunks, and 20 candidates per method for hybrid. Warm medians include source and JSONL verification. The first semantic query loads the model. The first lexical query builds BM25 postings. The first hybrid query reuses both.

| Mode | Recall@1 | Recall@5 | MRR@5 | Warm median | First query | Negative flags | Positive flags |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Semantic | 0.545 | 0.750 | 0.655 | 34.51 ms | 12,236.63 ms | 5/5 | 9/22 |
| BM25 | 0.545 | 0.886 | 0.734 | 12.36 ms | 30.97 ms | 5/5 | 6/22 |
| Hybrid | 0.545 | 0.909 | 0.754 | 33.03 ms | 32.83 ms | 5/5 | 7/22 |

Hybrid improved aggregate Recall@5 and MRR@5. It moved the actual cosine-score conversion from BM25 rank 1 to rank 4 after other candidates received support from both lists. The evidence diagnostic flagged the oversized-function query in semantic and hybrid even though the correct symbol ranked first. Its positive flags quantify how often it can be cautious on supported questions.

Hybrid repeated a symbol in the first five hits for 12 of 27 queries. Keeping only the first span per symbol would have hidden the only labelled hit for three queries, so presentation deduplicates identical chunk IDs and retains distinct spans.

The benchmark process peaked at 476.6 MiB of working set. The first refresh from Phase 2 took 107.578 seconds and generated 65 embeddings while other verification was running. The unchanged refresh took 2.773 seconds and generated zero embeddings. BM25 builds on demand and adds no persisted indexing step.

## Phase 2 · 2026-11-22

- Added Tree-sitter chunking for Python, JavaScript, JSX, TypeScript, and TSX. Nested symbols get distinct spans. Source between symbols is retained, and invalid syntax or unsupported grammars use smaller line-based windows.
- Preserved Phase 1's manifest and chunk IDs. Semantic indexing adds a versioned manifest, a source fingerprint, and a repository-local Chroma database. Verified Tree-sitter 0.26.1 and ChromaDB 1.5.9 on Windows.
- Added lazy CPU MiniLM embeddings, source-level stale detection, incremental vector reuse, and an atomic collection pointer. Verified the pinned checkpoint with Sentence Transformers 5.7.0, Transformers 4.57.6, and PyTorch 2.15.0+cpu.
- Ran 57 passing offline tests. One test skipped because Windows denied symlink creation. The Windows junction test passed. Pip reported no broken requirements.
- Indexed the **119 chunks** in the RepoSherlock backend package with the model already cached, a batch size of 16, and four CPU math threads. The initial CLI run took **21.380 seconds**. An unchanged run took **2.973 seconds** and generated zero embeddings. These times include CLI startup. The index used **0.10 MiB** for JSONL and **1.40 MiB** for Chroma. The model cache used **87.37 MiB**, and the Python environment's site-packages used **1.26 GiB**. RAM was not measured.
- Ran three real searches from separate CLI processes and verified all 119 stored snippets, byte ranges, and paths against the source. The source-filtering and chunking queries retrieved corresponding code. The authorization query ranked weak matches because this backend has no authorization layer.
- Edited one function in a separate sample checkout and generated one new embedding. Deleting a source file removed one active record and generated zero embeddings. Search rejected the stale snapshot before refresh.
- Deferred BM25, hybrid ranking, investigation, and frontend work.

## Phase 1 · 2026-11-22

- Scaffolded a Python 3.11+ backend with FastAPI, Uvicorn, Pydantic, and a Pytest test extra.
- Built a local scanner with sorted traversal, configurable budgets, skip reason records, and conservative source filtering.
- Chose overlapping line windows and content-derived chunk IDs to establish a reproducible ingestion baseline.
- Added a CLI and content-addressed JSONL output. The manifest is published last so readers always have a completed artifact to open.
- Added a synthetic fixture and regression tests for the health route, filtering, chunk metadata, repeat indexing, and recovery after a failed manifest write.
- Verified dependency installation and ran the suite on Python 3.12.14 on Windows. It passed 39 tests, including junction exclusion and long paths. The host lacked the privilege to create a symbolic link, so one test skipped.
- Indexed the sample twice and verified three chunk rows, one skipped file, and identical output on both runs.
- Kept semantic retrieval, GitHub access, and investigation logic for later phases.
