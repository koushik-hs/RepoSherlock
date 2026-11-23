const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.dirname(__dirname);
const { chromium } = require(path.join(root, ".reposherlock", "frontend-browser", "node_modules", "playwright-core"));
const output = process.env.REPOSHERLOCK_QA_OUTPUT;
const origin = process.env.REPOSHERLOCK_QA_WEB_ORIGIN;
const apiOrigin = process.env.REPOSHERLOCK_QA_API_ORIGIN;
const pairing = process.env.REPOSHERLOCK_BROWSER_PAIRING_CODE;
const credentials = [process.env.REPOSHERLOCK_SESSION_TOKEN, process.env.GEMINI_API_KEY, pairing];
const demoIssue = "Receipt totals are too low when one line contains multiple units.";
const issue = demoIssue + " password: fixture-not-for-provider";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  assert.ok(output && origin && apiOrigin && process.env.REPOSHERLOCK_QA_REPOSITORY && credentials.every(Boolean));
  const browser = await chromium.launch({
    executablePath: process.env.REPOSHERLOCK_QA_CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
  });
  const measurements = { searches: [], investigations: {}, browser: {} };
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const capture = (locator, name, options = {}) => locator.screenshot({
    path: path.join(output, name + ".png"),
    animations: "disabled", caret: "hide", ...options,
  });
  const networkChecks = [];
  const pageErrors = [];
  const violations = [];
  page.on("pageerror", (error) => pageErrors.push(error.name));
  page.on("request", (request) => {
    if (request.url().startsWith(apiOrigin + "/") || request.headers().authorization) {
      violations.push("browser bypassed bridge");
    }
  });
  page.on("response", (response) => {
    if (!response.url().startsWith(origin) || !["document", "script", "fetch"].includes(response.request().resourceType())) return;
    networkChecks.push(response.text().then((body) => {
      if (credentials.some((value) => body.includes(value))) violations.push("credential in browser-visible response");
    }).catch(() => {})); // Chrome can cancel an in-flight request on navigation.
  });

  try {
    for (const [width, height, name] of [
      [1440, 900, "desktop"], [1920, 1080, "wide"], [768, 1024, "tablet"], [390, 844, "mobile"],
    ]) {
      await page.setViewportSize({ width, height });
      const response = await page.goto(origin, { waitUntil: "networkidle" });
      assert.equal(response.status(), 200);
      const geometry = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        center: document.querySelector("h1").getBoundingClientRect().x + document.querySelector("h1").offsetWidth / 2,
        motion: getComputedStyle(document.documentElement).scrollBehavior,
        navigation: performance.getEntriesByType("navigation")[0].domContentLoadedEventEnd,
      }));
      assert.equal(geometry.width, width);
      assert.ok(Math.abs(geometry.center - width / 2) < 2);
      assert.equal(geometry.motion, "auto");
      measurements.browser[name] = { viewport: [width, height], dom_content_loaded_ms: Math.round(geometry.navigation) };
      await page.screenshot({ path: path.join(output, name + ".png") });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(origin, { waitUntil: "networkidle" });
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").innerText(), "Skip to workspace");
    assert.equal(await page.locator(":focus").evaluate((element) => getComputedStyle(element).outlineStyle), "solid");
    const scrollStarted = performance.now();
    await page.keyboard.press("Enter");
    await page.waitForURL("**/#workspace");
    measurements.browser.keyboard_navigation_ms = Math.round((performance.now() - scrollStarted) * 10) / 10;
    for (const [name, fragment] of [["Start investigating", "#investigations"], ["Explore code", "#search"],
      ["Local status", "#connection"], ["Open workspace", "#workspace"], ["Docs", "#docs"]]) {
      await page.getByRole("link", { name, exact: true }).click();
      assert.equal(new URL(page.url()).hash, fragment);
    }
    const docs = page.getByRole("link", { name: "API documentation", exact: true });
    const schema = page.getByRole("link", { name: "OpenAPI schema", exact: true });
    assert.equal(await docs.getAttribute("href"), apiOrigin + "/docs");
    assert.equal(await schema.getAttribute("href"), apiOrigin + "/openapi.json");
    assert.ok((await context.request.get(apiOrigin + "/docs")).ok());
    const spec = await (await context.request.get(apiOrigin + "/openapi.json")).json();
    assert.ok(spec.paths["/api/health"]);
    await page.getByText("API online", { exact: true }).waitFor();
    const code = page.getByLabel("Local pairing code");
    const pair = page.getByRole("button", { name: "Connect session" });
    await code.fill("x".repeat(43));
    await pair.click();
    await page.getByRole("alert").getByText(/pairing code did not match/).waitFor();
    await code.fill(pairing);
    await pair.click();
    await page.getByText("Session connected").waitFor();
    const [session] = await context.cookies(origin + "/api/local/session");
    assert.ok(session.httpOnly && session.sameSite === "Strict");
    assert.ok(!credentials.some((value) => session.value.includes(value)));
    assert.ok(!(await page.evaluate(() => document.cookie)).includes("reposherlock_session"));

    const registration = page.locator('[aria-labelledby="repository-title"]');
    const register = page.getByRole("button", { name: "Register repository" });
    const repoPath = page.getByLabel("Absolute repository path");
    await repoPath.fill(path.join(process.env.REPOSHERLOCK_QA_REPOSITORY, "missing"));
    await register.click();
    await registration.getByRole("alert").waitFor();
    await repoPath.fill(process.env.REPOSHERLOCK_QA_REPOSITORY);
    await register.click();
    await registration.getByText("Not indexed", { exact: true }).waitFor();
    const indexStarted = performance.now();
    const jobDone = page.waitForResponse(async (response) => response.url().includes("/api/local/jobs/") &&
      response.ok() && (await response.json()).state === "completed", { timeout: 85000 });
    jobDone.catch(() => {}); // The progress capture can fail before the completion wait is reached.
    await page.getByRole("button", { name: "Build index" }).click();
    await registration.locator(".job").filter({ hasText: /Building vectors|embedded/ }).waitFor({ timeout: 85000 });
    await capture(registration, "indexing", { mask: [repoPath], maskColor: "#252525" });
    const indexJob = await (await jobDone).json();
    await registration.getByText("Index ready", { exact: true }).waitFor();
    measurements.index = {
      worker_ms: Date.parse(indexJob.finished_at) - Date.parse(indexJob.started_at),
      browser_ms: Math.round(performance.now() - indexStarted),
      chunks: indexJob.index_result.total,
    };

    const search = page.locator("#search");
    await search.getByLabel("Search query").fill("receipt_total");
    for (const mode of ["semantic", "semantic", "semantic", "semantic", "bm25", "hybrid", "hybrid", "hybrid"]) {
      await search.getByLabel("Retrieval").selectOption(mode === "bm25" ? "lexical" : mode);
      const returned = page.waitForResponse((response) => response.url().endsWith("/search") && response.request().method() === "POST");
      const started = performance.now();
      await search.getByRole("button", { name: "Search code" }).click();
      const response = await returned;
      assert.equal(response.status(), 200);
      const body = await response.json();
      assert.ok(body.hits.some((hit) => hit.symbol_name === "receipt_total"));
      const source = search.locator("pre").first();
      await source.waitFor();
      assert.ok(parseFloat(await source.evaluate((element) => getComputedStyle(element).fontSize)) >= 13);
      measurements.searches.push({ mode, retrieval_ms: body.duration_ms, browser_ms: Math.round(performance.now() - started) });
    }
    await capture(search, "search");

    const investigation = page.locator("#investigations");
    let approvals = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/api/local/investigations")) approvals++;
    });
    const form = investigation.getByLabel("Bug report");
    await form.fill(demoIssue);
    await capture(investigation, "workspace");
    await form.fill(issue);
    for (const strategy of ["single", "agentic"]) {
      await investigation.getByLabel("Strategy").selectOption(strategy);
      const previewButton = investigation.getByRole("button", { name: "Preview source disclosure" });
      const receivedPreview = page.waitForResponse((response) => response.url().endsWith("/investigations/preview") && response.ok());
      await previewButton.click();
      const preview = await (await receivedPreview).json();
      assert.ok(!JSON.stringify(preview).includes("fixture-not-for-provider"));
      assert.ok(preview.sources.length > 0);
      const approval = investigation.getByRole("button", { name: "Approve and run investigation" });
      await approval.waitFor();
      assert.equal(await approval.isDisabled(), true);
      assert.equal(approvals, strategy === "single" ? 0 : 1);
      if (strategy === "agentic") {
        await investigation.getByText(/additional redacted excerpts found/).waitFor();
        await capture(investigation.locator(".disclosure"), "evidence-preview");
      }
      const traceResponse = page.waitForResponse((response) => response.url().endsWith("/trace") && response.ok());
      const report = investigation.getByRole("region", { name: "Investigation result" });
      let releaseApproval;
      const approvalPath = "**/api/local/investigations";
      if (strategy === "agentic") {
        assert.equal(await report.count(), 1);
        const pending = new Promise((resolve) => { releaseApproval = resolve; });
        await page.route(approvalPath, async (route) => {
          await pending;
          await route.continue();
        }, { times: 1 });
      }
      try {
        await investigation.getByRole("checkbox").check();
        await approval.click();
        if (strategy === "agentic") {
          await investigation.getByRole("status").filter({ hasText: /^Working…$/ }).waitFor();
          await page.getByText("Submitting", { exact: true }).waitFor();
          assert.equal(await report.count(), 0);
          assert.equal(await investigation.getByRole("status").getByText("completed").count(), 0);
        }
      } finally {
        releaseApproval?.();
      }
      await report.getByText(/omits quantities/).first().waitFor({ timeout: 30000 });
      const cite = report.locator(".citations details").first();
      await cite.locator("summary").click();
      assert.match(await cite.locator("pre").innerText(), /sum\(item\.unit_price_cents/);
      const { trace } = await (await traceResponse).json();
      assert.equal(trace.strategy, strategy);
      assert.ok(trace.usage_summary.total.tokens >= 38);
      assert.ok(!JSON.stringify(trace).includes('"snippet"'));
      if (strategy === "agentic") {
        assert.equal(trace.tools[0].name, "find_symbol");
        await investigation.locator("#activity").getByText("find_symbol", { exact: true }).waitFor();
        await capture(report, "result-citations");
        await capture(investigation.locator("#activity"), "agent-trace");
      }
      measurements.investigations[strategy] = {
        duration_ms: trace.duration_ms, retrieval_ms: trace.initial_retrieval_ms, orchestration_ms: trace.orchestration_ms,
        mock_requests: trace.model_requests.length, mock_tokens: trace.usage_summary.total.tokens, tool_calls: trace.tools.length,
      };
    }
    await page.screenshot({ path: path.join(output, "receipt-result.png"), fullPage: true });

    let pollRequests = 0;
    const observePolls = (request) => { if (request.url().includes("/api/local/jobs/")) pollRequests++; };
    page.on("request", observePolls);
    const polling = page.waitForRequest((request) => request.url().includes("/api/local/jobs/"));
    await page.route("**/api/local/jobs/*", () => {});
    await page.getByRole("button", { name: "Build index" }).click();
    await polling;
    await page.goto("about:blank");
    const countOnExit = pollRequests;
    await sleep(1100);
    assert.equal(pollRequests, countOnExit);
    await page.unroute("**/api/local/jobs/*");
    page.off("request", observePolls);

    await page.goto(origin, { waitUntil: "networkidle" });
    await page.getByText("Session connected").waitFor();
    await repoPath.fill(process.env.REPOSHERLOCK_QA_REPOSITORY);
    await register.click();
    await registration.getByText("Index ready", { exact: true }).waitFor();
    await page.route("**/api/local/jobs/*", (route) => route.fulfill({
      status: 404, json: { detail: { code: "job_unknown", message: "Job not found or expired." } },
    }));
    await page.getByRole("button", { name: "Build index" }).click();
    await registration.getByRole("alert").getByText(/Job not found or expired/).waitFor();
    assert.equal(await register.isEnabled(), true);
    await page.unroute("**/api/local/jobs/*");
    await context.clearCookies();
    await register.click();
    await page.locator("#connection").getByRole("alert").getByText(/session ended/).waitFor();
    await page.route("**/api/local/health", (route) => route.fulfill({ status: 503, json: {} }));
    await page.getByRole("button", { name: "Check API" }).click();
    await page.getByText("API offline", { exact: true }).waitFor();

    await Promise.all(networkChecks);
    assert.deepEqual(violations, []);
    assert.deepEqual(pageErrors, []);
    fs.writeFileSync(path.join(output, "browser-measurements.json"), JSON.stringify(measurements, null, 2));
    console.log("Browser smoke passed: runtime docs links, receipt indexing and search, both investigation strategies, repeat-submission status, consent, citations, trace, session recovery, and polling cancellation.");
    console.log(JSON.stringify(measurements));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
