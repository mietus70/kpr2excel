"""Wspólny bootstrap: uruchom skrypt w interpreterze ze środowiska .venv.

Skrypty diagnostyczne importują kod aplikacji, więc potrzebują zależności
(PyMuPDF, PyYAML). Gdy użytkownik wywoła je systemowym `python3`, zamiast
czytelnego komunikatu dostawał `ModuleNotFoundError: No module named 'fitz'`.

`ensure_venv()` przełącza proces na `.venv`, jeśli tylko takie środowisko
istnieje i nie jest już aktywne. Działa na Windows/macOS/Linux i nie wykonuje
żadnych żądań sieciowych ani instalacji - wyłącznie zmiana interpretera.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def venv_python() -> Path | None:
    """Ścieżka do interpretera w .venv, jeśli istnieje."""
    candidates = (
        REPO / ".venv" / "bin" / "python3",
        REPO / ".venv" / "bin" / "python",
        REPO / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ensure_venv() -> None:
    """Przełącz się na interpreter z .venv, jeśli obecny go nie ma."""
    if os.environ.get("KPIR_VENV_REEXEC") == "1":
        return
    try:
        import fitz  # noqa: F401

        return
    except ImportError:
        pass

    interpreter = venv_python()
    # Uwaga: .venv/bin/python3 bywa dowiązaniem do /usr/bin/python3, więc
    # porównanie ścieżek po resolve() dałoby fałszywą równość. O tym, czy
    # jesteśmy już w środowisku, decyduje sys.prefix.
    already_in_venv = Path(sys.prefix).resolve() == (REPO / ".venv").resolve()
    if interpreter is None or already_in_venv:
        print(
            "Brakuje zależności Pythona (PyMuPDF).\n"
            f"Zainstaluj je świadomie:  python3 {REPO / 'scripts' / 'install_deps.py'} --dev",
            file=sys.stderr,
        )
        raise SystemExit(1)

    env = dict(os.environ, KPIR_VENV_REEXEC="1")
    os.execve(str(interpreter), [str(interpreter), *sys.argv], env)
