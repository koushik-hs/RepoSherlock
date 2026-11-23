"""Windows browser smoke and interactive offline demo with a fixture transport."""

import ctypes
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from dev_supervisor import (
    BACKEND, FRONTEND, PRIVATE_NAMES, PROVIDER_KEYS, ROOT, StartupError,
    check_frontend_secrets, check_running, local_request, redact, start_service, stop_services, watch,
)
from dev_windows import ProcessTree


def peak_working_set_mib():
    from ctypes import wintypes as w

    class Counters(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("faults", w.DWORD), ("working", ctypes.c_size_t * 8)]

    counters = Counters()
    counters.size = ctypes.sizeof(counters)
    current = ctypes.WinDLL("kernel32").GetCurrentProcess
    current.restype = w.HANDLE
    get_memory = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    get_memory.argtypes = [w.HANDLE, ctypes.POINTER(Counters), w.DWORD]
    if get_memory(current(), ctypes.byref(counters), counters.size):
        return round(counters.working[0] / 1048576, 1)
    return None


def fixture_api():
    import httpx
    import uvicorn
    from google import genai
    from reposherlock.benchmarking.mock import ScriptedProvider
    from reposherlock.investigation.provider import GEMINI_ENDPOINT, configured_model

    key, model = os.environ["GEMINI_API_KEY"], configured_model()
    real_client = genai.Client
    attempts = 0

    def handle(request):
        nonlocal attempts
        attempts += 1
        assert str(request.url).startswith(GEMINI_ENDPOINT)
        assert request.url.path.endswith(f"/models/{model}:generateContent")
        assert request.headers["x-goog-api-key"] == key
        body = json.loads(request.content)
        payload = body["contents"][0]["parts"][0]["text"]
        assert "fixture-not-for-provider" not in payload
        if "tools" in body:
            parts = ([{"functionCall": {"name": "find_symbol", "args": {"symbol": "LineItem"}, "id": "receipt-test"}}]
                     if len(body["contents"]) == 1 else [{"text": "READY"}])
        else:
            result = ScriptedProvider().generate(payload, instruction="", schema={}, allow_remote_code=True)
            parts = [{"text": result.text}]
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 31, "candidatesTokenCount": 7, "totalTokenCount": 38},
        })

    def intercept(**kwargs):
        kwargs["http_options"].httpx_client = httpx.Client(
            transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False,
        )
        return real_client(**kwargs)

    genai.Client = intercept
    # Uvicorn restores and replays the console signal after its graceful drain.
    # Keep the fixture process alive long enough to write the final counters.
    signal.signal(signal.SIGBREAK, lambda *_: None)
    try:
        uvicorn.run("reposherlock.main:app", host="127.0.0.1", port=int(os.environ["REPOSHERLOCK_QA_API_PORT"]),
                    access_log=False, proxy_headers=False)
    finally:
        record = {"mock_requests": attempts, "backend_peak_working_set_mib": peak_working_set_mib()}
        (Path(os.environ["REPOSHERLOCK_QA_OUTPUT"]) / "backend-measurements.json").write_text(json.dumps(record))


def run(*, demo=False):
    with socket.socket() as api_socket, socket.socket() as web_socket:
        api_socket.bind(("127.0.0.1", 0))
        web_socket.bind(("127.0.0.1", 0))
        api_port, web_port = api_socket.getsockname()[1], web_socket.getsockname()[1]
    node = shutil.which("node.exe")
    next_cli = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
    if not node or not next_cli.is_file() or not (FRONTEND / ".next" / "BUILD_ID").is_file():
        raise StartupError("Install frontend dependencies and run npm.cmd run build first.")
    output_root = ROOT / ".reposherlock" / "phase7a-qa"
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="browser-", dir=output_root))
    repo = shutil.copytree(BACKEND / "tests" / "fixtures" / "buggy_receipts", run_dir / "receipt",
                          ignore=shutil.ignore_patterns(".reposherlock", "__pycache__"))
    secrets_by_name = {
        "REPOSHERLOCK_SESSION_TOKEN": secrets.token_urlsafe(32),
        "REPOSHERLOCK_BROWSER_PAIRING_CODE": secrets.token_urlsafe(32),
        "GEMINI_API_KEY": "offline-intercept-" + secrets.token_urlsafe(24),
    }
    source = dict(os.environ)
    private_values = {value for key, value in source.items() if key.upper() in PRIVATE_NAMES and value}
    clean = {key: value for key, value in source.items()
             if key.upper() not in {*secrets_by_name, *PROVIDER_KEYS, "REPOSHERLOCK_DEV_LAUNCHER"}}
    check_frontend_secrets(clean, private_values | set(secrets_by_name.values()))
    api_origin, web_origin = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
    clean.update(REPOSHERLOCK_QA_OUTPUT=str(run_dir), NEXT_TELEMETRY_DISABLED="1", PYTHONIOENCODING="utf-8",
                 REPOSHERLOCK_QA_API_PORT=str(api_port))
    backend = {**clean, "REPOSHERLOCK_SESSION_TOKEN": secrets_by_name["REPOSHERLOCK_SESSION_TOKEN"],
               "GEMINI_API_KEY": secrets_by_name["GEMINI_API_KEY"],
               "REPOSHERLOCK_ALLOWED_ROOTS": str(run_dir), "REPOSHERLOCK_FRONTEND_ORIGINS": "",
               "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "2"}
    if "HF_HOME" not in backend and "HF_HUB_CACHE" not in backend:
        backend["HF_HOME"] = str(ROOT / ".reposherlock" / "hf-cache")
    frontend = {**clean, "REPOSHERLOCK_SESSION_TOKEN": secrets_by_name["REPOSHERLOCK_SESSION_TOKEN"],
                "REPOSHERLOCK_BROWSER_PAIRING_CODE": secrets_by_name["REPOSHERLOCK_BROWSER_PAIRING_CODE"],
                "REPOSHERLOCK_API_ORIGIN": api_origin}
    values = tuple(secrets_by_name.values())
    stop = threading.Event()
    if demo:
        for event in (signal.SIGINT, signal.SIGBREAK):
            signal.signal(event, lambda *_: stop.set())
    tree = ProcessTree()
    services = {}
    started = time.perf_counter()
    try:
        services["FastAPI fixture"] = start_service(
            "FastAPI fixture", [sys.executable, "-u", str(Path(__file__).resolve()), "--fixture-api"], BACKEND, backend, values,
        )
        services["Next.js"] = start_service(
            "Next.js", [node, str(next_cli), "start", "--hostname", "127.0.0.1", "--port", str(web_port)],
            FRONTEND, frontend, values,
        )
        pending = {api_port: ("api", "/api/health"), web_port: ("workspace", "/api/local/health")}
        startup = {}
        while pending and time.perf_counter() - started < 45:
            if stop.is_set():
                return
            check_running(services)
            for port, (name, path) in tuple(pending.items()):
                try:
                    ok = local_request(port, path)[0] == 200
                except OSError:
                    ok = False
                if ok:
                    startup[f"{name}_ready_ms"] = round((time.perf_counter() - started) * 1000, 1)
                    del pending[port]
            if pending:
                time.sleep(.1)
        if pending:
            raise StartupError("Timed out waiting for the local fixture services.")
        (run_dir / "startup.json").write_text(json.dumps(startup, indent=2))
        if demo:
            print("Offline demo: Gemini requests are intercepted locally with scripted receipt responses.")
            print(f"Register this synthetic repository: {repo}")
            watch(services, stop, secrets_by_name["REPOSHERLOCK_BROWSER_PAIRING_CODE"],
                  web_url=web_origin, api_url=api_origin)
            return
        browser_env = {**clean, **secrets_by_name, "REPOSHERLOCK_QA_REPOSITORY": str(repo),
                       "REPOSHERLOCK_QA_WEB_ORIGIN": web_origin, "REPOSHERLOCK_QA_API_ORIGIN": api_origin}
        test = subprocess.run([node, str(ROOT / "scripts" / "verify-browser.cjs")], cwd=ROOT,
                              env=browser_env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        print(redact(test.stdout + test.stderr, values), end="")
        test.check_returncode()
        print(f"Browser artifacts: {run_dir}")
    finally:
        # Use the fixture server's graceful shutdown diagnostics even for a failed smoke test.
        stop_services(services, tree)
    print((run_dir / "backend-measurements.json").read_text())


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    if sys.argv[1:] == ["--fixture-api"]:
        fixture_api()
    elif sys.argv[1:] in ([], ["--demo"]):
        try:
            run(demo=sys.argv[1:] == ["--demo"])
        except StartupError as error:
            sys.exit(str(error))
    else:
        sys.exit("Usage: python scripts/verify_e2e.py [--demo]")
