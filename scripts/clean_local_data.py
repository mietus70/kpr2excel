#!/usr/bin/env python3
"""Removes the local data directory: PDFs, renders, database and exports."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from kpir_converter.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Usuwa lokalne dane aplikacji.")
    parser.add_argument("--yes", action="store_true", help="nie pytaj o potwierdzenie")
    args = parser.parse_args()

    settings = get_settings()
    target = settings.data_dir
    if not target.exists():
        print(f"Katalog danych nie istnieje: {target}")
        return 0

    print(f"Zostanie usunięty katalog: {target}")
    print("Obejmuje to oryginalne PDF-y, bazę, korekty i eksporty.")
    if not args.yes:
        answer = input("Kontynuować? [tak/NIE]: ").strip().lower()
        if answer not in ("tak", "t", "yes", "y"):
            print("Przerwano.")
            return 1

    shutil.rmtree(target, ignore_errors=True)
    print("Usunięto dane lokalne.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
