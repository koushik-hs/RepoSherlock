# RepoSherlock demo

Use the bundled `buggy_receipts` fixture. A line with quantity three contributes its unit price once. The investigation points to `receipt_total` and suggests a multi-unit regression case. For a predictable presentation use the offline demo below and say that the provider response is scripted.

## Start the interactive offline demo

Complete the [Windows setup](../README.md#windows-setup) first. These commands populate an empty local model cache, build the frontend, and launch the demo. The initial model download may need internet. Subsequent runs use the same cache and skip the warmup and build if the source and build are current.

~~~powershell
$python = '.\backend\.venv\Scripts\python.exe'
$env:HF_HOME = Join-Path (Get-Location).Path '.reposherlock\hf-cache'
& $python -m reposherlock.cli semantic-index --path .\backend\tests\fixtures\buggy_receipts
npm.cmd --prefix .\frontend run build
& $python -u .\scripts\verify_e2e.py --demo
~~~

The demo prints an isolated loopback workspace URL and the absolute path of a fresh copy of the receipt fixture. Open it in a private browser window if you already have a regular RepoSherlock session. Press **T** in the demo console and paste the temporary code into **Local pairing code**. The demo accepts only that run's fixture root. It uses the real Next.js bridge, FastAPI, MiniLM, ChromaDB, BM25, approval flow, citation checks, and tool trace. A local HTTP interceptor supplies scripted Gemini responses and synthetic token usage. No key or Playwright installation is needed. Press **Ctrl+C** in the demo console when finished.

For the normal launcher and separately authorized live Gemini workflow, follow [Windows startup and manual provider verification](END_TO_END_TESTING.md). Use the same issue and review the actual excerpts and model before approving.

## Present in two to three minutes

Before speaking, open the workspace and let the first index finish if the schedule is tight. Use a fresh demo launch to show registration and cold indexing. On a prepared fixture, use **Build index** again to show the incremental reuse path.

| Time | Show and say |
| --- | --- |
| 0:00–0:25 | Pair, register the printed absolute path, and select **Build index**. “The workspace runs locally and only indexes the checkout I choose.” |
| 0:25–0:55 | In Code search, select **Hybrid** and enter `receipt_total`. Open the function excerpt. “BM25 finds this identifier. Semantic retrieval also supports a description like ‘receipt total ignores quantity.’” |
| 0:55–1:40 | Enter `Receipt totals are too low when one line contains multiple units.` Select **Agentic**, then **Preview source disclosure**. Review the issue, excerpts, and additional disclosure notice. Check the approval box and select **Approve and run investigation**. “This explicit approval binds the investigation to the reviewed snapshot.” |
| 1:40–2:30 | Expand the receipt citation and show the sum of unit prices. Discuss multiplying by quantity and testing a multi-unit line. Open Agent Activity and its evidence references. “The read-only lookup found a symbol already in the preview, so the trace shows no new evidence. The model response in this demo is scripted.” |

To compare strategies, select **Single-pass** and create a new preview before approving it. To repeat an automated run instead of presenting interactively, use the [browser smoke commands](END_TO_END_TESTING.md#offline-browser-verification).

[Demo screenshots](../README.md#what-it-does)
