# Release checklist

## Source and secrets

- [x] Review the 134 files in the initial Git commit. The sample fixtures, dependency lockfile, environment template, and seven curated screenshots are eligible for release.
- [x] Keep environment overrides, package-manager credentials, private repositories, QA output, model caches, local indexes, and debug logs out of the source package. Existing local data remains on disk.
- [x] Scan release-candidate text for common provider credentials, private keys, app bearer and pairing literals, and credential-bearing URLs. The two private-key header matches were intentional scanner and redaction test fixtures. The curated images were visually checked for credentials and machine paths. The indexing screenshot masks the absolute path.
- [x] Audit the local initial commit for credentials and private files. If additional history is imported, audit it before publishing.
- [x] Record the owner's approval of MIT for the original source and include the upstream notices. [License](../LICENSE) · [Third-party notices](../THIRD_PARTY_NOTICES.md)
- [x] Record the owner's acceptance of the four open ChromaDB server advisories for embedded, loopback-only, single-user use. [Scope and advisory references](FINAL_AUDIT.md#chromadb)

## Functional and security checks

- [x] Run backend tests, compilation, and dependency consistency, and frontend typecheck, lint, tests, and production build. The Windows native symlink test requires a host privilege. The junction scope test passed.
- [x] Reinstall into a clean source export with a fresh Python virtual environment and npm tree. Download the pinned MiniLM weights into an empty QA cache, then index the receipt fixture and run hybrid search offline. The npm production dependency audit reported zero advisories at verification time.
- [x] Verify the browser-to-bridge cookie handshake, the server-only bearer, the scope and origin checks, explicit approval, snapshot freshness, and bounded polling.
- [x] Verify source is submitted through the provider only after approval. Exercise structured output and citation validation with an intercepted Gemini transport and confirm the agent exposes read-only source lookups.
- [x] Review errors and test output for credentials. The launcher filters provider keys from Next.js, rejects private values in public frontend settings, and redacts known secrets from child-service output. The browser smoke checks the observed responses and request headers for synthetic credentials.
- [x] Use the isolated offline demo and browser smoke to verify local startup and shutdown. The fixed-port launcher rejected occupied ports and preserved the existing listeners. A disposable copy using two temporary port constants verified Windows PowerShell startup, pairing, backend authorization, preserved origins, and Ctrl+C cleanup.
- [ ] Run the optional live browser procedure with an authorized account before claiming live browser verification. [Manual Gemini verification](END_TO_END_TESTING.md#manual-gemini-verification)

## Before a GitHub release

1. Verify the authenticated account and private repository. Confirm its main branch matches the reviewed commit.
2. Review the annotated tag, draft release notes, and GitHub source archive in the private repository.
3. Obtain the owner's explicit approval before changing visibility or publishing the release.
