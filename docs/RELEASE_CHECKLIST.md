# Release checklist

## Source and secrets

- [x] Review the source export and verify ignore rules in a disposable Git repository. The root checkout has no Git metadata. The sample fixture, dependency lockfile, environment template, and curated screenshots are eligible for release.
- [x] Keep environment overrides, package-manager credentials, private repositories, QA output, model caches, local indexes, and debug logs out of the source package. Existing local data remains on disk.
- [x] Scan release-candidate text for common provider credentials, private keys, app bearer and pairing literals, and credential-bearing URLs. The two private-key header matches were intentional scanner and redaction test fixtures. The curated images were visually checked for credentials and machine paths. The indexing screenshot masks the absolute path.
- [ ] Audit the intended repository's tracked paths and all available Git refs and history before making it public. This workspace has no history to inspect. If a real credential appears, revoke or rotate it first, then coordinate history cleanup and check the remotes and forks that might retain it.
- [ ] Confirm ownership and choose a license before publishing. The current source export has no license file.

## Functional and security checks

- [x] Run backend tests, compilation, and dependency consistency, and frontend typecheck, lint, tests, and production build. The Windows native symlink test requires a host privilege. The junction scope test passed.
- [x] Reinstall into a clean source export with a fresh Python virtual environment and npm tree. Download the pinned MiniLM weights into an empty QA cache, then index the receipt fixture and run hybrid search offline. The npm production dependency audit reported zero advisories at verification time.
- [x] Verify the browser-to-bridge cookie handshake, the server-only bearer, the scope and origin checks, explicit approval, snapshot freshness, and bounded polling.
- [x] Verify source is submitted through the provider only after approval. Exercise structured output and citation validation with an intercepted Gemini transport and confirm the agent exposes read-only source lookups.
- [x] Review errors and test output for credentials. The launcher filters provider keys from Next.js, rejects private values in public frontend settings, and redacts known secrets from child-service output. The browser smoke checks the observed responses and request headers for synthetic credentials.
- [x] Use the isolated offline demo and browser smoke to verify local startup and shutdown. The fixed-port launcher rejected occupied ports and preserved the existing listeners. A disposable copy using two temporary port constants verified Windows PowerShell startup, pairing, backend authorization, preserved origins, and Ctrl+C cleanup.
- [ ] Run the optional live browser procedure with an authorized account before claiming live browser verification. [Manual Gemini verification](END_TO_END_TESTING.md#manual-gemini-verification)

## Before a GitHub release

1. Resolve the history audit and licensing items in the intended Git repository. Inspect the complete staged file list and screenshots before publishing.
2. Repeat the [Windows setup](../README.md#windows-setup) from a fresh checkout and run the tests and [offline browser smoke](END_TO_END_TESTING.md#offline-browser-verification).
3. Draft release notes with the Windows requirements, loopback-only single-user scope, and separate evidence for the scripted benchmark, the one-pair live CLI sample, and the browser intercept. [Benchmark evidence](INVESTIGATION_BENCHMARK.md#recorded-results-and-scope)
4. Tag the reviewed commit, confirm the GitHub source archive contains only intended release files, and publish only after owner approval.
