"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  apiClient, expired, type Investigation, type Job, type Mode, type Preview,
  type Source, type Strategy, type Trace,
} from "../lib/api";
import { useWorkbench } from "../lib/workbench";
import { Icon } from "./mark";

const repositoryStates = {
  not_indexed: "Not indexed", ready: "Index ready", stale: "Index stale", invalid: "Invalid index", busy: "Index busy",
};
const stages: Record<string, string> = {
  scanning: "Scanning source", building_vectors: "Building vectors", publishing: "Publishing index", unchanged: "Index unchanged",
};
const modes = { hybrid: "Hybrid", semantic: "Semantic", lexical: "BM25" } satisfies Record<Mode, string>;
const nav = [["Workspace", "#workspace"], ["Code Search", "#search"], ["Investigations", "#investigations"], ["Docs", "#docs"]];
const highlights = ["Hybrid RAG", "Source citations", "Agentic search", "Read-only tools"];

export function RepoSherlock({ documentationOrigin }: { documentationOrigin: string | null }) {
  const [session, setSession] = useState({ connected: false, revision: 0, notice: "" });
  useEffect(() => {
    const controller = new AbortController();
    void apiClient().session(controller.signal).then(({ connected }) => {
      if (!controller.signal.aborted) {
        setSession((current) => current.revision === 0 ? { ...current, connected } : current);
      }
    }).catch(() => { });
    return () => controller.abort();
  }, []);

  function updateSession(connected: boolean, notice = "") {
    setSession(({ revision }) => ({ connected, notice, revision: revision + 1 }));
  }

  return <Workbench key={session.revision} connected={session.connected} notice={session.notice}
    updateSession={updateSession} documentationOrigin={documentationOrigin} />;
}

function Workbench({ connected, notice, updateSession, documentationOrigin }: {
  connected: boolean; notice: string; updateSession: (connected: boolean, notice?: string) => void;
  documentationOrigin: string | null;
}) {
  const work = useWorkbench((message) => updateSession(false, message));
  const [health, setHealth] = useState<"checking" | "online" | "offline">("checking");
  const [healthCheck, setHealthCheck] = useState(0);
  const [sessionInput, setSessionInput] = useState("");
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("hybrid");
  const [topK, setTopK] = useState(5);
  const [issue, setIssue] = useState("");
  const [strategy, setStrategy] = useState<Strategy>("single");
  const [maxSources, setMaxSources] = useState(6);

  useEffect(() => {
    const controller = new AbortController();
    async function check() {
      try {
        const response = await apiClient().health(controller.signal);
        if (!controller.signal.aborted) setHealth(response.status === "ok" ? "online" : "offline");
      } catch {
        if (!controller.signal.aborted) setHealth("offline");
      }
    }
    void check();
    return () => controller.abort();
  }, [healthCheck]);

  function retryHealth() {
    setHealth("checking");
    setHealthCheck((current) => current + 1);
  }

  async function changeSession() {
    if (sessionBusy) return;
    setSessionBusy(true);
    setSessionError("");
    try {
      const api = apiClient();
      const timeout = AbortSignal.timeout(15000);
      const status = await (connected ? api.disconnect(timeout) : api.pair(sessionInput.trim(), timeout));
      updateSession(status.connected);
    } catch (failure) {
      setSessionError(failure instanceof Error ? failure.message : "Unable to update the local session.");
    } finally {
      setSessionBusy(false);
    }
  }

  const current = work.repository;
  const locked = !connected || sessionBusy || work.busy !== null;
  const ready = !!current && current.snapshot_status === "ready";
  const finding = work.investigation?.report?.hypotheses[0];
  const searchItems = work.search?.hits.slice(0, 3) ?? [];
  const tools = work.trace?.tools ?? [];
  const healthLabel = { checking: "Checking", online: "API online", offline: "API offline" }[health];
  const latestStatus = work.submittingInvestigation ? "Submitting" :
    work.investigation?.report?.status === "insufficient_evidence" ? "Insufficient evidence"
      : work.investigation?.state ?? work.investigationJob?.state ?? "Awaiting report";

  return <>
    <a className="skip" href="#workspace">Skip to workspace</a>
    <header className="site-header" id="top">
      <div className="wide header-inner">
        <a href="#top" className="brand" aria-label="RepoSherlock home">
          <span className="brand-mark"><Icon name="brand" /></span><span>RepoSherlock</span>
        </a>
        <nav aria-label="Primary navigation">{nav.map(([name, href]) => <a key={href} href={href}>{name}</a>)}</nav>
        <div className="header-actions">
          <a href="#connection" className="local-link"><span className={`lamp ${health}`} />Local status</a>
          <a className="button button-header" href="#workspace">Open workspace</a>
        </div>
      </div>
    </header>
    <main>
      <div className="wide landing">
        <section className="hero" aria-labelledby="hero-heading">
          <div className="hero-copy">
            <div className="eyebrow"><span className="eyebrow-line" />Local-first code investigation</div>
            <h1 id="hero-heading"><span>Find the <em>bug</em>.</span><i>Understand the why.</i></h1>
            <p>Trace bugs across your codebase with source-grounded AI investigation.</p>
            <div className="hero-actions">
              <a className="button primary" href="#investigations">Start investigating</a>
              <a className="button secondary" href="#search">Explore code</a>
            </div>
          </div>
        </section>

        <section className="hero-stage" aria-label="Workspace overview">
          <div className="overview">
            <OverviewCard title="Repository Intelligence" href="#workspace" icon="folder"
              state={current ? repositoryStates[current.snapshot_status] : "No repository"}>
              {current ? <>
                <strong>{current.display_name}</strong>
                {searchItems.length ? <ul>{searchItems.map((hit) => <li key={hit.chunk_id}><code>{hit.file_path}</code><span>{hit.symbol_name || hit.language}</span></li>)}</ul>
                  : <p>{current.snapshot?.indexed_chunks != null ? `${current.snapshot.indexed_chunks} indexed chunks. Run a search to see source here.` : current.path}</p>}
              </> : <><strong>Connect a repository</strong><p>Browse indexed files and symbols from your workspace.</p></>}
            </OverviewCard>
            <OverviewCard title="Investigation Findings" href="#investigations" icon="findings" state={latestStatus}>
              {finding ? <><strong>{finding.suspected_cause}</strong><p>{finding.citations.map(location).join(" · ")}</p></>
                : <><strong>{work.investigation?.report ? "No supported hypothesis" : "No findings yet"}</strong>
                  <p>{work.investigation?.report?.missing_evidence[0] || "Preview a bug report to assemble source evidence."}</p></>}
            </OverviewCard>
            <OverviewCard title="Agent Activity" href="#activity" icon="activity"
              state={work.trace ? `${tools.length} tool ${tools.length === 1 ? "call" : "calls"}` : "No trace"}>
              {tools.length ? <ul>{tools.slice(0, 3).map((tool) => <li key={tool.sequence}><code>{tool.name}</code><span>{tool.status.replaceAll("_", " ")}</span></li>)}</ul>
                : <><strong>{work.trace ? "No tool calls used" : "No tool calls yet"}</strong><p>Read-only lookups and discovered evidence appear here.</p></>}
            </OverviewCard>
          </div>
          <ul className="feature-list" aria-label="RepoSherlock capabilities">
            {highlights.map((item) => <li key={item}><span aria-hidden="true" />{item}</li>)}
          </ul>
        </section>
      </div>

      <div className="workspace-wrap">
        <div className="wide workspace-body" id="workspace">
          <div className="section-title"><h2>Workspace</h2><p>Register an allowed local repository to index and investigate its source.</p></div>
          <div className="work-grid">
            <section className="work-panel" id="connection" aria-labelledby="connection-title">
              <div className="panel-heading"><h3 id="connection-title"><Icon name="lock" />Local connection</h3><span className="chip"><span className={`lamp ${health}`} />{healthLabel}</span></div>
              <p>Use the pairing code from the launcher to connect this browser.</p>
              {connected ? <div className="form-actions"><span className="connected">Session connected</span>
                <button className="button secondary compact" onClick={retryHealth}>Check API</button>
                <button className="button secondary compact" disabled={sessionBusy} onClick={() => void changeSession()}>Disconnect</button></div>
                : <form onSubmit={(event) => {
                  event.preventDefault();
                  if (sessionInput.trim()) void changeSession();
                }}>
                  <label htmlFor="session-token">Local pairing code</label>
                  <input id="session-token" type="password" value={sessionInput} autoComplete="off" spellCheck={false}
                    minLength={32} maxLength={128} pattern="[A-Za-z0-9_\-]{32,128}" required
                    onChange={(event) => setSessionInput(event.target.value)} placeholder="Paste the code from the launcher" />
                  <div className="form-actions"><button className="button primary compact" disabled={sessionBusy}>Connect session</button>
                    <button className="button secondary compact" type="button" onClick={retryHealth}>Check API</button></div>
                </form>}
              <ErrorNotice text={sessionError || notice || null} />
            </section>
            <section className="work-panel" aria-labelledby="repository-title">
              <div className="panel-heading"><h3 id="repository-title"><Icon name="folder" />Repository</h3>
                {current && <span className="chip">{repositoryStates[current.snapshot_status]}</span>}</div>
              <form onSubmit={(event) => { event.preventDefault(); void work.register(path); }}>
                <label htmlFor="repository-path">Absolute repository path</label>
                <input id="repository-path" value={path} onChange={(event) => setPath(event.target.value)}
                  placeholder="Absolute path to a permitted repository" maxLength={2048} disabled={locked || !!work.tracking} required />
                <div className="form-actions">
                  <button className="button primary compact" disabled={locked || !!work.tracking || !path.trim()}>Register repository</button>
                  <button type="button" className="button secondary compact" onClick={() => void work.index()}
                    disabled={locked || !current || !!work.tracking}>Build index</button>
                  {current && <button type="button" className="text-button" onClick={() => void work.refresh()} disabled={locked}>Refresh status</button>}
                </div>
              </form>
              {current && <p className="meta"><code>{current.path}</code>{current.snapshot?.indexed_chunks != null && ` · ${current.snapshot.indexed_chunks} chunks`}</p>}
              <JobNotice job={work.indexJob} pending={work.busy === "workspace" ? "Working…" : null} />
              {work.tracking?.kind === "index" && !work.busy && <button className="button secondary compact" onClick={() => void work.resume()}>Resume status</button>}
              <ErrorNotice text={work.error?.section === "workspace" ? work.error.message : null} />
            </section>
          </div>

          <section className="work-panel flow-panel" id="search" aria-labelledby="search-title">
            <div className="panel-heading"><h3 id="search-title"><Icon name="search" />Code search</h3></div>
            <form className="search-form" onSubmit={(event) => { event.preventDefault(); void work.find(query, mode, topK); }}>
              <div><label htmlFor="search-query">Search query</label><input id="search-query" value={query}
                maxLength={2000} onChange={(event) => setQuery(event.target.value)} placeholder="Search a symbol or describe behavior" disabled={locked || !ready} required /></div>
              <div><label htmlFor="retrieval-mode">Retrieval</label><select id="retrieval-mode" value={mode}
                onChange={(event) => setMode(event.target.value as Mode)} disabled={locked || !ready}>
                {Object.entries(modes).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div>
              <div><label htmlFor="top-k">Results</label><input id="top-k" type="number" min="1" max="20" value={topK || ""}
                onChange={(event) => setTopK(event.target.valueAsNumber || 0)} disabled={locked || !ready} required /></div>
              <button className="button primary compact" disabled={locked || !ready || !query.trim()}>{work.busy === "search" ? "Searching…" : "Search code"}</button>
            </form>
            {!ready && <p className="meta"><a className="inline-link" href="#workspace">Register and index a repository</a> to search its source.</p>}
            <ErrorNotice text={work.error?.section === "search" ? work.error.message : null} />
            {work.search && <div className="results">
              <p className="meta">{work.search.hits.length} results · {work.search.duration_ms.toFixed(1)} ms · {work.search.diagnostics.status.replaceAll("_", " ")}</p>
              {work.search.hits.map((hit, index) => <SourceBlock key={hit.chunk_id} source={hit} id={`Rank ${hit.rank}`} initiallyOpen={index === 0}
                detail={[hit.rrf_score != null && `RRF ${hit.rrf_score.toFixed(3)}`, hit.semantic_score != null && `Semantic ${hit.semantic_score.toFixed(3)}`,
                hit.bm25_score != null && `BM25 ${hit.bm25_score.toFixed(2)}`, hit.snippet_truncated && "Snippet clipped"].filter(Boolean).join(" · ")} />)}
            </div>}
          </section>

          <section className="work-panel flow-panel" id="investigations" aria-labelledby="investigation-title">
            <div className="panel-heading"><h3 id="investigation-title"><Icon name="findings" />Investigations</h3></div>
            <form onSubmit={(event: FormEvent) => { event.preventDefault(); void work.prepare(issue, strategy, maxSources); }}>
              <label htmlFor="bug-report">Bug report</label>
              <textarea id="bug-report" value={issue} maxLength={12000} rows={4} placeholder="Describe what happened and what you expected."
                onChange={(event) => { setIssue(event.target.value); work.clearPreview(); }} disabled={locked || !ready} required />
              <div className="investigation-form">
                <div><label htmlFor="strategy">Strategy</label><select id="strategy" value={strategy} disabled={locked || !ready}
                  onChange={(event) => { setStrategy(event.target.value as Strategy); work.clearPreview(); }}>
                  <option value="single">Single-pass</option><option value="agentic">Agentic</option></select></div>
                <div><label htmlFor="source-cap">Initial sources</label><input id="source-cap" type="number" min="1" max="12"
                  value={maxSources || ""} onChange={(event) => { setMaxSources(event.target.valueAsNumber || 0); work.clearPreview(); }} disabled={locked || !ready} required /></div>
                <button className="button primary compact" disabled={locked || !ready || !issue.trim()}>Preview source disclosure</button>
              </div>
            </form>
            {!ready && <p className="meta"><a className="inline-link" href="#workspace">Register and index a repository</a> to preview an investigation.</p>}
            <ErrorNotice text={work.error?.section === "investigations" ? work.error.message : null} />
            {work.preview && <Disclosure key={work.preview.preview_id} preview={work.preview} approved={work.approved}
              onApproval={work.setApproved} onRun={() => void work.approve()} disabled={locked || !!work.tracking} />}
            <JobNotice job={work.investigationJob} pending={work.busy === "investigations" ? "Working…" : null} />
            {work.tracking?.kind === "investigation" && !work.busy &&
              <button className="button secondary compact" onClick={() => void work.resume()}>Resume status</button>}
            {work.investigation && <Report result={work.investigation} sources={work.approvedSources} />}
            <Activity trace={work.trace} />
          </section>

          <section className="docs-panel" id="docs" aria-labelledby="docs-title">
            <h2 id="docs-title">Docs</h2>
            <p>Start the local services, pair this browser, and register a permitted directory. Index the repository before searching or previewing a bug report.</p>
            <p>The disclosure preview runs locally. Gemini is used after you review the excerpts and approve a specific investigation.</p>
            {documentationOrigin && ["/docs", "/openapi.json"].map((path) =>
              <a key={path} className="button secondary compact" href={documentationOrigin + path}
                target="_blank" rel="noreferrer">{path === "/docs" ? "API documentation" : "OpenAPI schema"}</a>
            )}
          </section>
        </div>
      </div>
    </main>
    <footer className="wide site-footer"><a href="#top" className="brand"><span className="brand-mark"><Icon name="brand" /></span>RepoSherlock</a><span>Local repository investigation</span></footer>
  </>;
}

function OverviewCard({ title, href, icon, state, children }: {
  title: string; href: string;
  icon: "folder" | "findings" | "activity"; state: string; children: ReactNode
}) {
  return <article className="overview-card">
    <div className="overview-title"><span className="tile-icon"><Icon name={icon} /></span><h3><a href={href}>{title}</a></h3></div>
    <div className="overview-content">{children}</div>
    <div className="overview-state"><span />{state}</div>
  </article>;
}

function ErrorNotice({ text }: { text: string | null }) {
  return text ? <p className="error" role="alert">{text}</p> : null;
}

function JobNotice({ job, pending }: { job: Job | null; pending: string | null }) {
  if (!job) return pending ? <p className="meta" role="status">{pending}</p> : null;
  const progress = job.progress;
  return <div className="job" role="status">
    <strong>{job.state}</strong>
    {progress && <span>{stages[progress.stage] || progress.stage}{progress.chunks_total != null
      ? ` · ${progress.chunks_processed ?? 0} / ${progress.chunks_total} chunks` : ""}</span>}
    {job.index_result && <span>{job.index_result.embedded} embedded · {job.index_result.reused} reused</span>}
    {job.error && <span className="failure">{job.error.message}</span>}
  </div>;
}

function location(source: Pick<Source, "file_path" | "start_line" | "end_line">) {
  return `${source.file_path}:${source.start_line}–${source.end_line}`;
}

function SourceBlock({ source, id, initiallyOpen, detail }: { source: Source; id: string; initiallyOpen?: boolean; detail?: string }) {
  return <details className="source" open={initiallyOpen}>
    <summary><span className="chip">{id}</span><code>{location(source)}</code>{source.symbol_name && <span>{source.symbol_name}</span>}</summary>
    {detail && <p className="meta">{detail}</p>}
    <pre><code>{source.snippet}</code></pre>
  </details>;
}

function Disclosure({ preview, approved, onApproval, onRun, disabled }: {
  preview: Preview; approved: boolean; onApproval: (approved: boolean) => void; onRun: () => void; disabled: boolean;
}) {
  const [ended, setEnded] = useState(false);
  useEffect(() => {
    const timeout = setTimeout(() => setEnded(true), Math.max(0, Date.parse(preview.expires_at) - Date.now()));
    return () => clearTimeout(timeout);
  }, [preview.expires_at]);
  return <div className="disclosure">
    <h4><Icon name="lock" />Review disclosure to Gemini</h4>
    <p>{preview.disclosure_notice}</p>
    <p className="meta">{preview.model} · {preview.sources.length} excerpts · {preview.source_bytes.toLocaleString()} source bytes · Expires {new Date(preview.expires_at).toLocaleTimeString()}</p>
    <blockquote>{preview.issue}</blockquote>
    {preview.sources.map((source) => <SourceBlock key={source.source_id} source={source} id={source.source_id} initiallyOpen />)}
    {ended && <p className="error" role="status">This preview expired. Generate a new one to continue.</p>}
    <label className="consent"><input type="checkbox" checked={approved} disabled={disabled || ended}
      onChange={(event) => onApproval(event.target.checked)} />
      <span>I authorize this investigation to send the redacted issue and source to Gemini.
        {preview.additional_disclosure_possible && " This includes additional redacted excerpts found during read-only agent lookups."}</span></label>
    <button className="button primary compact" type="button" disabled={!approved || disabled || ended}
      onClick={() => { if (!expired(preview)) onRun(); else setEnded(true); }}>Approve and run investigation</button>
  </div>;
}

function Report({ result, sources }: { result: Investigation; sources: Preview["sources"] }) {
  const report = result.report;
  const metrics = result.metrics;
  return <section className="report" aria-label="Investigation result">
    <div className="panel-heading"><h4>Investigation findings</h4><span className="chip">
      {report?.status === "insufficient_evidence" ? "Insufficient evidence" : result.state}</span></div>
    <p>{result.issue_summary}</p>
    <p className="meta">{result.model} · {result.strategy === "single" ? "Single-pass" : "Agentic"}
      {result.stop_reason && ` · ${result.stop_reason.replaceAll("_", " ")}`}</p>
    <ErrorNotice text={result.error?.message ?? null} />
    {report && <>
      {report.observations.map((item, index) => <div className="finding" key={index}><p>{item.statement}</p>
        <Citations items={item.citations} sources={sources} /></div>)}
      {report.hypotheses.map((item, index) => <div className="finding" key={index}>
        <h5>{item.suspected_cause}</h5><p>{item.reasoning}</p><Citations items={item.citations} sources={sources} />
        <TextList title="Suggested fixes" items={item.suggested_fixes} />
        <TextList title="Verification tests" items={item.verification_tests} />
      </div>)}
      <TextList title="Missing evidence" items={report.missing_evidence} />
      <TextList title="Uncertainties" items={report.uncertainties} />
    </>}
    {metrics && <p className="meta">{metrics.duration_ms.toFixed(1)} ms · {metrics.model_request_count} model requests · {metrics.tool_call_count} tool calls
      {metrics.valid_citations != null && ` · ${metrics.valid_citations} validated citations`}
      {metrics.usage_summary.total.tokens != null && ` · ${metrics.usage_summary.total.tokens} tokens (${metrics.usage_summary.total.status})`}</p>}
  </section>;
}

function Citations({ items, sources }: {
  items: (Omit<Source, "snippet"> & { source_id: string; quote: string | null })[]; sources: Preview["sources"];
}) {
  return <ul className="citations">{items.map((item, index) => {
    const source = sources.find((entry) => entry.source_id === item.source_id && location(entry) === location(item)
      && entry.symbol_name === item.symbol_name && entry.symbol_type === item.symbol_type);
    const excerpt = source?.snippet ?? item.quote;
    return <li key={`${item.source_id}-${index}`}><details className="source">
      <summary><span className="chip">{item.source_id}</span> <code>{location(item)}</code></summary>
      {excerpt ? <pre><code>{excerpt}</code></pre> : <p className="meta">This citation has a validated location. No excerpt is available here.</p>}
    </details></li>;
  })}</ul>;
}

function TextList({ title, items }: { title: string; items: string[] }) {
  return items.length ? <div className="text-list"><strong>{title}</strong><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div> : null;
}

function Activity({ trace }: { trace: Trace | null }) {
  return <section className="activity" id="activity" aria-labelledby="activity-title">
    <h4 id="activity-title"><Icon name="activity" />Agent activity</h4>
    {!trace ? <p className="meta">Read-only tool calls and evidence references appear after an investigation.</p> : <>
      <p className="meta">{trace.stop_reason.replaceAll("_", " ")} · {trace.tools.length} tool calls</p>
      {trace.tools.map((tool) => <div className="tool-row" key={tool.sequence}>
        <code>{tool.name}</code><span className="chip">{tool.status.replaceAll("_", " ")}</span>
        <span className="meta">Round {tool.round} · {tool.duration_ms.toFixed(1)} ms
          {tool.new_evidence_ids.length > 0 && ` · New evidence ${tool.new_evidence_ids.join(", ")}`}</span>
      </div>)}
      {trace.evidence.length > 0 && <details className="source"><summary>Trace evidence references</summary><ul>
        {trace.evidence.map((entry) => <li key={entry.source_id}><span className="chip">{entry.source_id}</span> <code>{location(entry)}</code></li>)}
      </ul></details>}
    </>}
  </section>;
}