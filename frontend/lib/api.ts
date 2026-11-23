export type Mode = "hybrid" | "semantic" | "lexical";
export type Strategy = "single" | "agentic";
export type Status = "queued" | "running" | "completed" | "failed";
type Problem = { code: string; message: string };
export type Repository = {
  repository_id: string; display_name: string; path: string;
  snapshot_status: "not_indexed" | "ready" | "stale" | "invalid" | "busy";
  snapshot: { indexed_chunks: number | null } | null;
};
export type Source = {
  file_path: string; symbol_name: string | null; symbol_type: string | null;
  start_line: number; end_line: number; snippet: string;
};
export type Hit = Source & {
  chunk_id: string; rank: number; language: string; snippet_truncated: boolean;
  semantic_score: number | null; bm25_score: number | null; rrf_score: number | null;
};
export type Search = { hits: Hit[]; duration_ms: number; diagnostics: { status: string } };
type AgentConfig = {
  max_rounds: number; max_tool_calls: number; max_calls_per_round: number;
  max_read_lines: number; max_model_requests: number;
};
type Configuration = { max_sources: number; context_chars?: number | null; agent?: AgentConfig | null };
export type Preview = {
  preview_id: string; expires_at: string; repository_id: string; strategy: Strategy;
  configuration: Configuration; model: string; issue: string; source_chars: number;
  source_bytes: number; context_chars: number; sources: (Source & { source_id: string })[];
  additional_disclosure_possible: boolean; disclosure_notice: string;
};
export type Job = {
  job_id: string; state: Status; error: Problem | null;
  progress: { stage: string; chunks_processed: number | null; chunks_total: number | null } | null;
  index_result: { total: number; new: number; reused: number; updated: number; removed: number; embedded: number } | null;
};
type Accepted = { job_id: string };
type Citation = Omit<Source, "snippet"> & { source_id: string; quote: string | null };
type Observation = { statement: string; citations: Citation[] };
type Hypothesis = {
  suspected_cause: string; reasoning: string; citations: Citation[];
  suggested_fixes: string[]; verification_tests: string[];
};
export type Investigation = {
  investigation_id: string; state: Status; error: Problem | null;
  issue_summary: string; strategy: Strategy; model: string; stop_reason: string | null;
  metrics: {
    duration_ms: number; initial_retrieval_ms: number; tool_call_count: number; model_request_count: number;
    valid_citations: number | null; invalid_citations: number | null;
    usage_summary: { input: Usage; output: Usage; total: Usage };
  } | null;
  report: {
    status: "hypotheses" | "insufficient_evidence"; problem_summary: string;
    observations: Observation[]; hypotheses: Hypothesis[]; uncertainties: string[]; missing_evidence: string[];
  } | null;
};
type Usage = { tokens: number | null; status: string };
export type Trace = {
  stop_reason: string; duration_ms: number;
  evidence: (Omit<Source, "snippet" | "symbol_type"> & { source_id: string })[];
  tools: {
    sequence: number; round: number; name: string; status: string; duration_ms: number;
    new_evidence_ids: string[]; evidence_ids: string[]; parameters: Record<string, unknown> | null;
  }[];
  model_requests: { kind: string; status: string; duration_ms: number }[];
};

export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

export function expired(preview: Preview, now = Date.now()): boolean {
  const expiration = Date.parse(preview.expires_at);
  return !Number.isFinite(expiration) || expiration <= now;
}

export function approvalBody(preview: Preview, authorized: boolean) {
  if (authorized !== true) throw new ApiError("permission_required", "Approve disclosure before sending code to Gemini.");
  if (expired(preview)) throw new ApiError("preview_expired", "This preview expired. Generate a new one.");
  return {
    preview_id: preview.preview_id, strategy: preview.strategy,
    configuration: preview.configuration, allow_remote_code: true,
  };
}

export function apiClient(transport = fetch) {
  async function request<T>(path: string, signal: AbortSignal, body?: object): Promise<T> {
    const headers = new Headers({ Accept: "application/json" });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    let response: Response;
    try {
      response = await transport(`/api/local${path}`, {
        method: body === undefined ? "GET" : "POST", headers, signal, credentials: "same-origin",
        redirect: "error", cache: "no-store", referrerPolicy: "no-referrer",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new ApiError("api_unreachable", "The workspace could not connect. Check that both local services are running.");
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = data?.detail;
      throw new ApiError(
        typeof detail?.code === "string" ? detail.code : "request_failed",
        typeof detail?.message === "string" ? detail.message : `The local API returned HTTP ${response.status}.`,
      );
    }
    if (data === null) throw new ApiError("invalid_response", "The local API returned an unreadable response.");
    return data as T;
  }

  return {
    health: (signal: AbortSignal) => request<{ status: string; service: string }>("/health", signal),
    session: (signal: AbortSignal) => request<{ connected: boolean }>("/session", signal),
    pair: (code: string, signal: AbortSignal) => request<{ connected: boolean }>("/session", signal, { code }),
    disconnect: (signal: AbortSignal) => request<{ connected: boolean }>("/session", signal, { disconnect: true }),
    register: (path: string, signal: AbortSignal) => request<Repository>("/repositories", signal, { path }),
    repository: (id: string, signal: AbortSignal) => request<Repository>(`/repositories/${encodeURIComponent(id)}`, signal),
    index: (id: string, signal: AbortSignal) => request<Accepted>(`/repositories/${encodeURIComponent(id)}/index`, signal, { rebuild: false }),
    search: (id: string, query: string, mode: Mode, topK: number, signal: AbortSignal) =>
      request<Search>(`/repositories/${encodeURIComponent(id)}/search`, signal, { query, mode, top_k: topK }),
    preview: (id: string, issue: string, strategy: Strategy, maxSources: number, signal: AbortSignal) =>
      request<Preview>("/investigations/preview", signal,
        { repository_id: id, issue, strategy, configuration: { max_sources: maxSources } }),
    approve: (preview: Preview, authorized: boolean, signal: AbortSignal) =>
      request<Accepted & { investigation_id: string }>("/investigations", signal, approvalBody(preview, authorized)),
    job: (id: string, signal: AbortSignal) => request<Job>(`/jobs/${encodeURIComponent(id)}`, signal),
    investigation: (id: string, signal: AbortSignal) =>
      request<Investigation>(`/investigations/${encodeURIComponent(id)}`, signal),
    trace: (id: string, signal: AbortSignal) =>
      request<{ trace: Trace | null }>(`/investigations/${encodeURIComponent(id)}/trace`, signal),
  };
}

function pause(ms: number, signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, ms);
    function cancel() {
      clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener("abort", cancel, { once: true });
  });
}

export async function pollJob(
  load: (signal: AbortSignal) => Promise<Job>, update: (job: Job) => void,
  signal: AbortSignal, interval = 900, attempts = 400,
) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    signal.throwIfAborted();
    const job = await load(signal);
    signal.throwIfAborted();
    update(job);
    if (job.state === "completed" || job.state === "failed") return job;
    if (attempt + 1 < attempts) await pause(interval, signal);
  }
  throw new ApiError("poll_timeout", "This job is taking longer than expected. Use Resume status to check it again.");
}
