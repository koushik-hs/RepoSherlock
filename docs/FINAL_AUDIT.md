# Final audit remediation

This report records the remediation of the independent release audit in the existing Windows workspace. The earlier findings were available in the audit response. This report is the first copy saved in the project.

## Findings and fixes

| Status | Original issue and root cause | Fix and regression coverage |
| --- | --- | --- |
| PASS | HTTP checked the approved snapshot before individual attempts. CLI single-pass and agentic relied on checks around the logical call, and the live benchmark hook reserved attempts without checking the snapshot. | Single-pass and agentic now bind the original source and chunk digests to the Gemini adapter's per-attempt hook. HTTP still resolves the registered directory and checks its identity and allowlist. The guard runs before an existing budget callback, and a stale snapshot surfaces as `snapshot_changed`. Parameterized HTTP, CLI, and benchmark tests change a synthetic file after a 503, confirm the next attempt stays outside the transport and benchmark ledger, and check successful retry and usage on an unchanged snapshot. The benchmark test also publishes a new index for the edited fixture to verify that the earlier approval never transfers to it. |
| PASS | Documentation URLs were compiled with a public environment variable and defaulted to port 8000 in the production demo. | The server-rendered page validates the runtime origin with the same loopback validator as the bridge. Only the validated origin becomes a browser prop. The production browser test checks both link targets and both HTTP endpoints on temporary ports. |
| PASS | A previous completed job remained visible until the replacement approval response arrived. | The workspace hides the previous status, report, and trace during submission and exposes the submitting state. A failed submission can still leave the earlier result available. The browser regression holds the second POST and inspects the pending UI before letting the real local job continue. |
| PASS | The frontend guide contained a malformed command, and the registration placeholder contained a developer-specific absolute path. | The guide uses the PowerShell launcher command. The field requests a permitted absolute path, and the production browser test registers a real synthetic Windows path. |
| PASS | The upgraded Hub treated a deliberately filtered model cache as an incomplete full repository. That blocked the offline benchmark even when every required model file was cached. | Both cache lookup and download use the same pinned file allowlist. A regression test validates offline lookup with only those files present. |

## Dependency advisories

The Windows test environment uses Python 3.12.14 and CPU PyTorch 2.15.0. The existing Sentence Transformers 5.7.0 declares Transformers `>=4.41,<6`. Transformers 5.24.0 supports Python 3.11 and later and requires Hub `>=1.31,<3` and Tokenizers `>=0.23.1,<0.24`. ChromaDB accepts Tokenizers `>=0.13.2`. The chosen upgrade was tried in the disposable virtual environment first. [Transformers release](https://pypi.org/project/transformers/5.24.0/) · [Sentence Transformers release](https://pypi.org/project/sentence-transformers/5.7.0/)

| Status | Package | Before | After |
| --- | --- | --- | --- |
| PASS | Transformers | 4.57.6 | 5.24.0 |
| PASS | pip | 25.0.1 | 26.3 |
| WARNING | ChromaDB | 1.5.9 | 1.5.9 |
| PASS | Sentence Transformers | 5.7.0 | 5.7.0 |
| PASS | PyTorch CPU | 2.15.0+cpu | 2.15.0+cpu |
| PASS | Hugging Face Hub | 0.36.2 | 2.4.0 |
| PASS | Tokenizers | 0.22.2 | 0.23.3 |

The final Hub dependency graph contains hf-xet 1.8.0, httpx2 2.16.2, httpcore2 2.16.2, and truststore 0.10.5. The Gemini SDK remains at 2.34.0.

### Transformers

The old installed release appeared in the advisory records below. RepoSherlock uses an allowlisted, pinned MiniLM checkpoint and local inference with remote code disabled. It does not call the affected training, checkpoint conversion, model persistence, LightGlue, or custom generation paths. [Model loader](../backend/reposherlock/semantic/embeddings.py)

| Status | Advisory | Advisory range | First patched release or range boundary |
| --- | --- | --- | --- |
| PASS | [CVE-2025-14929](https://github.com/pypa/advisory-database/blob/main/vulns/transformers/PYSEC-2025-217.yaml), X-CLIP checkpoint deserialization | PyPA record through 5.0.0rc0 | No fix listed in that record. 5.24.0 is outside the range. |
| PASS | [CVE-2026-1839](https://github.com/advisories/GHSA-69w3-r845-3855), Trainer code execution | <5.0.0rc3 | 5.0.0rc3 |
| PASS | [CVE-2026-4372](https://github.com/advisories/GHSA-29pf-2h5f-8g72), model-loading code execution | <5.3.0 | 5.3.0 |
| PASS | [CVE-2026-5241](https://github.com/advisories/GHSA-fgcw-684q-jj6r), LightGlue initialization | <5.5.0 | 5.5.0 |
| PASS | [CVE-2026-80047](https://github.com/advisories/GHSA-x9r9-c232-4q39), custom generation download before consent | 4.49.0–5.8.1 | No fix listed in that record. 5.24.0 is outside the range. |
| PASS | [CVE-2026-9856](https://github.com/advisories/GHSA-xrqw-3rrv-vx5w), tokenizer or processor save path traversal | <5.10.0 | 5.10.0 |

### pip

pip runs during setup and dependency changes. The runtime services do not install packages. The development environment uses pip 26.3 after the upgrade. [pip release](https://pypi.org/project/pip/26.3/)

| Status | Advisory | Affected range | First patched release |
| --- | --- | --- | --- |
| PASS | [CVE-2025-8869](https://github.com/advisories/GHSA-4xh5-x5gv-qwph), archive extraction | ≤25.2 | 25.3 |
| PASS | [CVE-2026-1703](https://github.com/advisories/GHSA-6vgw-5pg2-w6jp), path traversal | <26.0 | 26.0 |
| PASS | [CVE-2026-3219](https://github.com/advisories/GHSA-58qw-9mgm-455v), archive interpretation | ≤26.0.1 | 26.1 |
| PASS | [CVE-2026-6357](https://github.com/advisories/GHSA-jp4c-xjxw-mgf9), untrusted functionality | <26.1 | 26.1 |
| PASS | [CVE-2026-8643](https://github.com/advisories/GHSA-wf93-45jw-7689), script entry point paths | <26.1.2 | 26.1.2 |
| PASS | [CVE-2026-13346](https://github.com/advisories/GHSA-qwm4-qh6w-59xr), package URL handling | <26.2.0 | 26.2.0 |

### ChromaDB

The four findings remain open in the installed package. The publisher's current release is 1.5.9 and the advisory records list no patched version. RepoSherlock calls Chroma's embedded Rust-backed persistence API, provides its own vectors, and starts only FastAPI and Next.js on loopback. Its normal entry points expose no Chroma HTTP server, tenant API, or Chroma RBAC runtime. Keep Chroma embedded and review these records before any change to that deployment model. [Chroma release](https://pypi.org/project/chromadb/) · [Embedded storage](../backend/reposherlock/semantic/storage.py)

| Status | Advisory | Affected range |
| --- | --- | --- |
| WARNING | [CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c), pre-authentication server code injection | 1.0.0–1.5.9 |
| WARNING | [CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr), cross-tenant collection access | 0.4.17–1.5.9 |
| WARNING | [CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr), RBAC scope validation | 0.5.0–1.5.9 |
| WARNING | [CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf), authenticated server code injection | 0.4.17–1.5.9 |

**Owner decision on 2026-11-23.** Koushik HS accepted the residual risk for the source release under the embedded, loopback-only, single-user configuration. The four advisories remain open. Reassess before enabling the Chroma HTTP server or changing deployment scope.

## Release reverification

| Status | Check | Observed result |
| --- | --- | --- |
| PASS | Backend | Final project run had **227 passed, 0 failed, 1 skipped**. Python compilation and pip consistency passed. The suite exercised incremental indexing, all retrieval modes, and mocked SDK requests. |
| NOT TESTED | Native Windows symlink creation | Windows denied the privilege required by `test_scanner.py`. The separate Windows junction check passed. |
| PASS | CPU and persistence | The disposable upgrade loaded pinned MiniLM on CPU and produced 384 normalized values. It reopened a pre-upgrade Chroma index, created a new four-chunk index, and found `receipt_total` at rank one in all three modes from separate processes. An unchanged reindex reused all four vectors. The final workspace also downloaded the ten allowed files into an empty cache and reopened it in a separate, strictly offline process. The test host uses the Hub's file-copy fallback because Windows symlink creation is unavailable. |
| PASS | CLI and benchmark | Both strategies previewed receipt evidence locally without credentials. The scripted benchmark completed **18/18** investigations with zero Gemini attempts. The initial Recall@6 stayed at 92.9% for both strategies. These are fixture results. |
| PASS | Advisory scans | PyPI was queried for 120 installed third-party releases. The CPU PyTorch wheel was checked against its corresponding public release version. ChromaDB was the sole affected package returned, with four distinct CVEs. The npm lockfile audit returned zero advisories. |
| PASS | Frontend | TypeScript, ESLint, production build, and **10 unit tests** passed. The browser smoke checked four desktop, tablet, and mobile viewports and exercised the full production workspace at 1440×900 and 390×844. |
| PASS | Browser workflow | The fixture indexed, all three searches returned evidence, both strategy previews required approval, and both investigations returned citations and trace data. On the held repeat submission, the workspace displayed the pending state and suppressed the prior result. Both documentation links reached the FastAPI instance on its assigned port. The intercepted Gemini SDK handled four requests per run and both local processes shut down. |
| PASS | Windows launch and offline demo | The normal fixed-port command reported the existing occupied port and preserved both listeners. A disposable copy using spare ports passed the same PowerShell startup, pairing, server-only bearer and provider key checks, origin preservation, and Ctrl+C cleanup. The actual `--demo` command served both runtime docs links on nondefault ports and shut down cleanly. |
| PASS | Security regressions | Tests verified server-side credentials, the signed HTTP-only cookie, origin rejection for writes, route allowlisting, repository roots and junctions, fresh single-use approval, explicit consent, read-only agent tools, metadata-only traces, and sanitized provider errors. |

## Release gates and residual risk

| Status | Item | Disposition |
| --- | --- | --- |
| PASS | Candidate release contents | A read-only check using a disposable Git directory found 134 first-commit candidates. The ignore rules excluded environment overrides, package-manager credentials, model caches, Chroma indexes, snapshots, logs, virtual environments, and QA output. The seven release screenshots and synthetic source fixtures remain eligible. The credential scan found two intentional test markers and zero matching credential values. |
| PASS | Git history | The release gate audited the local initial commit after Git was initialized. Audit any imported history separately before publishing. |
| PASS | Project licensing | The owner approved MIT for RepoSherlock's original source, with copyright 2026 Koushik HS. [License](../LICENSE) · [Third-party notices](../THIRD_PARTY_NOTICES.md) |
| WARNING | Local trust and privacy | Redaction is heuristic and indexes can contain source. The guard rechecks before dispatch, and concurrent writers can still change files after any point-in-time check. Keep the checkout and index under the operator's control. The launcher clears the clipboard copy after 60 seconds. The server pairing code remains valid for the launcher session, and tokens rotate on restart. |
| NOT TESTED | Live Gemini browser | All Gemini traffic in verification was intercepted. A live browser claim needs a separately authorized synthetic receipt run. |
| NOT TESTED | Multi-user or externally exposed deployment | The verified configuration is single-user and loopback-only. |

**Recommendation.** The offline workflow is suitable for a labeled local demo. Verify the private GitHub repository, final source archive, and draft release before asking the owner to approve public publication. Keep Chroma's HTTP server outside the release configuration.

## Files changed

- Investigation and transport binding — `backend/reposherlock/investigation/service.py`, `backend/reposherlock/investigation/agent.py`, `backend/reposherlock/api/investigations.py`, and `backend/reposherlock/benchmarking/runner.py`.
- Model cache and dependencies — `backend/reposherlock/semantic/embeddings.py`, `backend/pyproject.toml`, and `README.md`.
- Backend regressions — `backend/tests/test_investigation.py`, `backend/tests/test_investigation_benchmark.py`, `backend/tests/test_local_api.py`, and `backend/tests/test_semantic.py`.
- Frontend and validation — `frontend/app/page.tsx`, `frontend/app/reposherlock.tsx`, `frontend/lib/api.ts`, `frontend/lib/local-bridge.ts`, `frontend/lib/workbench.ts`, `frontend/tests/api.test.mjs`, and `scripts/verify-browser.cjs`.
- Launcher and documentation — `scripts/dev_supervisor.py`, `docs/FRONTEND.md`, and `docs/FINAL_AUDIT.md`.
