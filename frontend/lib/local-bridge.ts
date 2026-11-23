import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

const COOKIE = "reposherlock_session";
const COOKIE_PATH = "/api/local";
const SESSION_SECONDS = 8 * 60 * 60;
const BODY_LIMIT = 64 * 1024;
const RESPONSE_LIMIT = 2 * 1024 * 1024;
const credentialPattern = /^[A-Za-z0-9_-]{32,128}$/;
const localHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
const readRoutes = /^(health|repositories\/[a-f0-9]{64}|jobs\/[a-f0-9]{32}|investigations\/[a-f0-9]{32}(?:\/trace)?)$/;
const writeRoutes = /^(repositories(?:\/[a-f0-9]{64}\/(?:index|search))?|investigations(?:\/preview)?)$/;

type Config = { bearer?: string; pairing?: string; origin?: string };

export function loopbackOrigin(value = "http://127.0.0.1:8000"): string {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || !localHosts.has(url.hostname) ||
      url.username || url.password || url.pathname !== "/" || url.search || url.hash ||
      (value !== url.origin && value !== url.origin + "/")) {
    throw new Error("The API must use an explicit local loopback origin.");
  }
  return url.origin;
}

function authorizedOrigin(request: Request) {
  const url = new URL(request.url);
  const host = request.headers.get("host");
  if (!host) return null;
  const origin = loopbackOrigin(`${url.protocol}//${host}`);
  if (request.headers.get("sec-fetch-site") === "cross-site") return null;
  const declared = request.headers.get("origin");
  if (declared !== null && declared !== origin) return null;
  return request.method === "GET" || declared === origin ? origin : null;
}

function reply(data: unknown, status = 200, cookie?: string) {
  const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  if (cookie !== undefined) headers.set("Set-Cookie", cookie);
  return Response.json(data, { status, headers });
}

function problem(status: number, code: string, message: string) {
  return reply({ detail: { code, message } }, status);
}

function equal(a: string, b: string) {
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function sign(value: string, bearer: string) {
  return createHmac("sha256", bearer).update("reposherlock-browser-v1\0").update(value).digest("base64url");
}

function signedCookie(bearer: string, time: number) {
  const value = `${time + SESSION_SECONDS}.${randomBytes(24).toString("base64url")}`;
  return `${value}.${sign(value, bearer)}`;
}

function getCookie(request: Request) {
  return (request.headers.get("cookie") ?? "").split(";").map((entry) => entry.trim())
    .find((entry) => entry.startsWith(COOKIE + "="))?.slice(COOKIE.length + 1) ?? "";
}

function validCookie(value: string, bearer: string, time: number) {
  if (!/^\d{10,12}\.[A-Za-z0-9_-]{32}\.[A-Za-z0-9_-]{43}$/.test(value)) return false;
  const [until, nonce, signature] = value.split(".");
  return Number(until) > time && Number(until) <= time + SESSION_SECONDS &&
    equal(signature, sign(`${until}.${nonce}`, bearer));
}

async function boundedText(stream: ReadableStream<Uint8Array> | null, limit: number) {
  if (!stream) return "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const reader = stream.getReader();
  let result = "", bytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return result + decoder.decode();
      bytes += value.byteLength;
      if (bytes > limit) throw new RangeError("Payload too large");
      result += decoder.decode(value, { stream: true });
    }
  } catch (error) {
    // A stream limit must not wait for another packet on the connection.
    void reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export async function localBridge(request: Request, segments: string[], config: Config,
                                  transport = fetch, time = Math.floor(Date.now() / 1000)) {
  let origin: string | null;
  try {
    origin = authorizedOrigin(request);
  } catch {
    origin = null;
  }
  if (!origin) return problem(403, "origin_forbidden", "Use the local workspace to make this request.");
  const path = segments.join("/");
  const isSession = path === "session";
  const isRead = request.method === "GET";
  if (segments.some((part) => !/^[a-z0-9]+$/.test(part)) || new URL(request.url).search ||
      (!isSession && !(isRead ? readRoutes : writeRoutes).test(path))) {
    return problem(404, "route_unavailable", "This request is outside the local workspace API.");
  }
  const isHealth = isRead && path === "health";
  const { bearer = "", pairing = "" } = config;
  if (!isHealth && (!credentialPattern.test(bearer) || !credentialPattern.test(pairing) || equal(bearer, pairing))) {
    return problem(503, "session_unconfigured", "Start both services with the RepoSherlock launcher to enable browser pairing.");
  }
  const cookie = isHealth ? "" : getCookie(request);
  const connected = !isHealth && validCookie(cookie, bearer, time);
  if (isSession && isRead) return reply({ connected });
  if (!isSession && !isHealth && !connected) {
    return problem(401, "session_required", "The local session ended. Pair again to continue.");
  }
  let body: string | undefined;
  if (!isRead) {
    if (!(request.headers.get("content-type") ?? "").split(";")[0].trim().match(/^application\/json$/i)) {
      return problem(415, "expected_json", "Send this request as JSON.");
    }
    try {
      body = await boundedText(request.body, BODY_LIMIT);
    } catch (error) {
      return error instanceof RangeError
        ? problem(413, "body_too_large", "The request exceeds the local size limit.")
        : problem(400, "invalid_body", "The request could not be read.");
    }
  }
  if (isSession) {
    const options = `Path=${COOKIE_PATH}; HttpOnly; SameSite=Strict${origin.startsWith("https:") ? "; Secure" : ""}`;
    let input: unknown;
    try { input = JSON.parse(body ?? ""); } catch { input = null; }
    if (input && typeof input === "object" && "disconnect" in input && input.disconnect === true) {
      return reply({ connected: false }, 200, `${COOKIE}=; Max-Age=0; ${options}`);
    }
    if (!input || typeof input !== "object" || !("code" in input) ||
        typeof input.code !== "string" || !credentialPattern.test(input.code) || !equal(input.code, pairing)) {
      return problem(401, "pairing_rejected", "That pairing code did not match. Copy the current code from the launcher.");
    }
    return reply({ connected: true }, 200, `${COOKIE}=${signedCookie(bearer, time)}; Max-Age=${SESSION_SECONDS}; ${options}`);
  }
  if (body && (body.includes(bearer) || body.includes(pairing))) {
    return problem(400, "credential_in_input", "Remove credentials from this request.");
  }

  let upstream: string;
  try {
    upstream = loopbackOrigin(config.origin);
  } catch {
    return problem(503, "api_destination_invalid", "The local API destination is misconfigured.");
  }
  try {
    const headers = new Headers({ Accept: "application/json" });
    if (!isHealth) headers.set("Authorization", `Bearer ${bearer}`);
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await transport(`${upstream}/api/${path}`, {
      method: request.method, headers, body, redirect: "error", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(45000)]),
    });
    if (response.status === 401) {
      return problem(503, "api_session_mismatch", "The two services use different credentials. Restart them together.");
    }
    const text = await boundedText(response.body, RESPONSE_LIMIT);
    const data: unknown = JSON.parse(text);
    const normalized = JSON.stringify(data);
    if ([bearer, pairing].some((secret) => secret && normalized.includes(secret))) {
      return problem(502, "unsafe_api_response", "The local API returned a response that could not be displayed safely.");
    }
    return reply(data, response.status);
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof RangeError) {
      return problem(502, "invalid_api_response", "The local API returned an unreadable response.");
    }
    return problem(503, "api_unreachable", "The local API did not respond. Check FastAPI and retry.");
  }
}
