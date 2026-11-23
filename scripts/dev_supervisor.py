"""Run the two Windows development servers from one console."""

import http.client
import importlib.util
import json
import msvcrt
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from dev_windows import ParentProcess, ProcessTree, TemporaryClipboard

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
HOST = "127.0.0.1"
API_PORT = 8000
WEB_PORT = 3000
API_URL = f"http://{HOST}:{API_PORT}"
WEB_URL = f"http://{HOST}:{WEB_PORT}"
SESSION = "REPOSHERLOCK_SESSION_TOKEN"
PAIRING = "REPOSHERLOCK_BROWSER_PAIRING_CODE"
PROVIDER_KEYS = {"GEMINI_API_KEY", "GOOGLE_API_KEY"}
PRIVATE_NAMES = PROVIDER_KEYS | {SESSION, PAIRING}
START_SECONDS = 90
STOP_SECONDS = 8


class StartupError(RuntimeError):
    pass


def split_environment(source, token, pairing):
    backend = dict(source)
    backend.pop(PAIRING, None)
    backend[SESSION] = token
    origins = [value.strip() for value in source.get("REPOSHERLOCK_FRONTEND_ORIGINS", "").split(",")]
    if WEB_URL not in origins:
        configured = source.get("REPOSHERLOCK_FRONTEND_ORIGINS", "").strip().strip(",")
        backend["REPOSHERLOCK_FRONTEND_ORIGINS"] = f"{configured},{WEB_URL}" if configured else WEB_URL
    cache = ROOT / ".reposherlock" / "hf-cache"
    if "HF_HOME" not in backend and "HF_HUB_CACHE" not in backend and (cache / "hub").is_dir():
        backend["HF_HOME"] = str(cache)
    backend["PYTHONIOENCODING"] = "utf-8"

    frontend = {
        name: value for name, value in source.items()
        if name.upper() not in PROVIDER_KEYS
    }
    frontend[SESSION] = token
    frontend[PAIRING] = pairing
    frontend["NODE_ENV"] = "development"
    frontend["REPOSHERLOCK_API_ORIGIN"] = API_URL
    frontend["REPOSHERLOCK_DEV_LAUNCHER"] = "1"
    frontend.setdefault("NEXT_TELEMETRY_DISABLED", "1")
    return backend, frontend


def check_frontend_secrets(environment, known_secrets):
    private = "|".join(sorted(PRIVATE_NAMES))
    names = re.compile(rf"(?im)^\s*(?:export\s+)?(?:NEXT_PUBLIC_)?(?:{private})\s*(?:=|:\s)")
    for name in (".env.development.local", ".env.local", ".env.development", ".env"):
        path = FRONTEND / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig")
        if names.search(text) or any(value in text for value in known_secrets):
            raise StartupError(f"Move credentials out of frontend/{name} and set the Gemini key in the launching shell.")
    for name, value in environment.items():
        if name.upper().startswith("NEXT_PUBLIC_") and (
            names.match(f"{name}=") or any(secret in value for secret in known_secrets)
        ):
            raise StartupError("Remove private credentials from NEXT_PUBLIC_ variables before starting Next.js.")


def check_dependencies(node, environment, needs_gemini):
    next_cli = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
    if sys.version_info < (3, 11):
        raise StartupError("The backend virtual environment needs Python 3.11 or newer.")
    if not node:
        raise StartupError("Node.js was not found on PATH. Install Node.js 24 and reopen PowerShell.")
    if not next_cli.is_file():
        raise StartupError("Frontend packages are missing. Run npm.cmd ci from frontend, then retry.")
    try:
        version = subprocess.run([node, "--version"], env=environment, capture_output=True, check=True, timeout=10).stdout
        major, minor = (int(part) for part in version.strip().lstrip(b"v").split(b".")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        raise StartupError("Node.js could not report its version. Check the Node.js installation.") from None
    if (major, minor) < (20, 9):
        raise StartupError("Next.js needs Node.js 20.9 or newer. Node.js 24 also supports the frontend tests.")

    dependencies = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["dependencies"]
    resolve = "for (const name of process.argv.slice(1)) require.resolve(name);"
    try:
        subprocess.run([node, "-e", resolve, *dependencies], cwd=FRONTEND, env=environment,
                       capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise StartupError("Frontend packages could not be resolved. Run npm.cmd ci from frontend.") from None

    modules = ["fastapi", "uvicorn", "pydantic", "chromadb", "tree_sitter", "tree_sitter_python",
               "tree_sitter_javascript", "tree_sitter_typescript", "sentence_transformers", "transformers", "torch"]
    if needs_gemini:
        modules += ["google.genai", "httpx"]
    missing = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except ModuleNotFoundError:
            missing.append(name)
    if missing:
        raise StartupError(f"Backend dependencies are missing ({', '.join(missing)}). See README.md for the backend install command.")
    return next_cli


def check_settings(environment):
    # The application remains the authority on allowed paths and browser origins.
    os.environ.update({"REPOSHERLOCK_FRONTEND_ORIGINS": environment["REPOSHERLOCK_FRONTEND_ORIGINS"],
                       SESSION: environment[SESSION]})
    try:
        from reposherlock.api.settings import ApiSettings

        ApiSettings.from_environment()
    except ModuleNotFoundError:
        raise StartupError("Install the backend project and dependencies in backend/.venv as described in README.md.") from None
    except (ValueError, OSError):
        raise StartupError("Check REPOSHERLOCK_ALLOWED_ROOTS, REPOSHERLOCK_FRONTEND_ORIGINS, and REPOSHERLOCK_SWAGGER_ASSETS.") from None


def check_ports():
    for port in (API_PORT, WEB_PORT):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((HOST, port))
            except OSError as error:
                if getattr(error, "winerror", None) == 10048:
                    raise StartupError(f"Port {port} is already in use. Stop its listener and run the launcher again.") from None
                raise StartupError(f"Port {port} is unavailable on {HOST}. Check for an existing listener or Windows port reservation.") from None


def redact(text, private_values):
    for value in private_values:
        text = text.replace(value, "[redacted]")
    return text


def start_service(name, command, workdir, environment, private_values):
    try:
        process = subprocess.Popen(
            command, cwd=workdir, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError:
        raise StartupError(f"{name} could not start. Check the local executable and permissions.") from None

    def forward():
        with process.stdout:
            for line in process.stdout:
                print(f"[{name}] {redact(line.rstrip(), private_values)}", flush=True)

    thread = threading.Thread(target=forward, name=name, daemon=True)
    thread.start()
    return process, thread


def local_request(port, path, headers=None):
    with closing(http.client.HTTPConnection(HOST, port, timeout=1.5)) as connection:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(262144), response.getheader("Access-Control-Allow-Origin")


def api_ready(token):
    status, body, _ = local_request(API_PORT, "/api/health")
    if status != 200 or json.loads(body) != {"status": "ok", "service": "reposherlock"}:
        return False
    status, body, origin = local_request(
        API_PORT, "/api/repositories/" + "0" * 64,
        {"Authorization": f"Bearer {token}", "Origin": WEB_URL},
    )
    return status == 404 and origin == WEB_URL and json.loads(body)["detail"]["code"] == "repository_unknown"


def web_ready():
    status, body, _ = local_request(WEB_PORT, "/")
    if status != 200 or b"RepoSherlock" not in body:
        return False
    status, body, _ = local_request(WEB_PORT, "/api/local/session")
    return status == 200 and json.loads(body) == {"connected": False}


def check_running(services):
    for name, (process, _thread) in services.items():
        status = process.poll()
        if status is not None:
            raise StartupError(f"{name} stopped with exit code {status}. See its output above.")


def await_readiness(services, token, stop, parent):
    deadline = time.monotonic() + START_SECONDS
    pending = {"FastAPI": lambda: api_ready(token), "Next.js": web_ready}
    while pending and not stop.is_set() and parent.running():
        check_running(services)
        for name, ready in tuple(pending.items()):
            try:
                if ready():
                    del pending[name]
            except (OSError, ValueError, KeyError, http.client.HTTPException):
                continue
        if pending and time.monotonic() >= deadline:
            raise StartupError(f"Readiness timed out for {', '.join(pending)}. See startup output above.")
        if pending:
            stop.wait(.2)
    return not stop.is_set() and parent.running()


def watch(services, stop, pairing, parent=None, *, web_url=WEB_URL, api_url=API_URL):
    clipboard = None
    expires = None
    interactive = sys.stdin.isatty()
    print(f"Workspace  {web_url}\nAPI health {api_url}/api/health\nAPI docs   {api_url}/docs")
    if interactive:
        print("Press T to copy the pairing code for 60 seconds, then paste it into Local pairing code in the workspace.")
    print("Press Ctrl+C to stop both services.", flush=True)
    try:
        while not stop.wait(.15) and (parent is None or parent.running()):
            check_running(services)
            if expires is not None and time.monotonic() >= expires and clipboard.clear():
                expires = None
            if interactive and msvcrt.kbhit() and msvcrt.getwch().lower() == "t":
                try:
                    if clipboard is None:
                        clipboard = TemporaryClipboard()
                    if clipboard.copy(pairing):
                        expires = time.monotonic() + 60
                        print("Pairing code copied temporarily.", flush=True)
                    else:
                        print("Clipboard is unavailable. Press T to retry.", flush=True)
                except OSError:
                    print("Clipboard is unavailable in this console.", flush=True)
    finally:
        if clipboard is not None:
            try:
                for _ in range(5):
                    if clipboard.clear():
                        break
                    time.sleep(.05)
            finally:
                clipboard.close()


def stop_services(services, tree):
    if not services:
        return
    print("Stopping RepoSherlock development services...", flush=True)
    for process, _thread in services.values():
        if process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                # The job object is the fallback when no console is attached.
                pass
    deadline = time.monotonic() + STOP_SECONDS
    try:
        while tree.has_children() and time.monotonic() < deadline:
            time.sleep(.1)
        if tree.has_children():
            print("Closing remaining development processes.", flush=True)
        else:
            for process, thread in services.values():
                process.wait()
                thread.join(timeout=.25)
            print("Development services stopped.", flush=True)
    except OSError:
        print("Closing remaining development processes.", flush=True)


def main():
    # Windows PowerShell redirects native output using a legacy code page on
    # some hosts. Keep draining child logs when their banners contain Unicode.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    stop = threading.Event()
    for event in (signal.SIGINT, signal.SIGBREAK):
        signal.signal(event, lambda *_: stop.set())
    token = secrets.token_urlsafe(32)
    pairing = secrets.token_urlsafe(32)
    source = dict(os.environ)
    known = {value for key, value in source.items() if key.upper() in PRIVATE_NAMES and value}
    for key in PROVIDER_KEYS:
        os.environ.pop(key, None)
    private_values = tuple(sorted(known | {token, pairing}, key=len, reverse=True))
    services = {}
    tree = None
    parent = None
    try:
        backend, frontend = split_environment(source, token, pairing)
        check_ports()
        check_frontend_secrets(frontend, private_values)
        node = shutil.which("node.exe")
        frontend_probe = {key: value for key, value in frontend.items() if key not in {SESSION, PAIRING}}
        next_cli = check_dependencies(node, frontend_probe, bool(backend.get("GEMINI_API_KEY", "").strip()))
        check_settings(backend)
        try:
            parent = ParentProcess(int(sys.argv[1]))
            tree = ProcessTree()
        except OSError:
            raise StartupError("Windows could not create a process group for safe cleanup. Run the launcher in a local PowerShell console.") from None
        if stop.is_set() or not parent.running():
            return 0
        print("Starting local development services...", flush=True)
        services["FastAPI"] = start_service(
            "FastAPI", [sys.executable, "-m", "uvicorn", "reposherlock.main:app", "--host", HOST,
                        "--port", str(API_PORT), "--workers", "1", "--no-proxy-headers", "--no-access-log",
                        "--timeout-graceful-shutdown", "5"],
            BACKEND, backend, private_values,
        )
        if stop.is_set() or not parent.running():
            return 0
        # Next.js propagates execArgv to its worker. Keep the launcher-only
        # inline signal handler in this process rather than the worker options.
        launch_next = 'process.execArgv.length = 0; process.on("SIGBREAK", () => process.emit("SIGINT")); require(process.argv[1]);'
        services["Next.js"] = start_service(
            "Next.js", [node, "-e", launch_next, str(next_cli), "dev", "--hostname", HOST, "--port", str(WEB_PORT)],
            FRONTEND, frontend, private_values,
        )
        if await_readiness(services, token, stop, parent):
            watch(services, stop, pairing, parent)
        return 0
    except StartupError as error:
        print(redact(str(error), private_values), file=sys.stderr, flush=True)
        return 1
    except (IndexError, OSError, ValueError):
        print("The launcher could not read its configuration or complete a local operation.", file=sys.stderr, flush=True)
        return 1
    finally:
        if tree is not None:
            stop_services(services, tree)
        if parent is not None:
            parent.close()


if __name__ == "__main__":
    raise SystemExit(main())
