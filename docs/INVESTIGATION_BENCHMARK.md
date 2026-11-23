# Bug investigation benchmark

## Recorded results and scope

| Evidence | What was measured |
| --- | --- |
| Phase 5B scripted benchmark | Nine paired synthetic cases (seven labelled bugs and two unsupported cases), 18 completions. Initial Recall@6 was 92.9% for both strategies. Single-pass matched 6/7 faulty functions with 8/8 valid citations. Agentic matched 7/7 with 10/10 valid citations. Each abstained on 2/2 unsupported cases. One agent lookup added the missing `timing.py:2–3` dispatch evidence. There were 9 versus 19 logical calls and 4,339 versus 8,098 simulated tokens. |
| Historical Gemini CLI sample | One paired `dispatch-window` fixture on Gemini 3.5 Flash-Lite. Identical initial evidence covered one of two labelled locations. Single-pass abstained in 4.27 s, with one attempt and 1,087 reported tokens. Agentic added the missing labelled location, localized the faulty function in 13.15 s, and used three logical calls, four reserved physical attempts, and 4,066 reported tokens. Citation checks passed for 1/1 and 3/3 references respectively. |
| Phase 7A browser integration | Real Next.js and FastAPI, local CPU retrieval and an intercepted Gemini SDK transport on the synthetic receipt checkout. It exercised both investigation modes in Chrome. Provider replies and usage were scripted. [Browser measurements](END_TO_END_TESTING.md#performance-measurements) |
| Live Gemini in the browser | Pending a separately authorized manual run. [Manual verification procedure](END_TO_END_TESTING.md#manual-gemini-verification) |

The scripted run checks retrieval, report validation, abstention, and agent behavior against known examples. Its token totals are simulated. The live CLI pair captures one historical model and case. Neither establishes general diagnostic accuracy, expected latency, billing, or a newer model's behavior. The underlying run artifacts are local and excluded from release source.

## Dataset

The [curated dataset](../backend/reposherlock/benchmarking/cases.json) contains nine reports across four small Python fixtures. The receipt and shipping fixtures come from the existing investigation tests. The catalog and dispatch fixtures add independent defects and several related dispatch functions that compete for retrieval. The labels describe behavior established by reading source. The benchmark treats the fixtures as data.

| Case | Coverage | Labelled defect or evidence gap |
|---|---|---|
| `receipt-quantity` | Single-file arithmetic | `receipt_total` sums one unit price per line. |
| `shipping-threshold` | Cross-file boundary | Checkout delegates to a tariff that excludes equality. |
| `loading-capacity` | Boundary | The slot predicate excludes an exact fit. |
| `catalog-archived` | Filter | The default view selects archived products. |
| `catalog-price-cache` | Cache invalidation | The price writer leaves a warm cache entry in place. |
| `catalog-page-count` | Misleading symbol | A function that promises count-based paging uses the count as a stop index. |
| `dispatch-window` | Wrong initial locus and cross-file reasoning | The dashboard delegates to a converter with an incorrect multiplier. |
| `receipt-missing-input` | Insufficient evidence | No transaction data connects the symptom to a cause. |
| `shipping-sms` | Unimplemented functionality | The fixture contains no cart notification pipeline. |

Positive labels specify an exact function, inclusive line range, expected cause, corrective behavior and regression. A cross-file case includes the associated caller or other necessary evidence. Unsupported cases include an explanation of the gap. The preflight verifies labelled ranges and symbols against indexed source. The investigation receives the issue and the applicable redacted evidence. The scorer and reviewer use the labels locally.

Custom datasets use the same versioned JSON schema. Pass `--dataset` and `--fixture-root`. Each case names a direct child directory of the fixture root, with labeled paths relative to that directory. The default fixture root is available in a project checkout. Supply it explicitly if running an installed wheel elsewhere.

## Offline execution

From the repository root in PowerShell, use the virtual environment with the `semantic`, `embeddings`, and `test` extras installed. The benchmark requires the pinned MiniLM weights to be present locally. An ordinary semantic index can populate the cache on a connected setup before benchmarking. The benchmark itself fails on a cache miss.

~~~powershell
$python = '.\backend\.venv\Scripts\python.exe'
$env:HF_HUB_OFFLINE = '1'
$run = Join-Path '.reposherlock' ('phase5b-mock-' + [guid]::NewGuid().ToString('N'))
& $python -m reposherlock.cli benchmark --output-dir $run
& $python -m pytest -q backend/tests
~~~

Choose a fresh output directory for each experiment. Each run writes:

- `results.json` with the configuration, labels, fixture digests, ranked references, validated reports, trace metrics, and checkpoint state.
- `summary.md` with paired results, cross-file evidence changes, and relative costs.
- `review.md` with issue text, ground truth, model explanations, suggested fixes and verification tests, cited files and functions, and reviewer fields.
- `review-template.json` for importing structured human labels.
- `snapshots/` with local fixture copies and reusable index state. This directory contains source and needs the same protection as the original fixtures.

The test suite uses deterministic embeddings, local indexes and a stub Gemini transport for live-path tests. It exercises request caps, resume, unavailable and partial usage, invalid citations, and stale-snapshot rejection.

## Metrics and pairing

Both strategies start with hybrid retrieval of up to 18 hits and select up to six initial excerpts under the same 16,000-character cap and source byte limit. `--max-sources` changes Recall@K and the initial selection limit together. The agent can add excerpts within the same final context cap. Its planner uses a separate instruction, bounded tool results and replay, and one or more additional calls. Its final generation uses the same report schema and citation checks. The runner alternates execution order across cases to distribute warm-cache effects.

| Metric | Interpretation |
|---|---|
| File and faulty function | A validated hypothesis cites the expected file. Faulty-function credit also requires the exact qualified symbol and full labelled line range. |
| Retrieval Recall@K | Fraction of labelled locations fully covered by the top K ranked source spans. |
| Initial and final context recall | Fraction of labelled locations covered by selected evidence before and after tools. A cross-file gain requires a newly covered labelled location. |
| Citation validity | Valid citations divided by inspected citations. The count includes detectable bad references in parseable rejected reports. Malformed reports with uninspectable citations have unavailable counts. |
| Unsupported abstention | An unsupported case ends in a completed insufficient-evidence report. Provider or validation failures stay unscored and resumable. |
| Diagnosis, fix, regression and unsupported claims | Independent human judgments, blank until reviewed. |
| Efficiency | Investigation and retrieval timing, logical requests, physical attempts reserved, tool calls, new excerpts, approximate source characters, and usage metadata. |

The summary groups each completed pair into both, single only, agentic only, or neither for faulty-function localization. Unsupported cases have their own abstention comparison. Unpaired or unevaluable rows remain visible. Tool calls with no new labelled coverage can flag overhead for review. A call can still provide context outside the curated labels. Timings exclude index setup, which is recorded separately. Tool search time includes its evidence registration overhead.

Scripted replies and simulated tokens exercise known paths. They cannot estimate general bug-solving ability, live latency, or billing. The dataset is small and synthetic. The dispatch case is deliberately rich in related retrieval candidates. Citation validation proves source identity and optional quote fidelity. Human review determines whether the explanation follows from the cited code. Retrieval accuracy measures whether relevant source was available for that reasoning.

## Human review

Read the issue, hypotheses, citations, and source in the saved snapshot. Score diagnosis, fix, and regression independently. Judge the explanation against the cause, the fix against the intended behavior, and the test against its ability to distinguish the faulty and corrected behavior. Count each distinct source-specific assertion that the cited evidence fails to support.

| Label | Rubric |
|---|---|
| Correct | The answer supports the expected cause or behavior with the relevant details and source. |
| Partially correct | The answer identifies a material part, with a missing link, corrective condition, or test assertion. |
| Incorrect | The answer contradicts the source, identifies the wrong cause, proposes an ineffective fix, or tests unrelated behavior. |
| Insufficient evidence | The response makes no supported determination on this dimension. Use notes to distinguish appropriate abstention on an unsupported case from a missed opportunity on a positive case. |

Copy `review-template.json` to a new file. Fill in labels, `unsupported_claims`, and optional notes. Leave unreviewed fields null. The import verifies that the review belongs to the exact completed result rows. It writes a separate summary and makes no provider calls.

~~~powershell
Copy-Item (Join-Path $run 'review-template.json') (Join-Path $run 'completed-review.json')
# Edit completed-review.json, then import it.
& $python -m reposherlock.cli benchmark --output-dir $run --review (Join-Path $run 'completed-review.json')
~~~

## Manually authorized Gemini sample

Use an installed `investigation` extra, the existing local MiniLM cache, and an authorized Gemini key. The following runs one paired fixture. You can choose another case with `--case` and use `--strategy single` or `--strategy agentic` to run one strategy. The model comes from `GEMINI_MODEL` when set, otherwise the existing application default applies.

~~~powershell
Set-Location 'D:\Reposherlock'
$python = '.\backend\.venv\Scripts\python.exe'
$env:HF_HOME = 'D:\Reposherlock\.reposherlock\hf-cache'
$env:HF_HUB_OFFLINE = '1'
$run = Join-Path '.reposherlock' ('phase5b-gemini-' + [guid]::NewGuid().ToString('N'))
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Gemini API key' -AsSecureString)).Password
try {
    & $python -m reposherlock.cli benchmark --provider gemini --strategy both --case dispatch-window --max-cases 1 --request-budget 9 --allow-remote-code --output-dir $run
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
~~~

Gemini requires `--allow-remote-code` each time and defaults to one selected case. An uninterrupted case allows at most three physical attempts in single-pass and six in agentic, including up to three planner calls and three final-generation attempts. A pair allows up to nine. The lifetime budget is printed before evaluation and counts all reserved physical attempts across resumes. The adapter bounds retries for transient HTTP errors such as 429 and 503 and for timeouts. A crash between reservation and dispatch can leave a conservative reservation. Usage returned by the provider can be partial or missing, including for retries.

To continue an incomplete run, use the same options and output directory, add `--resume`, authorize remote code again, and keep or raise the request budget. Completed rows are retained. Uncompleted attempts remain in the history. Configuration and the original and staged source digests must match. If capacity runs out between strategies, the summary shows the pending half of the pair.

Gemini receives the redacted issue and selected fixture excerpts. Agentic calls can include additional redacted excerpts and tool replay. Share only authorized fixture content. Raw results omit credentials, source snippets, complete prompts, raw provider errors, and citation quotes. They include redacted reports and reference coordinates. The source copies and indexes remain on the machine. Redaction is heuristic, so review custom fixtures and issue text before authorizing remote code.
