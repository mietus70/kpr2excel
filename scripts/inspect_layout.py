#!/usr/bin/env python3
"""Zrzut geometrii strony PDF na potrzeby kalibracji profilu KPiR.

Narzędzie z Etapu 0 (ARCHITECTURE.md §17): pozwala zobaczyć tokeny, linie
tabeli i wykryte granice kolumn, zanim powstanie profil dla nowego wariantu
formularza.

PRYWATNOŚĆ: domyślnie NIE wypisuje treści księgowej. Pokazuje wyłącznie
współrzędne, liczbę znaków i klasę tekstu (liczba/data/kwota/tekst). Dzięki temu
wynik można bezpiecznie wkleić w zgłoszeniu błędu. Pełny tekst pokazuje dopiero
flaga --show-text.

Przykłady:
    python scripts/inspect_layout.py plik.pdf                 # strona 1, tryb bezpieczny
    python scripts/inspect_layout.py plik.pdf --page 2
    python scripts/inspect_layout.py plik.pdf --show-text     # z treścią (dane wrażliwe!)
    python scripts/inspect_layout.py plik.pdf --suggest-profile > profiles/moj.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from kpir_converter.application.extraction import (  # noqa: E402
    detect_column_layout,
    detect_table_zone,
    match_profile,
)
from kpir_converter.domain.geometry import cluster_values, group_tokens_into_lines  # noqa: E402
from kpir_converter.infrastructure.pdf.adapter import PdfDocumentAdapter  # noqa: E402
from kpir_converter.infrastructure.storage.profiles_loader import load_registry  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_DATE_RE = re.compile(r"^\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")
_MONEY_RE = re.compile(r"^-?[\d\s\u00a0.,]+$")
_INT_RE = re.compile(r"^\d{1,6}[.)]?$")


def classify(text: str) -> str:
    """Klasa tekstu bez ujawniania jego treści."""
    stripped = text.strip()
    if not stripped:
        return "pusty"
    if _INT_RE.match(stripped):
        return "int"
    if _DATE_RE.match(stripped):
        return "data"
    if _MONEY_RE.match(stripped) and any(c.isdigit() for c in stripped):
        return "kwota"
    if any(c.isdigit() for c in stripped) and any(c.isalpha() for c in stripped):
        return "alfanum"
    return "tekst"


def render(text: str, show_text: bool, width: int = 22) -> str:
    if show_text:
        clipped = text if len(text) <= width else text[: width - 1] + "…"
        return f"{clipped!r}"
    return f"<{classify(text)}:{len(text)}>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Podgląd geometrii strony PDF.")
    parser.add_argument("pdf", type=Path, help="ścieżka do pliku PDF")
    parser.add_argument("--page", type=int, default=1, help="numer strony (1-based)")
    parser.add_argument(
        "--show-text", action="store_true", help="pokaż treść tokenów (DANE WRAŻLIWE)"
    )
    parser.add_argument(
        "--max-lines", type=int, default=14, help="ile linii tekstu wypisać (domyślnie 14)"
    )
    parser.add_argument(
        "--suggest-profile",
        action="store_true",
        help="wypisz szkic profilu YAML na podstawie wykrytych linii pionowych",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Nie znaleziono pliku: {args.pdf}", file=sys.stderr)
        return 1

    with PdfDocumentAdapter(args.pdf) as pdf:
        if args.page < 1 or args.page > pdf.page_count:
            print(f"Strona {args.page} poza zakresem (1..{pdf.page_count})", file=sys.stderr)
            return 1

        content = pdf.page_content(args.page)
        geometry = content.geometry

        if args.suggest_profile:
            return _suggest(content)

        print(f"Plik:    {args.pdf.name}")
        print(f"Stron:   {pdf.page_count}")
        print(f"Strona:  {args.page}")
        print(
            f"Rozmiar: {geometry.width:.0f} x {geometry.height:.0f} pt"
            f"  (rotacja {geometry.rotation}°)"
        )
        print(f"Tokenów: {len(content.tokens)}")
        if not content.has_text:
            print("\nUWAGA: brak warstwy tekstowej — to skan, MVP nie wykonuje OCR.")
            return 1

        # ----------------------------------------------------------- linie
        v_clusters = [
            sum(c) / len(c) for c in cluster_values(content.vertical_lines, tolerance=0.006)
        ]
        h_clusters = [
            sum(c) / len(c) for c in cluster_values(content.horizontal_lines, tolerance=0.004)
        ]
        print(f"\nLinie pionowe (separatory kolumn): {len(v_clusters)}")
        if v_clusters:
            print("  " + "  ".join(f"{x:.4f}" for x in v_clusters))
            print(f"  → sugeruje {max(0, len(v_clusters) - 1)} kolumn")
        print(f"Linie poziome (separatory wierszy): {len(h_clusters)}")
        if h_clusters:
            preview = h_clusters[:12]
            print(
                "  "
                + "  ".join(f"{y:.4f}" for y in preview)
                + ("  …" if len(h_clusters) > 12 else "")
            )

        # -------------------------------------------------- dopasowanie profilu
        registry = load_registry(REPO / "profiles")
        page_text = " ".join(t.raw_text for t in content.tokens)
        match, diagnostics = match_profile(registry.all(), [page_text])
        print("\nDopasowanie profilu:")
        if match:
            print(f"  {match.profile.qualified_id}  wynik={match.score:.4f}")
            print(f"  składowe: {match.components}")
        else:
            print(f"  BRAK dopasowania — {diagnostics}")

        profile = match.profile if match else registry.get("kpir_pl_2018")
        layout = detect_column_layout(profile, content.tokens, content.vertical_lines)
        print(f"\nWykryte granice kolumn (źródło: {layout.source}, pewność {layout.confidence}):")
        for key, x0, x1 in layout.boundaries:
            print(f"  {key:<28} {x0:.4f} .. {x1:.4f}")
        if layout.diagnostics:
            print("  diagnostyka: " + "; ".join(layout.diagnostics))

        # ------------------------------------------------------------ strefy
        lines = group_tokens_into_lines(content.tokens)
        header_bottom, footer_top = detect_table_zone(profile, lines)
        print(
            f"\nStrefa tabeli: nagłówek kończy się na y={header_bottom:.4f},"
            f" stopka zaczyna się na y={footer_top:.4f}"
        )

        # ------------------------------------------------------------- linie
        print(f"\nPierwsze linie tekstu (do {args.max_lines}):")
        if not args.show_text:
            print("  (treść ukryta — użyj --show-text)")
        for line in lines[: args.max_lines]:
            marker = ""
            if line.baseline <= header_bottom:
                marker = " [nagłówek]"
            elif line.top >= footer_top:
                marker = " [stopka]"
            cells = "  ".join(
                f"{t.bbox.center_x:.3f}:{render(t.raw_text, args.show_text, 14)}"
                for t in line.tokens[:9]
            )
            more = f"  (+{len(line.tokens) - 9})" if len(line.tokens) > 9 else ""
            print(f"  y={line.baseline:.4f}{marker}")
            print(f"    {cells}{more}")

    print("\nCo wysłać przy zgłoszeniu problemu z układem:")
    print("  - sekcję 'Linie pionowe' (liczba kolumn),")
    print("  - sekcję 'Dopasowanie profilu',")
    print("  - kilka linii z sekcji 'Pierwsze linie tekstu' (bez --show-text).")
    return 0


def _suggest(content) -> int:  # type: ignore[no-untyped-def]
    """Szkic profilu na podstawie linii pionowych — do ręcznego uzupełnienia."""
    clusters = [sum(c) / len(c) for c in cluster_values(content.vertical_lines, tolerance=0.006)]
    if len(clusters) < 2:
        print("# Za mało linii pionowych, aby zaproponować granice kolumn.", file=sys.stderr)
        return 1
    count = len(clusters) - 1
    print("# Szkic wygenerowany przez scripts/inspect_layout.py")
    print("# UZUPEŁNIJ: label_pl, type, form_number, header_keywords, required.")
    print("id: kpir_wariant_lokalny")
    print("version: 1")
    print(f'display_name: "KPiR - wariant lokalny ({count} kolumn)"')
    print("columns:")
    for index in range(count):
        print(f"  - key: kolumna_{index + 1}")
        print(f'    label_pl: "Kolumna {index + 1}"')
        print("    type: text")
        print(f"    x0: {clusters[index]:.4f}")
        print(f"    x1: {clusters[index + 1]:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
