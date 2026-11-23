# Local HTTP API

## Start the server

Use the backend virtual environment. Semantic indexing and retrieval need the `semantic` and `embeddings` extras and the pinned MiniLM files in the local cache. Install the `investigation` extra for approved live Gemini calls. The API uses local-only embeddings and reports a failed job if it cannot load the cache. An ordinary CLI semantic index can populate a fresh cache separately.

~~~powershell
Set-Location 'D:\Reposherlock'
$python = '.\backend\.venv\Scripts\python.exe'
$env:REPOSHERLOCK_ALLOWED_ROOTS = 'D:\Reposherlock\backend\tests\fixtures'
$env:REPOSHERLOCK_FRONTEND_ORIGINS = 'http://localhost:3000'
$env:REPOSHERLOCK_SESSION_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:HF_HOME = 'D:\Reposherlock\.reposherlock\hf-cache'
$env:HF_HUB_OFFLINE = '1'
& $python -m uvicorn reposherlock.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
~~~

| Setting | Meaning |
|---|---|
| `REPOSHERLOCK_ALLOWED_ROOTS` | Absolute local directories, separated by `;` on Windows or `:` on POSIX. When unset, the source checkout's test fixture directory is used if present. An explicitly empty setting denies all paths. |
| `REPOSHERLOCK_SESSION_TOKEN` | Random 32–128 character URL-safe local session token. Protected routes are disabled when unset. |
| `REPOSHERLOCK_FRONTEND_ORIGINS` | Comma-separated exact loopback HTTP(S) origins. The default permits same-origin use. |
| `REPOSHERLOCK_SWAGGER_ASSETS` | Optional absolute directory with a trusted local Swagger UI distribution. |
| `HF_HOME` and `HF_HUB_CACHE` | Optional settings for the existing pinned MiniLM cache. |
| `GEMINI_API_KEY` | Server-side credential used only after a live approval. |
| `GEMINI_MODEL` | Optional override for the application default in `.env.example`. |

Enter a Gemini key privately in the server shell before launch if testing live approval. The examples below use a bearer configured for direct FastAPI access. The browser frontend uses the separate launcher pairing flow.

~~~powershell
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString)).Password
~~~

## OpenAPI and Swagger

Open [OpenAPI JSON](http://127.0.0.1:8000/openapi.json) for schemas and routes. [The docs page](http://127.0.0.1:8000/docs) links to the schema. Set `REPOSHERLOCK_SWAGGER_ASSETS` before starting Uvicorn to enable interactive Swagger. Point it at a trusted local directory containing `swagger-ui-bundle.js` and `swagger-ui.css`. In Swagger, choose **Authorize** and enter the local session token. The browser loads the scripts and schema from the API origin. [FastAPI self-hosted docs](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/) · [Official Swagger UI distribution](https://www.npmjs.com/package/swagger-ui-dist)

## Routes

| Method | Path | Success | Purpose |
|---|---|---|---|
| GET | `/api/health` | 200 | Health check |
| POST | `/api/repositories` | 200 | Register an absolute local path |
| GET | `/api/repositories/{repository_id}` | 200 | Inspect index availability and snapshot status |
| POST | `/api/repositories/{repository_id}/index` | 202 | Queue semantic indexing |
| POST | `/api/repositories/{repository_id}/search` | 200 | Search by query, mode, and top-K |
| GET | `/api/jobs/{job_id}` | 200 | Poll job state, progress, and index counts |
| POST | `/api/investigations/preview` | 200 | Preview the initial redacted disclosure |
| POST | `/api/investigations` | 202 | Approve a preview and queue an investigation |
| GET | `/api/investigations/{investigation_id}` | 200 | Read the investigation state and report |
| GET | `/api/investigations/{investigation_id}/trace` | 200 | Read the metadata-only trace |

Use `Authorization: Bearer <local-session-token>` for every protected endpoint. POST requests accept UTF-8 JSON up to 64 KiB. Errors have the shape below. Output from a failed background job uses the same `code` and `message` fields inside its `error` property.

~~~json
{"detail":{"code":"remote_code_permission_required","message":"Explicitly approve remote-code disclosure for this investigation."}}
~~~

## Register, index, and search

Registration returns a stable repository ID, a display name, and a path label such as `root-1/buggy_receipts`. The index status is `not_indexed`, `ready`, `stale`, `invalid`, or `busy`. Registration leaves the index unchanged. Use the relative path label for display and the ID as the identifier in subsequent calls.

Run these examples in a second PowerShell shell. Keep the local token private and enter the one set in the server shell.

~~~powershell
$base = 'http://127.0.0.1:8000'
$token = [System.Net.NetworkCredential]::new('', (Read-Host 'Local session token' -AsSecureString)).Password
$headers = @{ Authorization = "Bearer $token" }
$repo = Invoke-RestMethod "$base/api/repositories" -Method Post -Headers $headers -ContentType 'application/json' -Body (
    @{ path = 'D:\Reposherlock\backend\tests\fixtures\buggy_receipts' } | ConvertTo-Json
)
$job = Invoke-RestMethod "$base/api/repositories/$($repo.repository_id)/index" -Method Post -Headers $headers -ContentType 'application/json' -Body '{}'
do {
    Start-Sleep -Milliseconds 500
    $poll = Invoke-RestMethod "$base/api/jobs/$($job.job_id)" -Headers $headers
} while ($poll.state -in @('queued', 'running'))
if ($poll.state -eq 'failed') { throw $poll.error.message }
$search = Invoke-RestMethod "$base/api/repositories/$($repo.repository_id)/search" -Method Post -Headers $headers -ContentType 'application/json' -Body (
    @{ query = 'receipt_total'; mode = 'hybrid'; top_k = 3 } | ConvertTo-Json
)
~~~

Indexing is incremental unless the caller sets `rebuild: true`. Job progress is nullable until the indexer reports a stage. Scan progress has no known total. Vector progress counts successfully staged chunks, and completion includes `total`, `new`, `reused`, `updated`, `removed`, and `embedded`. Search supports lexical, semantic, and hybrid retrieval. Results expose rank, chunk ID, relative location, source span, redacted snippet, scores, and diagnostics. Search caps snippets at 6,000 characters per hit and labels truncation.

## Preview and approval

~~~powershell
$previewBody = @{
    repository_id = $repo.repository_id
    issue = 'Receipt totals are too low when one line contains multiple units.'
    strategy = 'single'
    configuration = @{ max_sources = 6 }
} | ConvertTo-Json -Depth 5
$preview = Invoke-RestMethod "$base/api/investigations/preview" -Method Post -Headers $headers -ContentType 'application/json' -Body $previewBody
$preview | ConvertTo-Json -Depth 7
~~~

Preview uses zero Gemini requests. It returns the redacted issue, every initial source span with its ID, the source character and byte counts, context size, model, effective configuration, and expiration. Agentic previews include a disclosure notice for bounded later tool lookups. Display the full notice and evidence before asking for consent.

Only after reviewing the preview and deciding to send code externally, submit the exact strategy and effective configuration shown. The request below starts live work when the server has a Gemini key.

~~~powershell
$approval = @{
    preview_id = $preview.preview_id
    strategy = $preview.strategy
    configuration = $preview.configuration
    allow_remote_code = $true
} | ConvertTo-Json -Depth 8
$run = Invoke-RestMethod "$base/api/investigations" -Method Post -Headers $headers -ContentType 'application/json' -Body $approval
Invoke-RestMethod "$base/api/jobs/$($run.job_id)" -Headers $headers
Invoke-RestMethod "$base/api/investigations/$($run.investigation_id)" -Headers $headers
Invoke-RestMethod "$base/api/investigations/$($run.investigation_id)/trace" -Headers $headers
~~~

Previews are single use. Approval and execution must begin within the five-minute validity window. A changed source snapshot, initial context, model, or effective configuration requires a new preview. Successful reports can contain hypotheses or a grounded insufficient-evidence response. Failed jobs carry an error and may retain safe trace metrics. Provider quota, timeout, unavailable-provider, invalid-report, and invalid-planning outcomes are distinct from a completed abstention.

## Frontend contract

1. Pair the browser via `POST /api/local/session`. An HTTP-only, SameSite Strict cookie authorizes same-origin frontend requests. Next.js handles the FastAPI bearer and forwards only supported methods and paths to the configured loopback origin. The browser receives a separate pairing code from the launcher. Gemini credentials stay in FastAPI.
2. Accept an absolute local path as text and render the returned sanitized display label. Submit an index job when needed and poll by job ID. Display progress only when supplied by the indexer.
3. Request a new preview whenever the issue, strategy, or settings change. Render all initial excerpts as text. Use `additional_disclosure_possible` and `disclosure_notice` to present the scope of agentic consent.
4. Require a fresh user action before posting approval. Handle 409 as a state conflict and 410 as an unavailable preview. For a busy worker or full queue, wait and retry within the preview validity window.
5. Poll until `completed` or `failed`. Display validated citations and result metrics when available. Open a citation to see its matching approved excerpt or validated provider quote when available. Fetch the trace separately. Its evidence references and tool metadata omit snippets and prompts. Provider token fields may be partial or unavailable.

Run on loopback as one process. The bearer token grants access to that process's local data and jobs. Registrations, previews, and jobs are held in memory. A restart clears them, while committed semantic indexes remain on disk. The worker bounds concurrency and retained records. Source scans reject linked paths and ignore generated and sensitive files according to the scanner's rules. Index files live in the selected checkout. Source redaction is heuristic, and the server assumes the operator controls the host, allowed repositories, and local index files. These controls support single-user local development.
