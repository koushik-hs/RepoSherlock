# Command-line workflows

Install the extras from the [Windows setup](../README.md#windows-setup). Where an example uses `python`, activate the virtual environment or substitute its Python executable. [Browser demo](DEMO_GUIDE.md) · [HTTP API](HTTP_API.md)

## Start FastAPI by itself

~~~powershell
$python = '.\backend\.venv\Scripts\python.exe'
$env:REPOSHERLOCK_SESSION_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:REPOSHERLOCK_ALLOWED_ROOTS = (Resolve-Path '.\backend\tests\fixtures').Path
$env:REPOSHERLOCK_FRONTEND_ORIGINS = 'http://localhost:3000'
& $python -m uvicorn reposherlock.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
~~~

Run the commands from the repository root. [Health](http://127.0.0.1:8000/api/health) returns `{"status":"ok","service":"reposherlock"}`. The protected API supports local repository registration, queued indexing, search, and investigation previews. A live investigation needs approval for its specific preview and a server-side Gemini key. See the [API guide](HTTP_API.md) for environment settings, Swagger, examples, and the frontend contract.

## Index a local repository

~~~sh
python -m reposherlock.cli index --path "/path/to/local/repository"
python -m reposherlock.cli index --help
~~~

You can also use `--path tests/fixtures/sample_repo` from `backend`. The CLI prints counts and the two output paths. In the target directory, open `.reposherlock/indexes/<repository-id>/manifest.json`, then read the JSONL file named by its `chunks_file` field. Each JSONL row contains a chunk ID, relative file path, language, inclusive line range, and source text. A file with zero bytes counts as indexed and produces zero chunks. The manifest supplies source byte counts and skip reasons.

| Option | Default |
| --- | ---: |
| `--max-file-bytes` | 524,288 bytes (512 KiB) |
| `--max-files` | 5,000 accepted files |
| `--max-total-bytes` | 52,428,800 accepted bytes (50 MiB) |
| `--chunk-lines` | 100 lines |
| `--overlap-lines` | 15 lines |

Choose an overlap between zero and one less than the chunk size. The scan visits directory entries in sorted order. Supported UTF-8 extensions include `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.java`, `.cpp`, `.cc`, `.c`, `.h`, `.hpp`, `.go`, `.rs`, `.sql`, `.json`, `.md`, `.yaml`, and `.yml`.

## Build a semantic index and search

From an activated virtual environment in `backend`, install the CPU build of PyTorch and the semantic dependencies.

~~~sh
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[semantic,embeddings,test]'
python -m reposherlock.cli semantic-index --path ./reposherlock --batch-size 16
python -m reposherlock.cli search --path ./reposherlock --query "How are generated and binary files filtered?" --top-k 5
~~~

Tree-sitter locates functions, methods, classes, and selected declarations in Python, JavaScript, JSX, TypeScript, and TSX. Keeping symbols together gives retrieval a more meaningful unit than an arbitrary line window. Separate chunks keep imports and module statements searchable. Other formats and files with parser errors use the existing line window algorithm, with 24 lines and 3 lines of overlap by default for semantic indexing. Both strategies split long spans to a 900-byte source budget.

MiniLM maps text to a normalized 384-dimensional vector. Code inputs include the path and available symbol name, and the original code is stored separately. The first index or search that needs vectors downloads the pinned model weights into the Hugging Face cache pointed to by `HF_HOME` or the per-user default. Once cached, the model works offline. ChromaDB stores vectors locally and returns results ranked by cosine similarity, where higher scores indicate closer matches. Similarity scores measure retrieval relevance. They do not establish that a function contains a bug.

Rerun `semantic-index` after editing the source tree. Unchanged chunks reuse their embeddings, changed inputs are embedded, and deleted chunks leave the active collection. Search checks the accepted files and rejects a stale index. Use `semantic-index --rebuild` to replace an incompatible or damaged vector collection. Use `--help` to see scan limits, fallback window settings, batch size, and other options.

## Choose a retrieval mode

~~~sh
python -m reposherlock.cli search --path ./reposherlock --mode semantic --query "What happens when a repository index is stale?"
python -m reposherlock.cli search --path ./reposherlock --mode lexical --query "chunk_source"
python -m reposherlock.cli search --path ./reposherlock --mode hybrid --query "How are unchanged embeddings reused?" --top-k 5
~~~

| Mode | Ranking |
| --- | --- |
| `semantic` | MiniLM cosine similarity. This remains the CLI default. |
| `lexical` | BM25 over source, symbol names, and file paths. Lexical queries use the existing semantic JSONL and require no embedding inference. |
| `hybrid` | Reciprocal rank fusion of the two candidate lists. Output includes contributing scores and ranks. |

BM25 rewards rare, matching terms and adjusts for chunk length. The tokenizer preserves exact names such as `getUserById` and `tenant_id` alongside their parts, so keyword queries can match code identifiers. Hybrid search defaults to 20 candidates per method and an RRF constant of 60. Set `--candidate-k` and `--rrf-k` to change them. RRF uses the positions in the ranked lists because cosine similarity and BM25 scores have different scales.

A `Low lexical overlap` message means the leading candidates shared few query terms. Ranked candidates remain available for review, including relevant code described by a paraphrase. Search reports an empty result separately. Investigation also distinguishes an empty usable context from a report that abstains from proposing a cause. Retrieval rank does not establish that a chunk contains a bug.

## Run the retrieval benchmark

Build or refresh the index for `./reposherlock` from `backend`, then run the benchmark.

~~~sh
python -m reposherlock.evaluation --path ./reposherlock --dataset ../benchmarks/reposherlock.json
~~~

Pass `--output` to save per-query scores, ranks, matches, and timings as JSON. The dataset contains 22 positive queries and five unsupported queries. Positive labels use manually inspected paths and symbols, with an exact source fragment where only part of a symbol is relevant. The runner checks those labels against the current snapshot before evaluating. See the [measured results](BUILD_LOG.md#phase-3--2026-11-22) and the [dataset](../benchmarks/reposherlock.json).

## Investigate a bug on Windows

Use an indexed local repository and preview the selected excerpts in PowerShell. Complete the CPU embedding setup above if needed.

~~~powershell
Set-Location .\backend
$python = '.\.venv\Scripts\python.exe'
$repo = 'C:\path\to\your\repository'
$issue = 'Describe the observed behavior, expected behavior, and any relevant error.'
& $python -m reposherlock.cli semantic-index --path $repo
& $python -m reposherlock.cli investigate --path $repo --issue $issue --provider gemini --dry-run
~~~

Dry run uses local hybrid retrieval and displays the redacted excerpts and their source references. It requires no Gemini credential and makes no Gemini requests. Fresh local embedding setups may need to download model weights. To use a longer UTF-8 report, replace `--issue $issue` with `--issue-file 'C:\path\to\bug-report.txt'`.

For a live investigation, install the Google SDK extra and enter your key privately in the current PowerShell shell. Review the dry-run excerpts and issue before authorizing the request.

~~~powershell
& $python -m pip install -e '.[investigation]'
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString)).Password
# Optional model override
# $env:GEMINI_MODEL = 'gemini-3.8-flash'
& $python -m reposherlock.cli investigate --path $repo --issue $issue --provider gemini --strategy single --allow-remote-code
Remove-Item Env:GEMINI_API_KEY
~~~

The application reads `GEMINI_API_KEY` and `GEMINI_MODEL` from the process environment. The default model is `gemini-3.8-flash`. The [.env.example](../.env.example) file lists the settings. Live mode requires `--allow-remote-code` on each invocation and sends a redacted issue plus selected excerpts. `--context-chars` limits the JSON payload to 16,000 characters by default, and `--max-sources` defaults to six. The excerpt budget also omits at least one quarter of accepted source bytes, including on small checkouts. Redaction is heuristic. Review the preview and issue for sensitive material.

Gemini uses a single structured generation with a 30-second timeout per attempt and at most three attempts for transient failures. The report distinguishes observations from suspected causes and proposed tests. The application validates citations against the selected excerpts and checks snapshot freshness again before showing live results. It does not run the code, apply fixes, or run verification tests. [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/) · [Structured output reference](https://ai.google.dev/gemini-api/docs/structured-output)

### Gather additional evidence with the agentic strategy

`--strategy single` remains the default. `--strategy agentic` starts with the same hybrid retrieval, then lets Gemini request bounded source lookups before a separate validated report request. The planner can finish immediately by calling `finish_investigation` without arguments or by returning a completed response with no tool calls. Agentic consent allows additional redacted snippets to be sent in later requests. The preview lists the available tools and budgets and makes zero Gemini requests.

~~~powershell
# Reuse $python, $repo, and $issue from the setup above.
& $python -m reposherlock.cli investigate --path $repo --issue $issue --provider gemini --strategy agentic --dry-run

$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString)).Password
$trace = Join-Path (Join-Path $repo '.reposherlock') ('agent-' + [guid]::NewGuid().ToString('N') + '.json')
& $python -m reposherlock.cli investigate --path $repo --issue $issue --provider gemini --strategy agentic --allow-remote-code --trace-output $trace
Get-Content -LiteralPath $trace -Raw
Remove-Item Env:GEMINI_API_KEY
~~~

| Tool | Scope |
| --- | --- |
| `search_code` | Hybrid by default, with optional semantic or lexical mode and up to five results. |
| `read_code_lines` | An exact relative path and inclusive range from verified source, with at most 6,000 characters per read. If a permitted range starts inside the file and crosses EOF, the tool returns the actual range with `truncated: true`. |
| `find_symbol` | An exact qualified or short indexed symbol name, with up to five results and an ambiguity indicator. |

| Option | Agentic default |
| --- | ---: |
| `--max-rounds` | 3 planning rounds |
| `--max-tool-calls` | 8 requests |
| `--max-calls-per-round` | 4 requests |
| `--max-read-lines` | 150 lines |
| `--max-model-requests` | 5 logical requests, with one reserved for the report |
| `--context-chars` | 24,000 serialized issue and evidence characters |
| `--max-sources` | 6 initial excerpts |

Duplicate, rejected, and empty tool calls count toward the tool budget. Cached replies carry metadata and existing references without adding duplicate source text. Two consecutive rounds with no new evidence stop planning early if there is another round left. One unproductive round leaves room for a different lookup. Planning requests have no retry. The final report uses the single-pass retry policy. The evidence registry counts at most 32 excerpts and still omits at least one quarter of accepted source bytes. Small budgets can prevent a tool result from adding an excerpt.

`--trace-output` writes a new JSON file outside the source tree or under the checkout's ignored `.reposherlock` directory. It records validated parameters with recognized secrets masked, evidence references, request counts, timings, reported token usage, and the stop reason. Search query text is represented by its length. The trace omits source snippets, complete prompts, and Gemini thought metadata. Dry-run traces have empty tool and model request lists.

The agentic terminal report and the trace's `usage_summary` total the provider-reported counts across all planning and report requests. Input, output, and total counts are marked complete, partial, or unavailable separately. A partial total includes only requests that reported that count. Usage that the provider did not return remains unknown.
