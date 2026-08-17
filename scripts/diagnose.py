#!/usr/bin/env python3
"""Diagnostyka jakości ekstrakcji dla zaimportowanych dokumentów.

Odpowiada na pytanie „dlaczego eksport jest zablokowany": pokazuje problemy
pogrupowane według kodu, kolumny najczęściej dotknięte błędem oraz przykładowe
wiersze. Niczego nie zmienia w danych.

Skrypt czyta wyłącznie lokalną bazę aplikacji i nie wysyła nic na zewnątrz.
Domyślnie NIE wypisuje treści księgowej — użyj --show-values, jeśli chcesz
zobaczyć wartości komórek (przydatne przy zgłaszaniu błędu na własnych danych).

Przykłady:
    python scripts/diagnose.py                     # podsumowanie wszystkich dokumentów
    python scripts/diagnose.py --document <uuid>   # jeden dokument
    python scripts/diagnose.py --code REQUIRED_VALUE_MISSING --limit 5
    python scripts/diagnose.py --show-values       # dołącz wartości komórek
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from kpir_converter.config import get_settings  # noqa: E402
from kpir_converter.infrastructure.db.database import Database  # noqa: E402


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


if not sys.stdout.isatty():
    Colors.disable()


# Podpowiedzi: co dany kod zwykle oznacza i co z nim zrobić.
HINTS = {
    "REQUIRED_VALUE_MISSING": (
        "Brak wartości w kolumnie wymaganej (numer porządkowy, data, nr dowodu).",
        "Zwykle znaczy, że granice kolumn są przesunięte albo jako rekordy złapały się"
        " wiersze nagłówka/stopki. Sprawdź, czy 'Przykładowe wiersze' wyglądają jak dane.",
    ),
    "INVALID_DATE": (
        "Tekst w kolumnie daty nie jest datą.",
        "Częsty objaw przesunięcia kolumn albo innego formatu daty niż w profilu.",
    ),
    "INVALID_MONEY": (
        "Tekst w kolumnie kwotowej nie jest kwotą.",
        "Sprawdź, czy kolumna kwotowa nie zbiera tekstu z sąsiedniej kolumny.",
    ),
    "LOW_PROFILE_CONFIDENCE": (
        "Nie udało się pewnie dopasować układu tabeli.",
        "Najczęstsza przyczyna: dokument ma inny wariant formularza niż profil"
        " (np. inną liczbę kolumn). Profil wymaga kalibracji na tym pliku.",
    ),
    "TOKEN_CROSSES_COLUMN": (
        "Tekst przecina granicę kolumny.",
        "Granice kolumn nie pokrywają się z rzeczywistym układem strony.",
    ),
    "OCR_REQUIRED": (
        "Brak warstwy tekstowej (skan).",
        "MVP nie wykonuje OCR — takiego pliku nie da się przetworzyć.",
    ),
    "EXPENSE_TOTAL_MISMATCH": (
        "Suma wydatków nie zgadza się ze składnikami.",
        "Może być błędem ekstrakcji albo faktyczną rozbieżnością w dokumencie.",
    ),
    "INCOME_TOTAL_MISMATCH": (
        "Suma przychodów nie zgadza się ze składnikami.",
        "Może być błędem ekstrakcji albo faktyczną rozbieżnością w dokumencie.",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostyka ekstrakcji KPiR.")
    parser.add_argument("--document", help="UUID dokumentu (domyślnie: wszystkie)")
    parser.add_argument("--code", help="pokaż przykłady tylko dla tego kodu problemu")
    parser.add_argument(
        "--limit", type=int, default=3, help="ile przykładowych wierszy (domyślnie 3)"
    )
    parser.add_argument(
        "--show-values", action="store_true", help="pokaż wartości komórek (dane księgowe!)"
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.db_path.exists():
        print(f"Nie znaleziono bazy: {settings.db_path}")
        print("Czy aplikacja była uruchamiana? Sprawdź zmienną KPIR_DATA_DIR.")
        return 1

    db = Database(settings.db_path)
    print(f"{Colors.DIM}baza: {settings.db_path}{Colors.END}")

    if args.document:
        documents = db.query_all(
            "SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", (args.document,)
        )
    else:
        documents = db.query_all(
            "SELECT * FROM documents WHERE deleted_at IS NULL ORDER BY created_at"
        )
    if not documents:
        print("Brak dokumentów.")
        return 1

    for doc in documents:
        _report_document(db, doc, args)
    return 0


def _report_document(db: Database, doc, args) -> None:  # type: ignore[no-untyped-def]
    doc_id = doc["id"]
    print()
    print(f"{Colors.BOLD}=== {doc['original_name']} ==={Colors.END}")
    print(f"  id:      {doc_id}")
    print(f"  status:  {doc['status']}   strony: {doc['page_count']}")
    print(f"  profil:  {doc['detected_profile_id']}")
    print(f"  okres:   {doc['declared_period_from']} .. {doc['declared_period_to']}")

    total = db.query_scalar("SELECT COUNT(*) FROM records WHERE document_id = ?", (doc_id,))
    print(f"  rekordy: {total}")

    # --------------------------------------------------------------- problemy
    rows = db.query_all(
        "SELECT code, severity, COUNT(*) n FROM issues"
        " WHERE document_id = ? AND status = 'open' GROUP BY code, severity ORDER BY n DESC",
        (doc_id,),
    )
    if not rows:
        print(f"\n  {Colors.OK}Brak otwartych problemów.{Colors.END}")
        return

    print(f"\n  {Colors.BOLD}Problemy według kodu{Colors.END}")
    for row in rows:
        color = Colors.ERR if row["severity"] == "critical" else Colors.WARN
        print(f"    {row['n']:>6}  {color}{row['severity']:<9}{Colors.END} {row['code']}")

    # ------------------------------------------------- których kolumn dotyczą
    detail_rows = db.query_all(
        "SELECT code, details_json FROM issues WHERE document_id = ? AND status = 'open'"
        " AND details_json IS NOT NULL",
        (doc_id,),
    )
    columns: Counter[str] = Counter()
    for row in detail_rows:
        if args.code and row["code"] != args.code:
            continue
        try:
            key = json.loads(row["details_json"]).get("column_key")
        except json.JSONDecodeError:
            continue
        if key:
            columns[key] += 1
    if columns:
        print(f"\n  {Colors.BOLD}Najczęściej dotknięte kolumny{Colors.END}")
        for key, count in columns.most_common(8):
            print(f"    {count:>6}  {key}")

    # --------------------------------------------------------- co to znaczy
    top_code = args.code or rows[0]["code"]
    if top_code in HINTS:
        meaning, advice = HINTS[top_code]
        print(f"\n  {Colors.BOLD}Co oznacza {top_code}{Colors.END}")
        print(f"    {meaning}")
        print(f"    {Colors.DIM}{advice}{Colors.END}")

    # ------------------------------------------------------ przykładowe wiersze
    print(f"\n  {Colors.BOLD}Przykładowe wiersze{Colors.END}")
    if not args.show_values:
        print(f"    {Colors.DIM}(wartości ukryte — użyj --show-values){Colors.END}")

    sample = db.query_all(
        "SELECT DISTINCT r.id, r.logical_index, r.source_page_from FROM records r"
        " JOIN issues i ON i.record_id = r.id"
        " WHERE r.document_id = ? AND i.status = 'open'"
        + (" AND i.code = ?" if args.code else "")
        + " ORDER BY r.logical_index LIMIT ?",
        ((doc_id, args.code, args.limit) if args.code else (doc_id, args.limit)),
    )
    for record in sample:
        print(f"\n    wiersz #{record['logical_index']} (strona {record['source_page_from']})")
        cells = db.query_all(
            "SELECT c.column_key, c.raw_text, c.parsed_value_text, c.value_type, c.confidence"
            " FROM cells c WHERE c.record_id = ? ORDER BY c.rowid",
            (record["id"],),
        )
        for cell in cells:
            raw = cell["raw_text"] or ""
            parsed = cell["parsed_value_text"]
            if args.show_values:
                shown_raw = raw[:38] + ("…" if len(raw) > 38 else "")
                shown_parsed = "(puste)" if parsed is None else str(parsed)[:24]
            else:
                shown_raw = f"<{len(raw)} znaków>" if raw else "<pusty>"
                shown_parsed = "(puste)" if parsed is None else "<wartość>"
            unparsed = parsed is None and bool(raw)
            flag = f" {Colors.ERR}← nieprzetworzone{Colors.END}" if unparsed else ""
            line = f"      {cell['column_key']:<28} raw={shown_raw:<42} parsed={shown_parsed}"
            print(line + flag)


if __name__ == "__main__":
    sys.exit(main())
