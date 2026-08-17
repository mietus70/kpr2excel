#!/usr/bin/env python3
"""Instalator zależności Pythona (FastAPI, Uvicorn, PyMuPDF, OpenPyXL, Pydantic...).

Pobranie zależności jest osobnym etapem konfiguracji, a NIE działaniem aplikacji
(ARCHITECTURE.md §13.3). Sama aplikacja nigdy nie łączy się z siecią — ten skrypt
jest jedynym miejscem, które tego wymaga, i tylko przy pierwszej konfiguracji.

Lista pakietów pochodzi z `pyproject.toml`, żeby wersje nie rozjechały się
z projektem. Skrypt nie instaluje niczego bez zgody: domyślnie pokazuje plan
i pyta o potwierdzenie.

Przykłady:
    python scripts/install_deps.py                 # pokaż plan i zapytaj
    python scripts/install_deps.py --yes           # bez pytania
    python scripts/install_deps.py --dev           # razem z narzędziami testowymi
    python scripts/install_deps.py --create-venv   # utwórz .venv i zainstaluj tam
    python scripts/install_deps.py --check         # tylko sprawdź, nic nie instaluj
    python scripts/install_deps.py --wheelhouse ./wheels --offline   # instalacja offline
    python scripts/install_deps.py --build-wheelhouse ./wheels       # przygotuj paczkę offline
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
VENV_DIR = REPO / ".venv"

MIN_PYTHON = (3, 11)

# Nazwa dystrybucji -> moduł, który faktycznie importujemy w kodzie.
# Bez tego nie da się sprawdzić obecności pakietu (pymupdf -> fitz itd.).
IMPORT_NAMES = {
    "pymupdf": "fitz",
    "pyyaml": "yaml",
    "python-multipart": "multipart",
    "uvicorn[standard]": "uvicorn",
    "pytest-cov": "pytest_cov",
}


class Colors:
    OK = "\033[32m"
    WARN = "\033[33m"
    ERR = "\033[31m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    END = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        cls.OK = cls.WARN = cls.ERR = cls.DIM = cls.BOLD = cls.END = ""


if os.name == "nt" and not os.environ.get("ANSICON"):
    try:
        import colorama  # type: ignore

        colorama.just_fix_windows_console()
    except Exception:  # noqa: BLE001
        Colors.disable()
if not sys.stdout.isatty():
    Colors.disable()


def info(message: str) -> None:
    print(f"{Colors.DIM}[info]{Colors.END} {message}", flush=True)


def ok(message: str) -> None:
    print(f"{Colors.OK}[ok]{Colors.END} {message}", flush=True)


def warn(message: str) -> None:
    print(f"{Colors.WARN}[uwaga]{Colors.END} {message}", flush=True)


def fail(message: str) -> None:
    print(f"{Colors.ERR}[błąd]{Colors.END} {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------
# Odczyt zależności z pyproject.toml (jedno źródło prawdy)
# --------------------------------------------------------------------------------------


def read_dependencies(include_dev: bool) -> tuple[list[str], list[str]]:
    """Zwraca (runtime, dev) jako listy specyfikacji pakietów."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - obsłużone przez check_python
        fail("wymagany Python 3.11+ (brak modułu tomllib)")
        raise SystemExit(1) from None

    if not PYPROJECT.exists():
        fail(f"nie znaleziono {PYPROJECT}")
        raise SystemExit(1)

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    runtime = list(project.get("dependencies", []))
    dev: list[str] = []
    if include_dev:
        dev = list(project.get("optional-dependencies", {}).get("dev", []))
    if not runtime:
        fail("pyproject.toml nie definiuje żadnych zależności runtime")
        raise SystemExit(1)
    return runtime, dev


def base_name(spec: str) -> str:
    """'uvicorn[standard]>=0.27' -> 'uvicorn'."""
    for separator in (">=", "<=", "==", "!=", "~=", ">", "<", ";"):
        spec = spec.split(separator)[0]
    return spec.split("[")[0].strip().lower()


def extras_name(spec: str) -> str:
    """'uvicorn[standard]>=0.27' -> 'uvicorn[standard]' (do mapy IMPORT_NAMES)."""
    for separator in (">=", "<=", "==", "!=", "~=", ">", "<", ";"):
        spec = spec.split(separator)[0]
    return spec.strip().lower()


# --------------------------------------------------------------------------------------
# Kontrola środowiska
# --------------------------------------------------------------------------------------


def check_python() -> bool:
    version = ".".join(str(v) for v in sys.version_info[:3])
    if sys.version_info < MIN_PYTHON:
        fail(f"wymagany Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, wykryto {version}")
        return False
    ok(f"Python {version} ({sys.executable})")
    return True


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def is_externally_managed() -> bool:
    """Debian/Ubuntu blokują instalację do systemowego Pythona (PEP 668)."""
    stdlib = sysconfig.get_paths().get("stdlib")
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").exists()


def installed_version(spec: str) -> str | None:
    """Zwraca zainstalowaną wersję albo None."""
    try:
        return metadata.version(base_name(spec))
    except metadata.PackageNotFoundError:
        return None


def importable(spec: str) -> bool:
    """Sprawdza, czy moduł faktycznie da się zaimportować."""
    from importlib.util import find_spec

    module = IMPORT_NAMES.get(extras_name(spec), base_name(spec).replace("-", "_"))
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def report(specs: list[str], label: str) -> list[str]:
    """Wypisuje stan pakietów i zwraca listę brakujących."""
    print(f"\n{Colors.BOLD}{label}{Colors.END}")
    missing: list[str] = []
    for spec in specs:
        version = installed_version(spec)
        if version is not None:
            print(f"  {Colors.OK}✓{Colors.END} {spec:<28} zainstalowano {version}")
        else:
            print(f"  {Colors.WARN}✗{Colors.END} {spec:<28} {Colors.DIM}brak{Colors.END}")
            missing.append(spec)
    return missing


# --------------------------------------------------------------------------------------
# Instalacja
# --------------------------------------------------------------------------------------


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def create_venv(venv: Path) -> Path | None:
    if venv.exists():
        ok(f"środowisko wirtualne już istnieje: {venv}")
        return venv_python(venv)
    info(f"tworzę środowisko wirtualne: {venv}")
    result = subprocess.run([sys.executable, "-m", "venv", str(venv)])
    if result.returncode != 0:
        fail("nie udało się utworzyć środowiska wirtualnego")
        if os.name != "nt":
            info("na Debianie/Ubuntu może brakować pakietu: sudo apt install python3-venv")
        return None
    ok("utworzono środowisko wirtualne")
    return venv_python(venv)


def pip_install(
    python: Path | str,
    specs: list[str],
    *,
    wheelhouse: Path | None,
    offline: bool,
    upgrade_pip: bool,
) -> bool:
    if upgrade_pip and not offline:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            check=False,
        )

    command = [str(python), "-m", "pip", "install"]
    if offline:
        command += ["--no-index"]
    if wheelhouse:
        command += ["--find-links", str(wheelhouse)]
    command += specs

    info("uruchamiam: " + " ".join(command))
    return subprocess.run(command).returncode == 0


def build_wheelhouse(specs: list[str], target: Path) -> bool:
    """Pobiera koła do katalogu, aby umożliwić późniejszą instalację offline."""
    target.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pip", "download", "--dest", str(target), *specs]
    info("uruchamiam: " + " ".join(command))
    if subprocess.run(command).returncode != 0:
        return False
    count = len(list(target.glob("*")))
    ok(f"pobrano {count} plików do {target}")
    info("instalacja offline na innej maszynie:")
    info(f"  python scripts/install_deps.py --wheelhouse {target} --offline --yes")
    return True


def verify(specs: list[str], python: Path | str) -> bool:
    """Weryfikuje instalację realnym importem w docelowym interpreterze."""
    modules = [IMPORT_NAMES.get(extras_name(s), base_name(s).replace("-", "_")) for s in specs]
    code = (
        "import importlib, sys\n"
        f"mods = {modules!r}\n"
        "bad = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as exc:\n"
        "        bad.append(f'{m}: {type(exc).__name__}')\n"
        "print('MISSING:' + ','.join(bad) if bad else 'ALL_OK')\n"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True)
    output = (result.stdout or "").strip()
    if output.endswith("ALL_OK"):
        return True
    fail(f"weryfikacja importów nie powiodła się: {output or result.stderr.strip()}")
    return False


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Instaluje zależności Pythona dla konwertera KPiR.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--yes", "-y", action="store_true", help="nie pytaj o potwierdzenie")
    parser.add_argument("--dev", action="store_true", help="dołącz zależności deweloperskie")
    parser.add_argument("--check", action="store_true", help="tylko sprawdź stan, nie instaluj")
    parser.add_argument(
        "--create-venv", action="store_true", help=f"utwórz {VENV_DIR.name} i zainstaluj tam"
    )
    parser.add_argument(
        "--wheelhouse", type=Path, default=None, help="katalog z gotowymi kołami (.whl)"
    )
    parser.add_argument(
        "--offline", action="store_true", help="nie korzystaj z sieci (wymaga --wheelhouse)"
    )
    parser.add_argument(
        "--build-wheelhouse",
        type=Path,
        default=None,
        metavar="KATALOG",
        help="pobierz koła do katalogu i zakończ (przygotowanie instalacji offline)",
    )
    args = parser.parse_args()

    print()
    print(f"{Colors.BOLD}Konwerter KPiR — instalacja zależności{Colors.END}")

    if not check_python():
        return 1

    runtime, dev = read_dependencies(include_dev=args.dev)
    specs = runtime + dev

    # --------------------------------------------------------------- wheelhouse
    if args.build_wheelhouse:
        info(f"przygotowuję paczkę offline dla {len(specs)} pakietów")
        return 0 if build_wheelhouse(specs, args.build_wheelhouse) else 1

    if args.offline and not args.wheelhouse:
        fail("--offline wymaga podania --wheelhouse z katalogiem kół")
        return 1

    # ------------------------------------------------------------------- status
    missing_runtime = report(runtime, "Zależności aplikacji (runtime)")
    missing_dev = report(dev, "Zależności deweloperskie") if dev else []
    missing = missing_runtime + missing_dev

    # Pakiet zainstalowany, ale nieimportowalny to uszkodzona instalacja.
    broken = [s for s in specs if installed_version(s) is not None and not importable(s)]
    if broken:
        warn("pakiety zainstalowane, ale nieimportowalne: " + ", ".join(broken))
        missing = list(dict.fromkeys(missing + broken))

    print()
    if not missing:
        ok("wszystkie zależności są już zainstalowane")
        info("uruchomienie aplikacji:  python scripts/dev.py")
        return 0

    if args.check:
        warn(f"brakuje {len(missing)} pakietów")
        info("instalacja:  python scripts/install_deps.py --dev")
        return 1

    # ------------------------------------------------------------------ gdzie
    target_python: Path | str = sys.executable
    if args.create_venv:
        created = create_venv(VENV_DIR)
        if created is None:
            return 1
        target_python = created
    elif not in_virtualenv():
        warn("nie wykryto aktywnego środowiska wirtualnego")
        if is_externally_managed():
            # Debian/Ubuntu: pip odmówi instalacji do systemowego Pythona (PEP 668).
            fail(
                "ten Python jest zarządzany przez system (PEP 668)"
                " — instalacja globalna zablokowana"
            )
            info("użyj środowiska wirtualnego:")
            info("  python scripts/install_deps.py --create-venv --dev")
            return 1
        info("zalecane jest środowisko wirtualne (--create-venv)")

    print()
    print(f"{Colors.BOLD}Plan instalacji{Colors.END}")
    print(f"  interpreter: {target_python}")
    print(f"  źródło:      {'wheelhouse (offline)' if args.offline else 'PyPI (wymaga sieci)'}")
    if args.wheelhouse:
        print(f"  wheelhouse:  {args.wheelhouse}")
    print(f"  pakiety:     {len(missing)}")
    for spec in missing:
        print(f"    - {spec}")

    if not args.offline:
        print()
        warn("ten krok wymaga internetu; sama aplikacja działa później w pełni offline")

    if not args.yes:
        print()
        try:
            answer = input("Kontynuować instalację? [tak/NIE]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            info("przerwano")
            return 1
        if answer not in ("tak", "t", "yes", "y"):
            info("przerwano — nic nie zainstalowano")
            return 1

    print()
    if not pip_install(
        target_python,
        missing,
        wheelhouse=args.wheelhouse,
        offline=args.offline,
        upgrade_pip=True,
    ):
        fail("instalacja nie powiodła się")
        if not args.offline:
            info("sprawdź połączenie sieciowe lub ustawienia proxy")
        return 1

    print()
    info("weryfikuję instalację…")
    if not verify(runtime, target_python):
        return 1
    ok("wszystkie moduły aplikacji importują się poprawnie")

    print()
    ok("gotowe")
    if args.create_venv:
        activate = ".venv\\Scripts\\activate" if os.name == "nt" else "source .venv/bin/activate"
        info(f"aktywuj środowisko:  {activate}")
    info("następny krok (frontend):  cd frontend && npm install")
    info("uruchomienie:              python scripts/dev.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
