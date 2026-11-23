# Local frontend

The Next.js workspace runs on loopback. Start both services with `.\scripts\start-dev.ps1` from the project root and open the workspace URL printed by the launcher. Press **T** in the launcher console, paste the temporary pairing code into **Local pairing code**, and select **Connect session**.

The browser sends workspace requests to its own origin under `/api/local`. The Next.js server uses `REPOSHERLOCK_SESSION_TOKEN` for FastAPI and `REPOSHERLOCK_BROWSER_PAIRING_CODE` for the local pairing exchange. The bridge and documentation links use the validated runtime `REPOSHERLOCK_API_ORIGIN`, which defaults to `http://127.0.0.1:8000`. The launcher sets these values and scopes the Gemini key to FastAPI.

Register a permitted absolute path, wait for indexing, then search in Hybrid, Semantic, or BM25 mode. To investigate, review the redacted preview, check the consent box, and select **Approve and run investigation**. Agentic previews describe the potential disclosure from read-only lookups. Open result citations to see approved excerpts or validated quotes when available. Agent Activity shows trace references and tool metadata.

The browser session can be reused on refresh. Disconnect clears the cookie and workspace view. Restarting both services requires a fresh code. Registrations, previews, and jobs live in FastAPI memory. An expired job or server restart can require registering the directory again, while its committed index remains on disk.

The centered serif hero, snow-white illumination, charcoal panels, and lime action accents are the latest approved design. The responsive layout stacks panels on narrow screens and respects reduced-motion preferences. [Browser validation](END_TO_END_TESTING.md)
