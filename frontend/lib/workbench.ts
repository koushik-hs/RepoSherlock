"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError, apiClient, pollJob, type Investigation, type Job, type Mode, type Preview,
  type Repository, type Search, type Strategy, type Trace,
} from "./api";

type Tracking = { kind: "index"; id: string; repository: string }
  | { kind: "investigation"; id: string; investigation: string };

const refreshPreviewCodes = new Set([
  "snapshot_changed", "index_stale", "preview_unavailable", "preview_configuration_mismatch",
  "preview_model_changed", "preview_expired",
]);

export function useWorkbench(onSessionEnded: (message: string) => void) {
  const [repository, setRepository] = useState<Repository | null>(null);
  const [indexJob, setIndexJob] = useState<Job | null>(null);
  const [search, setSearch] = useState<Search | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [approved, setApproved] = useState(false);
  const [submittingInvestigation, setSubmittingInvestigation] = useState(false);
  const [investigationJob, setInvestigationJob] = useState<Job | null>(null);
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [approvedSources, setApprovedSources] = useState<Preview["sources"]>([]);
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ section: string; message: string } | null>(null);
  const operation = useRef<AbortController | null>(null);

  useEffect(() => () => operation.current?.abort(), []);

  function clearPreview() {
    setPreview(null);
    setApproved(false);
  }

  async function run(section: string, action: (client: ReturnType<typeof apiClient>, signal: AbortSignal) => Promise<void>) {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(section);
    setError(null);
    try {
      await action(apiClient(), controller.signal);
    } catch (failure) {
      if (controller.signal.aborted) return;
      if (failure instanceof ApiError) {
        if (failure.code === "session_required") {
          onSessionEnded(failure.message);
          return;
        }
        if (refreshPreviewCodes.has(failure.code)) clearPreview();
        if (failure.code === "index_stale" || failure.code === "snapshot_changed") {
          setRepository((value) => value && { ...value, snapshot_status: "stale" });
          setSearch(null);
        }
        if (failure.code === "job_unknown" || failure.code === "repository_unknown") {
          setTracking(null);
          setRepository(null);
          setIndexJob(null);
          setInvestigationJob(null);
          setSearch(null);
          setInvestigation(null);
          setTrace(null);
          setApprovedSources([]);
          clearPreview();
        }
      }
      setError({ section, message: failure instanceof Error ? failure.message : "The operation could not finish." });
    } finally {
      if (operation.current === controller) operation.current = null;
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  async function track(client: ReturnType<typeof apiClient>, pending: Tracking, signal: AbortSignal) {
    const update = pending.kind === "index" ? setIndexJob : setInvestigationJob;
    await pollJob((pollSignal) => client.job(pending.id, pollSignal), update, signal);
    if (pending.kind === "index") {
      setRepository(await client.repository(pending.repository, signal));
    } else {
      const result = await client.investigation(pending.investigation, signal);
      setInvestigation(result);
      setTrace((await client.trace(pending.investigation, signal)).trace);
    }
    setTracking(null);
  }

  function register(path: string) {
    return run("workspace", async (client, signal) => {
      const next = await client.register(path.trim(), signal);
      setRepository(next);
      setIndexJob(null);
      setSearch(null);
      setInvestigationJob(null);
      setInvestigation(null);
      setTrace(null);
      setApprovedSources([]);
      clearPreview();
    });
  }

  function refresh() {
    if (repository) return run("workspace", async (client, signal) => {
      const next = await client.repository(repository.repository_id, signal);
      setRepository(next);
      if (next.snapshot_status !== "ready") clearPreview();
    });
  }

  function index() {
    if (!repository || tracking) return;
    return run("workspace", async (client, signal) => {
      const accepted = await client.index(repository.repository_id, signal);
      const next: Tracking = { kind: "index", id: accepted.job_id, repository: repository.repository_id };
      setIndexJob(null);
      setSearch(null);
      clearPreview();
      setTracking(next);
      await track(client, next, signal);
    });
  }

  function find(query: string, mode: Mode, topK: number) {
    if (repository) return run("search", async (client, signal) => {
      setSearch(null);
      setSearch(await client.search(repository.repository_id, query.trim(), mode, topK, signal));
    });
  }

  function prepare(issue: string, strategy: Strategy, maxSources: number) {
    if (repository) return run("investigations", async (client, signal) => {
      clearPreview();
      setPreview(await client.preview(repository.repository_id, issue.trim(), strategy, maxSources, signal));
    });
  }

  function approve() {
    if (!preview || tracking) return;
    return run("investigations", async (client, signal) => {
      setSubmittingInvestigation(true);
      let next: Tracking;
      try {
        const accepted = await client.approve(preview, approved, signal);
        next = { kind: "investigation", id: accepted.job_id, investigation: accepted.investigation_id };
        clearPreview();
        setApprovedSources(preview.sources);
        setInvestigation(null);
        setInvestigationJob(null);
        setTrace(null);
        setTracking(next);
      } finally {
        setSubmittingInvestigation(false);
      }
      await track(client, next, signal);
    });
  }

  function resume() {
    if (tracking) {
      return run(tracking.kind === "index" ? "workspace" : "investigations",
        (client, signal) => track(client, tracking, signal));
    }
  }

  return {
    repository, indexJob, search, preview, approved, submittingInvestigation,
    investigationJob: submittingInvestigation ? null : investigationJob,
    investigation: submittingInvestigation ? null : investigation,
    trace: submittingInvestigation ? null : trace,
    approvedSources, tracking, busy, error,
    register, refresh, index, find, prepare, approve, resume, clearPreview, setApproved,
  };
}
