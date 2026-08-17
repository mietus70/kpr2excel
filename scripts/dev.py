#!/usr/bin/env python3
"""Cross-platform development launcher (Windows, macOS, Linux).

Starts the API, the worker and Vite as child processes, waits for readiness,
opens the browser and cleans everything up on exit. Dependencies are checked but
never installed without consent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO / "backend" / "src"
FRONTEND = REPO / "frontend"

MIN_PYTHON = (3, 11)
MIN_NODE = 18


class Colors:
    OK = "\033[32m"
    WARN = "\033[33m"
    ERR = "\033[31m"
    DIM = "\033[2m"
    END = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        cls.OK = cls.WARN = cls.ERR = cls.DIM = cls.END = ""


if os.name == "nt" and not os.environ.get("ANSICON"):
    try:
        import colorama  # type: ignore

        colorama.just_fix_windows_console()
    except Exception:  # noqa: BLE001
        Colors.disable()


def info(message: str) -> None:
    print(f"{Colors.DIM}[dev]{Colors.END} {message}", flush=True)


def ok(message: str) -> None:
    print(f"{Colors.OK}[ok]{Colors.END} {message}", flush=True)


def warn(message: str) -> None:
    print(f"{Colors.WARN}[uwaga]{Colors.END} {message}", flush=True)


def fail(message: str) -> None:
    print(f"{Colors.ERR}[błąd]{Colors.END} {message}", file=sys.stderr, flush=True)


def free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


def check_python() -> bool:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"wymagany Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, wykryto {sys.version.split()[0]}"
        )
        return False
    ok(f"Python {sys.version.split()[0]}")
    return True


def check_node() -> str | None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    node = shutil.which("node")
    if not node or not npm:
        fail("nie znaleziono Node.js/npm w PATH")
        return None
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        major = int(version.lstrip("v").split(".")[0])
        if major < MIN_NODE:
            fail(f"wymagany Node >= {MIN_NODE}, wykryto {version}")
            return None
        ok(f"Node {version}")
    except Exception as exc:  # noqa: BLE001
        fail(f"nie można sprawdzić wersji Node: {exc}")
        return None
    return npm


def check_python_deps() -> bool:
    missing = []
    for module in ("fastapi", "uvicorn", "fitz", "openpyxl", "pydantic", "yaml"):
        try:
            __import__(module)
        except ImportError:
            missing.append({"fitz": "pymupdf", "yaml": "pyyaml"}.get(module, module))
    if missing:
        fail("brakuje zależności Pythona: " + ", ".join(missing))
        info("zainstaluj je świadomie, np.:")
        info(f"  {sys.executable} -m pip install -e '.[dev]'")
        return False
    ok("zależności Pythona obecne")
    return True


def check_node_deps(npm: str) -> bool:
    if (FRONTEND / "node_modules").exists():
        ok("zależności frontendu obecne")
        return True
    fail("brak katalogu frontend/node_modules")
    info(f"zainstaluj je świadomie:  cd frontend && {Path(npm).name} install")
    return False


def wait_for_http(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def stream_output(process: subprocess.Popen, label: str) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"{Colors.DIM}[{label}]{Colors.END} {line.rstrip()}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Uruchamia lokalne środowisko deweloperskie.")
    parser.add_argument("--api-port", type=int, default=int(os.environ.get("KPIR_API_PORT", 8756)))
    parser.add_argument(
        "--frontend-port", type=int, default=int(os.environ.get("KPIR_FRONTEND_PORT", 5173))
    )
    parser.add_argument("--no-browser", action="store_true", help="nie otwieraj przeglądarki")
    parser.add_argument("--no-worker", action="store_true", help="nie uruchamiaj workera")
    parser.add_argument(
        "--host",
        default=os.environ.get("KPIR_API_HOST", "127.0.0.1"),
        help="adres nasłuchu API (domyślnie loopback)",
    )
    args = parser.parse_args()

    print()
    info("Konwerter KPiR — środowisko deweloperskie")
    if not check_python():
        return 1
    npm = check_node()
    if npm is None:
        return 1
    if not check_python_deps():
        return 1
    if not check_node_deps(npm):
        return 1

    api_port = free_port(args.api_port)
    frontend_port = free_port(args.frontend_port)
    if api_port != args.api_port:
        warn(f"port {args.api_port} zajęty, API użyje {api_port}")
    if frontend_port != args.frontend_port:
        warn(f"port {args.frontend_port} zajęty, UI użyje {frontend_port}")

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_SRC), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["KPIR_API_PORT"] = str(api_port)
    env["KPIR_FRONTEND_PORT"] = str(frontend_port)
    env["KPIR_API_HOST"] = args.host
    env["KPIR_DEV_MODE"] = "1"

    # Migrations run before anything else touches the database.
    info("uruchamiam migracje bazy…")
    migrate = subprocess.run(
        [
            sys.executable,
            "-c",
            "from kpir_converter.worker.runner import build_context;"
            " build_context(); print('migrations ok')",
        ],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if migrate.returncode != 0:
        fail("migracje nie powiodły się:\n" + (migrate.stderr or migrate.stdout))
        return 1
    ok("baza gotowa")

    processes: list[tuple[str, subprocess.Popen]] = []

    def spawn(label: str, command: list[str], cwd: Path) -> subprocess.Popen:
        creation = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creation,
            start_new_session=(os.name != "nt"),
        )
        processes.append((label, process))
        threading.Thread(target=stream_output, args=(process, label), daemon=True).start()
        return process

    def shutdown(*_args: object) -> None:
        print()
        info("zatrzymuję procesy…")
        for label, process in reversed(processes):
            if process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                warn(f"{label} nie zakończył się, wymuszam")
                process.kill()
        ok("zatrzymano")

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(0)))

    try:
        info(f"start API na http://{args.host}:{api_port}")
        spawn(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "kpir_converter.main:create_app",
                "--factory",
                "--host",
                args.host,
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            REPO,
        )
        if not wait_for_http(f"http://127.0.0.1:{api_port}/api/v1/health", timeout=60):
            fail("API nie odpowiedziało w wyznaczonym czasie")
            shutdown()
            return 1
        ok("API gotowe")

        if not args.no_worker:
            info("start workera")
            spawn("worker", [sys.executable, "-m", "kpir_converter.worker.runner"], REPO)
            ok("worker działa")

        info(f"start UI na http://127.0.0.1:{frontend_port}")
        spawn("vite", [npm, "run", "dev", "--", "--port", str(frontend_port)], FRONTEND)
        url = f"http://127.0.0.1:{frontend_port}"
        if not wait_for_http(url, timeout=90):
            warn("UI jeszcze się kompiluje")
        else:
            ok("UI gotowe")

        print()
        ok(f"Aplikacja: {url}")
        info(f"API:       http://127.0.0.1:{api_port}/api/v1/docs")
        info("Zatrzymanie: Ctrl+C")
        print()

        if not args.no_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()

        while True:
            for label, process in processes:
                code = process.poll()
                if code is not None:
                    fail(f"proces '{label}' zakończył się z kodem {code}")
                    shutdown()
                    return 1
            time.sleep(0.7)
    except KeyboardInterrupt:
        shutdown()
        return 0


if __name__ == "__main__":
    sys.exit(main())
