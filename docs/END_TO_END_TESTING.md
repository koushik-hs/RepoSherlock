# End-to-end validation

## Windows startup

Use the Python virtual environment and Node.js dependencies from the [README](../README.md). The frontend tests use Node.js 24. Indexing needs the pinned MiniLM cache. The launcher reuses the workspace cache when no alternate Hugging Face cache is configured.

~~~powershell
Set-Location 'D:\Reposherlock'
$env:REPOSHERLOCK_ALLOWED_ROOTS = (Resolve-Path '.\backend\tests\fixtures').Path
.\scripts\start-dev.ps1
~~~

Use [127.0.0.1:3000](http://127.0.0.1:3000/), press **T** in the launcher console, and paste the code into **Local pairing code**. Press **Ctrl+C** in that console when finished. The launcher passes a private bearer to Next.js and FastAPI and passes the Gemini key only to FastAPI. Browser sessions use a signed HTTP-only cookie. Keep provider credentials in the server shell and source scope as narrow as the current investigation.

## Offline browser verification

The smoke starts its own services on temporary loopback ports with a new copy of the receipt fixture, a production build, and a Google SDK HTTP interceptor. It uses synthetic credentials and the host's existing Chrome installation. To use another Chrome binary, set `REPOSHERLOCK_QA_CHROME`.

~~~powershell
Set-Location 'D:\Reposherlock'
npm.cmd --prefix .\frontend ci
npm.cmd --prefix .\.reposherlock\frontend-browser install playwright-core
npm.cmd --prefix .\frontend run build
& .\backend\.venv\Scripts\python.exe -u .\scripts\verify_e2e.py
~~~

The smoke covers pairing, registration, indexing, semantic and hybrid retrieval, BM25, receipt diagnosis in both strategies, fresh consent, job polling, citation expansion, and agent activity. It verifies that the browser sends no bearer header and checks the observed HTML, JavaScript, and API responses for the test credentials. Each run saves Chrome screenshots and measured timings under `.reposherlock/phase7a-qa`. The curated release images omit the absolute repository path and use the bundled synthetic source. [Interactive offline demo](DEMO_GUIDE.md)

## Manual Gemini verification

Live Gemini behavior remains unverified until an operator completes this opt-in procedure. Use only the synthetic receipt fixture. Enter the real key privately in the shell that launches FastAPI. Optionally set `GEMINI_MODEL` to the model enabled for your Gemini account.

~~~powershell
Set-Location 'D:\Reposherlock'
$env:REPOSHERLOCK_ALLOWED_ROOTS = (Resolve-Path '.\backend\tests\fixtures').Path
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString)).Password
try {
    .\scripts\start-dev.ps1
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
~~~

1. Pair the browser and register `D:\Reposherlock\backend\tests\fixtures\buggy_receipts`. Build or refresh its index.
2. Enter `Receipt totals are too low when one line contains multiple units. Single-unit purchases look fine.`
3. Select **Single-pass**, generate the preview, and confirm that every excerpt is from the synthetic fixture. Review the configured model. Check the disclosure box only if you approve sending this source, then start the job.
4. Verify a completed job, a displayed hypothesis that identifies the omitted line quantity, and a correction that multiplies unit price by quantity. Open the `receipts.py` citation and confirm the cited lines support the explanation. Confirm that the UI shows validated citation counts and token usage when reported by Gemini.
5. Inspect Agent Activity and record the stop reason, model requests, and evidence references. If you also want to validate Agentic, select it and generate and approve a fresh preview. Record the actual result and whether tools were used.

Track account/model or quota errors as live verification failures. An intercepted SDK response confirms wiring and parsing. It leaves live model access, diagnostic accuracy, and latency open.

## Recovery and limits

| Symptom | Recovery |
|---|---|
| Workspace or API offline | Check the launcher output, loopback ports, and required packages. |
| Wrong or expired pairing | Copy the current launcher code and pair again. |
| Repository path rejected | Use an absolute directory in an allowed root. Recheck filesystem permissions and linked ancestors. |
| Index missing, stale, or failed | Check the pinned model cache and repository permissions, then rebuild or refresh the index. An empty repository can have a valid index with zero results. |
| Preview expired or source changed | Refresh the index when stale, generate a new preview, and review and approve it again. |
| Gemini rejected authentication | Check the server-side key and whether the configured account can use the model. Restart with corrected settings. |
| Quota, temporary provider errors, or timeouts | Inspect the failed job and retry only after reviewing a new preview. |
| Job disappeared after restart | Register the repository again. Start a new job using the current index and a fresh preview for disclosure. |

RepoSherlock is a local, single-user application. The operator controls the host, repository roots, and generated indexes. Redaction is heuristic, and approved agentic lookups can disclose additional redacted evidence from the same snapshot. Process-local jobs can be lost on exit. The browser's polling cancellation stops local polling. The backend owns already accepted jobs.

## Browser and security checks

Chrome verified 1440×900 and 1920×1080 desktop layouts, a 768×1024 tablet layout, and a 390×844 mobile layout. The test checked horizontal overflow, centered typography, navigation and both CTAs, visible keyboard focus, the skip link, reduced motion, and readable source. The consent button remained disabled until the disclosure checkbox was checked.

The smoke tested wrong pairing, an invalid path, session recovery, and navigation with a pending poll. Browser fixtures simulated unavailable health and a missing job after restart. The backend suite separately tested a fresh lifespan while an earlier job was running, empty and stale indexes, failed and repeated indexing, stale and expired approvals, filesystem restrictions, and provider authentication, quota, 503, timeout, malformed-response, and invalid-citation failures. The Windows junction test passed. Native symlink creation was skipped because the host lacked the privilege.

The bridge rejected external destinations and cross-origin writes in frontend tests. The browser smoke checked HTTP-only cookie attributes, the absence of browser bearer headers, and the observed HTML, JavaScript, and API responses for all fixture credentials. FastAPI validates registration scope, current snapshots, approval flags, and citation metadata. Read-only tools accept data for search and source lookup. A valid insufficient-evidence report is a completed outcome, while provider and validation failures carry failed job status.

Two Phase 7B runs verified the same browser flow. The four-chunk receipt fixture took 17.9 and 61.6 seconds to index. Across six warm hybrid searches, retrieval took 31.9–36.0 ms. The interceptor handled four requests per run across both strategies. These workstation and fixture measurements show how cold indexing can vary on the same setup.

The same smoke passed separately from a clean source export using a new Python environment, npm tree, Playwright driver, and MiniLM cache. The backend suite there passed 215 tests and skipped the Windows native symlink test. Starlette emitted a test-client deprecation warning.

## Performance measurements

Measurements use a fresh local process, the Windows receipt fixture, a cached CPU MiniLM checkpoint, and the production Next.js build. Cold indexing includes the embedding model's first load. Query measurements start after indexing. Warm values summarize repeated queries in the same run. Investigation durations include the intercepted SDK calls and local validation. Token counts come from the fixture response.

| Measurement | Observed |
|---|---|
| Cold process readiness | FastAPI 3.44 s and Next.js bridge 3.33 s |
| Receipt indexing | 4 chunks, 17.82 s worker time and 18.92 s from the browser |
| Semantic retrieval | 52.47 ms first query, 45.80 ms warm median (3 samples) |
| BM25 retrieval | 7.32 ms (1 sample) |
| Hybrid retrieval | 38.61 ms warm median (3 samples) |
| Single-pass investigation with the intercepted SDK | 388.58 ms total, 8.00 ms orchestration overhead, 38 fixture tokens |
| Agentic investigation with the intercepted SDK | 496.71 ms total, 13.66 ms orchestration overhead, 1 tool call, 114 fixture tokens |
| Headless Chrome page and interaction | DOMContentLoaded 27–81 ms across four viewports, keyboard navigation 25.4 ms |
| Backend process memory | 522.0 MiB peak working set |
| Production frontend build | 46.12 s wall clock |

The earlier browser workflow took 64.23 s to index the same fixture in a fresh process. Keep cold-index measurements separate from warm retrieval, and compare several runs before using these times as release targets.

## Release checks to complete

Run the manual live verification with an authorized Gemini account. Re-run the native symlink test on a Windows host with symlink creation enabled. Repeat the performance sample on representative hardware and keep the measurements with the release notes.
