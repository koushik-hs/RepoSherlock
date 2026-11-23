import json
import os
from dataclasses import replace
from pathlib import Path
from shutil import copytree, ignore_patterns
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from fastapi.testclient import TestClient

from reposherlock.api import investigations
from reposherlock.api.jobs import MAX_OUTSTANDING
from reposherlock.api.security import MAX_BODY_BYTES
from reposherlock.api.settings import ApiSettings, DEFAULT_FIXTURES
from reposherlock.benchmarking.mock import ScriptedProvider
from reposherlock.investigation import service as single_service
from reposherlock.main import create_app
from reposherlock.semantic import indexing
from test_gemini_provider import FAKE_KEY, _error, _plan_response, stub_http
from test_investigation_benchmark import FixtureEmbeddings

TOKEN = "phase6a-offline-session-42-x6gq1279"
ORIGIN = "http://localhost:3000"
ISSUE = "Receipt totals are too low when one line contains multiple units. Single-unit purchases look fine."
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def copy_receipts(root, name):
    return copytree(DEFAULT_FIXTURES / "buggy_receipts", root / name,
                    ignore=ignore_patterns(".reposherlock", "__pycache__"))


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    root = tmp_path / "allowed"
    root.mkdir()
    repo = copy_receipts(root, "shop")
    settings = ApiSettings((root,), (ORIGIN,), TOKEN)
    with TestClient(create_app(settings, embeddings=FixtureEmbeddings()),
                    base_url="http://127.0.0.1:8000", headers=HEADERS) as client:
        yield client, repo


def success(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def problem(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def register(client, path):
    return success(client.post("/api/repositories", json={"path": str(path)}))


def start_index(client, repo):
    return success(client.post(f"/api/repositories/{repo}/index", json={}), 202)["job_id"]


def poll(client, job, *, terminal=True):
    end = monotonic() + 10
    while monotonic() < end:
        data = success(client.get(f"/api/jobs/{job}"))
        if data["state"] in ({"completed", "failed"} if terminal else {"running"}):
            return data
        sleep(0.015)
    pytest.fail(f"Job did not reach expected state: {data}")


@pytest.fixture
def indexed(local):
    client, path = local
    repo = register(client, path)["repository_id"]
    assert poll(client, start_index(client, repo))["state"] == "completed"
    return client, path, repo


def preview(client, repo, strategy="single", issue=ISSUE, **options):
    return success(client.post("/api/investigations/preview", json={
        "repository_id": repo, "strategy": strategy, "issue": issue, **options,
    }))


def approval(p, **updates):
    return {**{key: p[key] for key in ("preview_id", "strategy", "configuration")},
            "allow_remote_code": True, **updates}


def stub_reply(request, *, invalid=False):
    request_body = json.loads(request.content)
    payload = request_body["contents"][0]["parts"][0]["text"]
    if "tools" in request_body:
        return _plan_response([{"text": "READY"}])
    reply = ScriptedProvider().generate(payload, instruction="", schema={}, allow_remote_code=True)
    document = json.loads(reply.text)
    if invalid:
        document["hypotheses"][0]["citations"][0]["start_line"] = 1
    return _plan_response([{"text": json.dumps(document)}])


def test_registration_scope_status_index_and_search(local):
    client, path = local
    record = register(client, path)
    repo = record["repository_id"]
    assert record == register(client, path)
    assert record["path"] == "root-1/shop" and record["display_name"] == "shop"
    assert record["snapshot_status"] == "not_indexed" and record["index_available"] is False
    assert not (path / ".reposherlock").exists()
    problem(client.get("/api/repositories/" + "a" * 64), 404, "repository_unknown")
    problem(client.post("/api/repositories", json={"path": str(path.parent.parent)}), 403,
            "repository_outside_allowed_roots")
    problem(client.post("/api/repositories", json={"path": str(path / "missing")}), 404, "repository_missing")
    problem(client.post("/api/repositories", json={"path": str(path / "receipts.py")}), 400, "invalid_path")
    for invalid in ("../shop", f"{path}/../shop", "//server/share", "D:shop", f"{path}:stream", f"{path}/NUL"):
        problem(client.post("/api/repositories", json={"path": invalid}), 400, "invalid_path")

    status = poll(client, start_index(client, repo))
    assert status["state"] == "completed" and status["index_result"]["embedded"] > 0
    assert status["progress"]["stage"] == "publishing"
    assert status["progress"]["chunks_processed"] == status["index_result"]["total"]
    snapshot = success(client.get(f"/api/repositories/{repo}"))
    assert snapshot["snapshot_status"] == "ready" and snapshot["snapshot"]["indexed_chunks"] > 0
    repeated = poll(client, start_index(client, repo))
    assert repeated["progress"]["stage"] == "unchanged" and repeated["index_result"]["embedded"] == 0
    for mode in ("lexical", "semantic", "hybrid"):
        search = success(client.post(f"/api/repositories/{repo}/search", json={
            "query": "receipt_total", "top_k": 3, "mode": mode,
        }))
        assert search["snapshot"] == snapshot["snapshot"]
        assert search["hits"][0]["rank"] == 1
        assert any(hit["symbol_name"] == "receipt_total" and hit["start_line"] == 10 for hit in search["hits"])
        assert "status" in search["diagnostics"]


def test_missing_access_and_links_are_rejected(local, monkeypatch):
    client, path = local
    target = path.parent.parent / "outside"
    target.mkdir()
    link = path.parent / "alias"
    if os.name == "nt":
        from _winapi import CreateJunction

        CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    problem(client.post("/api/repositories", json={"path": str(link)}), 403, "unsafe_path")
    problem(client.post("/api/repositories", json={"path": str(link / "next")}), 403, "unsafe_path")

    scandir = os.scandir

    def inaccessible(location):
        if Path(location) == path:
            raise PermissionError("private system information")
        return scandir(location)

    monkeypatch.setattr(os, "scandir", inaccessible)
    response = client.post("/api/repositories", json={"path": str(path)})
    problem(response, 403, "repository_inaccessible")
    assert "private system" not in response.text


def test_cors_host_session_json_and_validation(local):
    client, path = local
    routes = success(client.get("/openapi.json"))["paths"]
    assert len(routes) == 10 and "security" not in routes["/api/health"]["get"]
    assert routes["/api/repositories"]["post"]["security"] == [{"LocalSession": []}]
    probe = client.options("/api/repositories", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "authorization,content-type"})
    assert probe.status_code == 200 and probe.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Access-Control-Allow-Credentials" not in probe.headers
    bad = "https://untrusted.invalid"
    assert client.options("/api/repositories", headers={"Origin": bad, "Access-Control-Request-Method": "POST"}).status_code == 400
    problem(client.post("/api/repositories", json={"path": str(path)}, headers={"Origin": bad}), 403, "origin_forbidden")
    problem(client.post("/api/repositories", json={"path": str(path)}, headers={"Sec-Fetch-Site": "cross-site"}),
            403, "origin_forbidden")
    problem(client.post("/api/repositories", json={"path": str(path)}, headers={"Host": "untrusted.invalid"}),
            400, "invalid_host")
    problem(client.post("/api/repositories", json={"path": str(path)}, headers={"Authorization": ""}),
            401, "local_session_required")
    problem(client.post("/api/repositories", data={"path": str(path)}), 415, "json_required")
    problem(client.post("/api/repositories", json={"path": "x" * MAX_BODY_BYTES}), 413, "request_too_large")
    problem(client.post("/api/repositories", content=iter([b"x" * 40000, b"x" * 40000]),
                        headers={"Content-Type": "application/json"}), 413, "request_too_large")
    problem(client.post("/api/repositories", content=b"{", headers={"Content-Type": "application/json"}),
            422, "invalid_request")
    invalid = client.post("/api/repositories", json={"path": str(path), "api_key": FAKE_KEY})
    problem(invalid, 422, "invalid_request")
    assert FAKE_KEY not in invalid.text
    accepted = client.post("/api/repositories", json={"path": str(path)}, headers={"Origin": ORIGIN})
    assert accepted.status_code == 200
    assert accepted.headers["Access-Control-Allow-Origin"] == ORIGIN and accepted.headers["Cache-Control"] == "no-store"


def test_configuration_defaults_are_narrow_and_deny_without_session(monkeypatch):
    for name in ("REPOSHERLOCK_ALLOWED_ROOTS", "REPOSHERLOCK_FRONTEND_ORIGINS", "REPOSHERLOCK_SESSION_TOKEN",
                 "REPOSHERLOCK_SWAGGER_ASSETS"):
        monkeypatch.delenv(name, raising=False)
    settings = ApiSettings.from_environment()
    assert settings.allowed_roots == (DEFAULT_FIXTURES,) and settings.frontend_origins == ()
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        assert client.get("/api/health").status_code == 200
        problem(client.post("/api/repositories", json={"path": str(DEFAULT_FIXTURES)}), 503, "local_session_unconfigured")
    with pytest.raises(ValueError, match="explicit loopback"):
        ApiSettings(frontend_origins=("*",))


def test_swagger_only_loads_local_assets(tmp_path):
    settings = ApiSettings(swagger_assets=tmp_path)
    (tmp_path / "swagger-ui-bundle.js").write_text("// static offline fixture")
    (tmp_path / "swagger-ui.css").write_text("/* static offline fixture */")
    (tmp_path / "not-public.css").write_text("private")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        page = client.get("/docs")
        assert page.status_code == 200
        assert "/docs/assets/swagger-ui-bundle.js" in page.text and "https://" not in page.text
        assert "connect-src 'self'" in page.headers["Content-Security-Policy"]
        assert '"validatorUrl": null' in page.text and "nonce=" in page.text
        assert client.get("/docs/assets/swagger-ui-bundle.js").text == "// static offline fixture"
        assert client.get("/docs/assets/swagger-ui.css").status_code == 200
        assert client.get("/docs/assets/not-public.css").status_code == 422


def test_search_redacts_snippets_and_query_diagnostics(local, monkeypatch):
    client, path = local
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    (path / "example.py").write_text(f'DEMO = "{FAKE_KEY}"\npassword = "clearly_synthetic_secret"\n')
    repo = register(client, path)["repository_id"]
    assert poll(client, start_index(client, repo))["state"] == "completed"
    result = success(client.post(f"/api/repositories/{repo}/search", json={
        "query": "DEMO password: clearly_synthetic_secret", "mode": "lexical",
    }))
    assert any("[REDACTED]" in hit["snippet"] for hit in result["hits"])
    assert FAKE_KEY not in json.dumps(result) and "clearly_synthetic_secret" not in json.dumps(result)


def test_preview_authorization_one_use_and_missing_model(indexed, monkeypatch, stub_http):
    client, _, repo = indexed
    traffic, _ = stub_http(stub_reply)
    p = preview(client, repo, issue=ISSUE + " Password: clearly_synthetic_secret")
    assert "clearly_synthetic_secret" not in json.dumps(p) and "[REDACTED]" in p["issue"]
    assert p["source_chars"] == sum(len(span["snippet"]) for span in p["sources"]) > 0
    assert p["context_chars"] > p["source_chars"] and p["additional_disclosure_possible"] is False
    problem(client.post("/api/investigations", json=approval(p, allow_remote_code=False)), 403,
            "remote_code_permission_required")
    problem(client.post("/api/investigations", json=approval(p, allow_remote_code="true")), 422, "invalid_request")
    problem(client.post("/api/investigations", json=approval(p, configuration={"max_sources": 1})),
            409, "preview_configuration_mismatch")
    problem(client.post("/api/investigations", json=approval(p, strategy="unknown")), 422, "invalid_request")
    problem(client.post("/api/investigations", json=approval(p, api_key=FAKE_KEY)), 422, "invalid_request")
    problem(client.post("/api/investigations", json=approval(p)), 503, "model_configuration_missing")
    assert not traffic
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_MODEL", "changed-model")
    problem(client.post("/api/investigations", json=approval(p)), 409, "preview_model_changed")
    monkeypatch.delenv("GEMINI_MODEL")
    launched = success(client.post("/api/investigations", json=approval(p)), 202)
    identifier = launched["investigation_id"]
    assert identifier == launched["job_id"] and poll(client, identifier)["state"] == "completed"
    problem(client.post("/api/investigations", json=approval(p)), 410, "preview_unavailable")
    [sent] = traffic
    initial = json.loads(json.loads(sent.content)["contents"][0]["parts"][0]["text"])
    assert initial == {"issue": p["issue"], "sources": p["sources"]}
    result = success(client.get(f"/api/investigations/{identifier}"))
    trace = success(client.get(f"/api/investigations/{identifier}/trace"))
    assert result["snapshot"] == p["snapshot"]
    assert result["report"]["hypotheses"][0]["citations"][0]["file_path"] == "receipts.py"
    assert result["metrics"]["model_request_count"] == 1
    assert result["metrics"]["valid_citations"] == 1 and result["metrics"]["invalid_citations"] == 0
    assert result["metrics"]["usage_summary"]["total"]["tokens"] == 12
    assert trace["trace"]["model_requests"][0]["kind"] == "report"
    for forbidden in (FAKE_KEY, TOKEN, "clearly_synthetic_secret", "snippet", '"payload"'):
        assert forbidden not in json.dumps(trace)
    problem(client.get(f"/api/investigations/{identifier}", headers={"Authorization": ""}), 401, "local_session_required")


def test_preview_expiration_stale_index_and_preview_credential_rejection(indexed, monkeypatch, stub_http):
    client, path, repo = indexed
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    traffic, _ = stub_http(stub_reply)
    now = [100.0]
    monkeypatch.setattr(investigations, "monotonic", lambda: now[0])
    p = preview(client, repo, "agentic")
    assert p["additional_disclosure_possible"] and "additional redacted excerpts" in p["disclosure_notice"]
    now[0] += 301
    problem(client.post("/api/investigations", json=approval(p)), 410, "preview_unavailable")
    p = preview(client, repo)
    with (path / "receipts.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# changed source\n")
    problem(client.post("/api/investigations", json=approval(p)), 409, "snapshot_changed")
    problem(client.post(f"/api/repositories/{repo}/search", json={"query": "receipt"}), 409, "index_stale")
    assert success(client.get(f"/api/repositories/{repo}"))["snapshot_status"] == "stale"
    problem(client.post("/api/investigations/preview", json={"repository_id": repo, "strategy": "single",
                                                            "issue": FAKE_KEY}), 422, "credential_in_input")
    assert not traffic


def test_worker_rechecks_exact_context_before_provider(indexed, monkeypatch, stub_http):
    client, _, repo = indexed
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    traffic, _ = stub_http(stub_reply)
    p = preview(client, repo)
    select = single_service.select_context

    def changed(*args, **kwargs):
        context = select(*args, **kwargs)
        return replace(context, payload=context.payload + " ")

    monkeypatch.setattr(single_service, "select_context", changed)
    job = success(client.post("/api/investigations", json=approval(p)), 202)["job_id"]
    assert poll(client, job)["error"]["code"] == "snapshot_changed"
    assert not traffic


@pytest.mark.parametrize("strategy", ["single", "agentic"])
@pytest.mark.parametrize("changed", [False, True])
def test_snapshot_is_checked_on_each_transport_attempt(indexed, monkeypatch, stub_http, strategy, changed):
    client, path, repo = indexed
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    reports = 0

    def handler(request):
        nonlocal reports
        if "tools" in json.loads(request.content):
            return stub_reply(request)
        reports += 1
        if reports == 1:
            if changed:
                with (path / "receipts.py").open("a", encoding="utf-8") as stream:
                    stream.write("\n# edited after the authorized attempt\n")
            return _error(503)
        return stub_reply(request)

    traffic, waits = stub_http(handler)
    p = preview(client, repo, strategy)
    job = success(client.post("/api/investigations", json=approval(p)), 202)["job_id"]
    result = poll(client, job)
    report = success(client.get(f"/api/investigations/{job}"))
    assert waits == [0.25]
    assert reports == (1 if changed else 2)
    assert len(traffic) == reports + (strategy == "agentic")
    if changed:
        assert result["error"]["code"] == "snapshot_changed" and report["report"] is None
    else:
        assert result["state"] == "completed" and report["report"]["status"] == "hypotheses"
        assert report["metrics"]["usage_summary"]["total"]["tokens"] == (12 if strategy == "single" else 24)


def test_agentic_dynamic_disclosure_and_tool_metadata(local, monkeypatch, stub_http):
    client, path = local
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    shipping = copytree(DEFAULT_FIXTURES / "buggy_shipping", path.parent / "shipping",
                        ignore=ignore_patterns(".reposherlock", "__pycache__"))
    repo = register(client, shipping)["repository_id"]
    assert poll(client, start_index(client, repo))["state"] == "completed"
    planned = []

    def handler(request):
        body = json.loads(request.content)
        if "tools" in body and not planned:
            planned.append(body)
            return _plan_response([{"functionCall": {
                "name": "find_symbol", "args": {"symbol": "standard_fee"}, "id": "safe-call-1",
            }}])
        return stub_reply(request)

    traffic, _ = stub_http(handler)
    p = preview(client, repo, "agentic", issue="checkout_shipping_cents charges exactly $50 orders the shipping fee.",
                configuration={"max_sources": 1})
    assert [source["file_path"] for source in p["sources"]] == ["checkout.py"]
    assert not traffic
    job = success(client.post("/api/investigations", json=approval(p)), 202)["job_id"]
    assert poll(client, job)["state"] == "completed"
    assert len(traffic) == 3
    assert b"rates.py" not in traffic[0].content and b"rates.py" in traffic[1].content
    trace = success(client.get(f"/api/investigations/{job}/trace"))["trace"]
    assert trace["tools"][0]["name"] == "find_symbol" and trace["tools"][0]["new_evidence_ids"]
    assert '"snippet"' not in json.dumps(trace)


@pytest.mark.parametrize(("strategy", "kind", "error_code", "attempts"), [
    ("agentic", "ready", None, 2), ("agentic", "quota", "provider_quota", 1),
    ("single", "authentication", "provider_auth", 1),
    ("single", "timeout", "provider_timeout", 3), ("single", "unavailable", "provider_unavailable", 3),
    ("single", "invalid", "invalid_report", 1), ("single", "malformed", "invalid_report", 1),
])
def test_provider_success_and_failure_are_distinct(indexed, monkeypatch, stub_http,
                                                   strategy, kind, error_code, attempts):
    client, _, repo = indexed
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)

    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout(f"internal {FAKE_KEY}", request=request)
        if kind in ("quota", "unavailable"):
            return _error(429 if kind == "quota" else 503)
        if kind == "authentication":
            return _error(401)
        if kind == "malformed":
            return _plan_response([{"text": "{"}])
        return stub_reply(request, invalid=kind == "invalid")

    traffic, _ = stub_http(handler)
    p = preview(client, repo, strategy)
    assert not traffic
    job = success(client.post("/api/investigations", json=approval(p)), 202)["job_id"]
    result = poll(client, job)
    report = success(client.get(f"/api/investigations/{job}"))
    trace = success(client.get(f"/api/investigations/{job}/trace"))["trace"]
    assert len(traffic) == attempts and FAKE_KEY not in json.dumps(report)
    if error_code:
        assert result["state"] == report["state"] == "failed"
        assert report["error"]["code"] == error_code and report["report"] is None
        if kind == "invalid":
            assert report["metrics"]["invalid_citations"] == 1
    else:
        assert result["state"] == "completed" and report["report"]["status"] == "hypotheses"
    assert trace["stop_reason"] == report["stop_reason"]


def test_empty_repository_has_empty_search_and_preview(local):
    client, path = local
    empty = path.parent / "empty"
    empty.mkdir()
    repo = register(client, empty)["repository_id"]
    assert poll(client, start_index(client, repo))["index_result"]["total"] == 0
    response = success(client.post(f"/api/repositories/{repo}/search", json={"query": "receipt", "mode": "hybrid"}))
    assert response["hits"] == []
    assert preview(client, repo)["sources"] == []


def test_failed_indexing_is_safe_and_can_retry(local, monkeypatch):
    client, path = local
    repo = register(client, path)["repository_id"]
    build = indexing.build_semantic_index

    def unavailable(*args, **kwargs):
        raise RuntimeError(FAKE_KEY)

    monkeypatch.setattr(indexing, "build_semantic_index", unavailable)
    job = poll(client, start_index(client, repo))
    assert job["state"] == "failed" and job["error"]["code"] == "index_failed"
    assert FAKE_KEY not in json.dumps(job)
    monkeypatch.setattr(indexing, "build_semantic_index", build)
    assert poll(client, start_index(client, repo))["state"] == "completed"


def test_fresh_lifespan_has_no_running_job_from_prior_instance(local, monkeypatch):
    client, path = local
    repo = register(client, path)["repository_id"]
    entered, release = Event(), Event()
    build = indexing.build_semantic_index

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return build(*args, **kwargs)

    monkeypatch.setattr(indexing, "build_semantic_index", blocked)
    job = start_index(client, repo)
    try:
        assert entered.wait(5)
        assert poll(client, job, terminal=False)["state"] == "running"
        with TestClient(create_app(ApiSettings((path.parent,), (ORIGIN,), TOKEN), embeddings=FixtureEmbeddings()),
                        base_url="http://127.0.0.1:8000", headers=HEADERS) as restarted:
            problem(restarted.get(f"/api/jobs/{job}"), 404, "job_unknown")
            problem(restarted.get(f"/api/repositories/{repo}"), 404, "repository_unknown")
    finally:
        release.set()


def test_repeated_index_submissions_and_queue_limits(local, monkeypatch):
    client, path = local
    entered, release = Event(), Event()
    build = indexing.build_semantic_index

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return build(*args, **kwargs)

    monkeypatch.setattr(indexing, "build_semantic_index", blocked)
    repositories = [register(client, path)["repository_id"]]
    repositories += [register(client, copy_receipts(path.parent, f"repo-{index}"))["repository_id"]
                     for index in range(MAX_OUTSTANDING)]
    first = start_index(client, repositories[0])
    try:
        assert entered.wait(5)
        assert poll(client, first, terminal=False)["state"] == "running"
        problem(client.post(f"/api/repositories/{repositories[0]}/index", json={}), 409, "index_already_queued")
        queued = [start_index(client, repo) for repo in repositories[1:-1]]
        assert all(success(client.get(f"/api/jobs/{job}"))["state"] == "queued" for job in queued)
        response = client.post(f"/api/repositories/{repositories[-1]}/index", json={})
        problem(response, 429, "job_queue_full")
    finally:
        release.set()
    assert all(poll(client, job)["state"] == "completed" for job in (first, *queued))
