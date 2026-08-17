#!/usr/bin/env python3
"""Runs the whole quality gate: backend tests, typecheck and frontend build."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(label: str, command: list[str], cwd: Path, env: dict | None = None) -> bool:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode != 0:
        print(f"[FAIL] {label}", file=sys.stderr)
        return False
    print(f"[OK] {label}")
    return True


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "backend" / "src")
    # Tests must be deterministic and must never reach the network.
    env["KPIR_DATA_DIR"] = str(REPO / ".pytest-data")

    steps: list[tuple[str, list[str], Path]] = [
        ("backend tests", [sys.executable, "-m", "pytest", "backend/tests", "-q"], REPO),
    ]

    ok = True
    for label, command, cwd in steps:
        ok = run(label, command, cwd, env) and ok

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm and (REPO / "frontend" / "node_modules").exists():
        ok = run("frontend typecheck", [npm, "run", "typecheck"], REPO / "frontend", env) and ok
        ok = run("frontend build", [npm, "run", "build"], REPO / "frontend", env) and ok
    else:
        print("\n[skip] frontend: brak node_modules")

    print("\n" + ("WSZYSTKO OK" if ok else "SĄ BŁĘDY"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
