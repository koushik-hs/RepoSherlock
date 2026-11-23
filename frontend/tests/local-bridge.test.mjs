import test from "node:test";
import assert from "node:assert/strict";
import { localBridge } from "../lib/local-bridge.ts";

const origin = "http://127.0.0.1:3000";
const config = { bearer: "backend-private-test-bearer-123456789", pairing: "browser-test-pairing-code-123456789" };
const instant = 1_800_000_000;
const id = "a".repeat(64);
const fail = () => assert.fail("This request must end at the bridge");

function request(path, { method = "GET", body, cookie, headers = {}, service = config, time = instant, upstream = fail } = {}) {
  const incoming = new Request(origin + "/api/local/" + path, {
    method, headers: { Host: "127.0.0.1:3000", ...(method === "POST" && { Origin: origin, "Content-Type": "application/json" }),
      ...(cookie && { Cookie: cookie }), ...headers }, body,
  });
  return localBridge(incoming, path.split("/"), service, upstream, time);
}

async function pair() {
  const response = await request("session", { method: "POST", body: JSON.stringify({ code: config.pairing }) });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { connected: true });
  const cookie = response.headers.get("set-cookie");
  assert.match(cookie, /HttpOnly; SameSite=Strict/);
  assert.ok(!cookie.includes(config.bearer) && !cookie.includes(config.pairing));
  assert.equal(response.headers.get("cache-control"), "no-store");
  return cookie.split(";")[0];
}

test("pairing accepts a separate code, supports logout, and rejects expired and rotated credentials", async () => {
  for (const code of [config.bearer, "a".repeat(43)]) {
    const wrong = await request("session", { method: "POST", body: JSON.stringify({ code }) });
    assert.equal(wrong.status, 401);
    assert.equal(wrong.headers.get("set-cookie"), null);
  }
  const cookie = await pair();
  assert.deepEqual(await (await request("session", { cookie })).json(), { connected: true });
  const expired = await request("repositories/" + id, { cookie, time: instant + 8 * 60 * 60 });
  assert.equal(expired.status, 401);
  assert.equal((await request("repositories/" + id, { cookie, service: { ...config, bearer: "z".repeat(43) } })).status, 401);
  const logout = await request("session", { method: "POST", cookie, body: '{"disconnect":true}' });
  assert.match(logout.headers.get("set-cookie"), /Max-Age=0/);
});

test("malformed routes, origins and destinations are rejected before transport", async () => {
  const cookie = await pair();
  for (const path of ["external", "repositories/relative-path", "jobs/" + "a".repeat(32) + "/extra",
    "health?target=https://example.com"]) {
    assert.equal((await request(path, { cookie })).status, 404);
  }
  assert.equal((await request("repositories", { method: "POST", cookie, body: "{}", headers: { Origin: "https://example.com" } })).status, 403);
  assert.equal((await request("health", { headers: { Host: "example.com" } })).status, 403);
  assert.equal((await request("health", { service: { origin: "https://example.com" } })).status, 503);
});

test("the bridge bounds bodies, rejects credential echoes and replaces browser headers", async () => {
  const cookie = await pair();
  const path = "repositories/" + id + "/search";
  assert.equal((await request(path, { method: "POST", cookie, body: "x".repeat(64 * 1024 + 1) })).status, 413);
  assert.equal((await request(path, { method: "POST", cookie, body: "{}", headers: { "Content-Type": "text/plain" } })).status, 415);
  assert.equal((await request(path, { method: "POST", cookie, body: JSON.stringify({ query: config.pairing }) })).status, 400);
  const response = await request(path, { method: "POST", cookie, body: '{"query":"receipt"}',
    headers: { Authorization: "Bearer untrusted", "X-Custom": "untrusted" }, upstream(url, options) {
      assert.equal(url, "http://127.0.0.1:8000/api/" + path);
      assert.equal(options.redirect, "error");
      assert.equal(options.headers.get("Authorization"), "Bearer " + config.bearer);
      assert.equal(options.headers.get("Cookie"), null);
      assert.equal(options.headers.get("X-Custom"), null);
      assert.equal(options.body, '{"query":"receipt"}');
      return Response.json({ accidental: config.bearer });
    } });
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(config.bearer));
});

test("backend failures stay failures and keep recovery information", async () => {
  const cookie = await pair();
  const path = "jobs/" + "b".repeat(32);
  const offline = await request(path, { cookie, upstream: async () => { throw new Error("Sensitive internal error"); } });
  assert.equal(offline.status, 503);
  assert.equal((await offline.json()).detail.code, "api_unreachable");
  const restart = await request(path, { cookie, upstream: async () => new Response(null, { status: 401 }) });
  assert.equal((await restart.json()).detail.code, "api_session_mismatch");
  const missing = await request(path, { cookie, upstream: async () => Response.json({ detail: { code: "job_unknown" } }, { status: 404 }) });
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).detail.code, "job_unknown");
});
