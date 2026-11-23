import test from "node:test";
import assert from "node:assert/strict";
import { approvalBody, apiClient, expired, pollJob } from "../lib/api.ts";
import { loopbackOrigin } from "../lib/local-bridge.ts";

const config = { max_sources: 6, context_chars: 24000, agent: { max_rounds: 3 } };
const preview = {
  preview_id: "preview-01",
  strategy: "agentic",
  configuration: config,
  expires_at: "2099-01-01T00:00:00Z",
};
const origin = "http://127.0.0.1:8000";
const signal = new AbortController().signal;

test("configured destinations remain on loopback", () => {
  assert.equal(loopbackOrigin(), origin);
  assert.equal(loopbackOrigin("http://127.0.0.1:53182"), "http://127.0.0.1:53182");
  assert.equal(loopbackOrigin("http://[::1]:8000/"), "http://[::1]:8000");
  for (const remote of ["https://example.com", "http://localhost.example.com", "http://localhost:8000/path",
    "ftp://localhost", "http://user@localhost", "http://localhost/?token=example"]) {
    assert.throws(() => loopbackOrigin(remote), /explicit local loopback origin/);
  }
});

test("approval requires current disclosure and explicit confirmation", () => {
  assert.throws(() => approvalBody(preview, false), { code: "permission_required" });
  assert.throws(() => approvalBody({ ...preview, expires_at: "2020-01-01T00:00:00Z" }, true), { code: "preview_expired" });
  assert.equal(expired({ ...preview, expires_at: "invalid" }), true);
  assert.deepEqual(approvalBody(preview, true), {
    preview_id: "preview-01", strategy: "agentic", configuration: config, allow_remote_code: true,
  });
});

test("browser requests use the same-origin bridge and carry no bearer header", async () => {
  const seen = [];
  const transport = async (url, init) => {
    seen.push({ url, init });
    return Response.json({});
  };
  const client = apiClient(transport);
  await client.health(signal);
  await client.register("D:\\repos\\sample", signal);
  await client.search("repo", "total", "lexical", 3, signal);
  await client.preview("repo", "Receipt total mismatch", "agentic", 6, signal);
  assert.throws(() => client.approve(preview, false, signal), { code: "permission_required" });
  await client.approve(preview, true, signal);
  assert.deepEqual(seen.map(({ url }) => url), [
    "/api/local/health", "/api/local/repositories", "/api/local/repositories/repo/search",
    "/api/local/investigations/preview", "/api/local/investigations",
  ]);
  assert.equal(seen[0].init.headers.get("Authorization"), null);
  assert.equal(seen[1].init.headers.get("Authorization"), null);
  assert.equal(seen[1].init.credentials, "same-origin");
  assert.equal(seen[1].init.redirect, "error");
  assert.deepEqual(JSON.parse(seen[2].init.body), { query: "total", mode: "lexical", top_k: 3 });
  assert.deepEqual(JSON.parse(seen[3].init.body).configuration, { max_sources: 6 });
  assert.deepEqual(JSON.parse(seen[4].init.body), approvalBody(preview, true));
});

test("API errors retain their recovery code", async () => {
  const transport = async () => Response.json({ detail: { code: "snapshot_changed", message: "Generate a new preview." } }, { status: 409 });
  await assert.rejects(apiClient(transport).approve(preview, true, signal), {
    code: "snapshot_changed", message: "Generate a new preview.",
  });
});

test("polling stops on a terminal result including failures", async () => {
  for (const final of ["completed", "failed"]) {
    const states = ["queued", "running", final];
    const seen = [];
    const job = await pollJob(async () => ({ state: states.shift() }), ({ state }) => seen.push(state), signal, 0);
    assert.deepEqual(seen, ["queued", "running", final]);
    assert.equal(job.state, final);
  }
});

test("polling is bounded and responds to cancellation", async () => {
  await assert.rejects(pollJob(async () => ({ state: "running" }), () => {}, signal, 0, 1), { code: "poll_timeout" });
  const abort = new AbortController();
  const promise = pollJob(async () => ({ state: "queued" }), () => abort.abort(), abort.signal, 5000);
  await assert.rejects(promise, { name: "AbortError" });
});
