#!/usr/bin/env python3
"""Porównuje geometrię kolumn i rozkład problemów strona po stronie.

Read-only. Nie wypisuje żadnych wartości z dokumentu - wyłącznie współrzędne,
liczniki i długości tekstu, żeby raport można było bezpiecznie wkleić.

Użycie:
    python3 scripts/diagnose_pages.py                # wszystkie dokumenty
    python3 scripts/diagnose_pages.py --doc 1812     # fragment nazwy pliku
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

COLUMNS_OF_INTEREST = ("row_number", "business_date", "evidence_number")


def default_db_path() -> Path:
    override = os.environ.get("KPIR_DATA_DIR")
    if override:
        return Path(override).expanduser() / "app.db"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "kpir-converter" / "app.db"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kpir-converter" / "app.db"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "kpir-converter" / "app.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--doc", default=None, help="fragment nazwy pliku")
    parser.add_argument("--max-pages", type=int, default=4)
    args = parser.parse_args()

    db_path = args.db or default_db_path()
    if not db_path.exists():
        print(f"Nie znaleziono bazy: {db_path}")
        return 1
    print(f"baza: {db_path}\n")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    docs = conn.execute(
        "SELECT id, original_name FROM documents WHERE deleted_at IS NULL ORDER BY original_name"
    ).fetchall()
    if args.doc:
        docs = [d for d in docs if args.doc.lower() in (d["original_name"] or "").lower()]

    for doc in docs:
        print(f"=== {doc['original_name']} ===")

        rows = conn.execute(
            """
            SELECT r.source_page_from AS page, COUNT(*) AS n
            FROM records r WHERE r.document_id = ?
            GROUP BY page ORDER BY page
            """,
            (doc["id"],),
        ).fetchall()
        print("  Rekordy na stronę: " + ", ".join(f"s{r['page']}={r['n']}" for r in rows))

        # Problemy w rozbiciu na strony - pokazuje, czy defekt jest lokalny.
        probs = conn.execute(
            """
            SELECT i.page_number AS page, i.code, COUNT(*) AS n
            FROM issues i
            WHERE i.document_id = ? AND i.resolved_at IS NULL
            GROUP BY page, i.code ORDER BY page, n DESC
            """,
            (doc["id"],),
        ).fetchall()
        if probs:
            print("  Problemy wg strony:")
            current = None
            for p in probs:
                if p["page"] != current:
                    current = p["page"]
                    print(f"    strona {current}:")
                print(f"        {p['n']:>5}  {p['code']}")

        # Granice kolumn odtworzone z bboxów - klucz do wykrycia przesunięcia.
        print(f"  Geometria kolumn (min x0 / max x1) dla pierwszych {args.max_pages} stron:")
        for key in COLUMNS_OF_INTEREST:
            parts = []
            for page in range(1, args.max_pages + 1):
                geo = conn.execute(
                    """
                    SELECT MIN(c.bbox_x0) AS x0, MAX(c.bbox_x1) AS x1,
                           COUNT(*) AS n, SUM(LENGTH(c.raw_text)) AS chars
                    FROM cells c JOIN records r ON r.id = c.record_id
                    WHERE r.document_id = ? AND c.source_page = ? AND c.column_key = ?
                      AND LENGTH(c.raw_text) > 0
                    """,
                    (doc["id"], page, key),
                ).fetchone()
                if geo and geo["n"]:
                    avg = (geo["chars"] or 0) / geo["n"]
                    parts.append(
                        f"s{page}: {geo['x0']:.4f}-{geo['x1']:.4f} (n={geo['n']}, śr.dł={avg:.1f})"
                    )
                else:
                    parts.append(f"s{page}: brak")
            print(f"    {key:<16} " + " | ".join(parts))

        # Ile wierszy ma pustą kolumnę, w rozbiciu na strony.
        print("  Puste komórki wg strony:")
        for key in COLUMNS_OF_INTEREST:
            parts = []
            for page in range(1, args.max_pages + 1):
                got = conn.execute(
                    """
                    SELECT SUM(CASE WHEN LENGTH(c.raw_text) = 0 THEN 1 ELSE 0 END) AS empty,
                           COUNT(*) AS total
                    FROM cells c JOIN records r ON r.id = c.record_id
                    WHERE r.document_id = ? AND c.source_page = ? AND c.column_key = ?
                    """,
                    (doc["id"], page, key),
                ).fetchone()
                if got and got["total"]:
                    parts.append(f"s{page}: {got['empty']}/{got['total']}")
                else:
                    parts.append(f"s{page}: -")
            print(f"    {key:<16} " + " | ".join(parts))
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
