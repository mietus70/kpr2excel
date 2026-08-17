"""Generator syntetycznego KPiR w wariancie *znakowym* (monospace).

Odtwarza strukturę raportu drukowanego przez programy typu "SYSTEM FIRMA":

- tabela rysowana znakami ``|`` i ``_``, BEZ linii wektorowych;
- wiersz numeracji kolumn ``|_1_|____2___|...|________17_______|`` renderowany
  jako jeden token, bo nie zawiera spacji;
- 17 numerowanych kolumn, przy czym kolumna 16 dzieli się na "opis"/"wartość";
- puste pole oznaczone glifem ``˙`` (U+02D9), nie myślnikiem;
- daty dwucyfrowe (``02.01.23``) wymagające kontekstu okresu;
- wiersze podsumowań "Suma folio" / "Przeniesienie z folio" / "Razem";
- stopka z nazwą programu drukującego.

Dane są w pełni fikcyjne. Wzorowane wyłącznie na STRUKTURZE dokumentu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pymupdf

__all__ = [
    "CharacterRow",
    "COLUMN_RULER",
    "DEFAULT_CHARACTER_ROWS",
    "build_character_kpir",
    "expected_character_records",
]

# Wiersz numeracji kolumn - kotwica geometrii dla całej tabeli.
COLUMN_RULER = (
    "|_1_|____2___|_______3_______|____________4___________"
    "|____________________5_________________"
    "|____________________6___________________"
    "|_____7____|_____8____|_____9____|____10____|____11____|____12____"
    "|____13____|____14____|____15____|______16______|________17_______|"
)

EMPTY_GLYPH = "\u02d9"  # ˙ DOT ABOVE


@dataclass(slots=True)
class CharacterRow:
    row_number: int
    date: str  # drukowana postać, np. "02.01.23"
    evidence: str
    contractor: str = ""
    address: str = ""
    description: str = ""
    income_goods_services: Decimal | None = None
    other_income: Decimal | None = None
    goods_materials_purchase: Decimal | None = None
    purchase_incidental_costs: Decimal | None = None
    remuneration: Decimal | None = None
    other_expenses: Decimal | None = None
    notes: str = ""

    @property
    def total_income(self) -> Decimal | None:
        parts = [p for p in (self.income_goods_services, self.other_income) if p is not None]
        return sum(parts, Decimal("0.00")) if parts else None

    @property
    def total_expenses(self) -> Decimal | None:
        # Wzór KPiR: kolumna 14 = 12 + 13 (bez kolumn 10 i 11).
        parts = [p for p in (self.remuneration, self.other_expenses) if p is not None]
        return sum(parts, Decimal("0.00")) if parts else None

    def iso_date(self, century: int = 2000) -> str:
        day, month, year = self.date.split(".")
        return f"{century + int(year)}-{int(month):02d}-{int(day):02d}"


DEFAULT_CHARACTER_ROWS: list[CharacterRow] = [
    CharacterRow(
        row_number=1,
        date="02.01.23",
        evidence="91/12/2022",
        contractor="Alfa Testowa Sp.",
        address="Miasto Testowa 2D",
        description="zaplata za wywoz odpadow",
        other_expenses=Decimal("1820.00"),
    ),
    CharacterRow(
        row_number=2,
        date="09.01.23",
        evidence="1/1/2023",
        contractor="Beta Fikcyjna",
        address="Wies Lustrzana 4",
        description="sprzedaz uslug",
        income_goods_services=Decimal("15000.00"),
    ),
    # Wiersz wewnętrzny: brak kontrahenta i adresu - typowy dla dokumentów WEW.
    CharacterRow(
        row_number=3,
        date="31.01.23",
        evidence="1 WEW",
        description="umowa zlec./o dzielo styczen",
        remuneration=Decimal("4049.70"),
    ),
    CharacterRow(
        row_number=4,
        date="13.02.23",
        evidence="22976/23/C",
        contractor="Gamma Paliwa",
        address="Osada Ogrodowa 63",
        description="paliwo",
        other_expenses=Decimal("67.27"),
        notes="(75% z 89.69)",
    ),
]


def expected_character_records(
    rows: list[CharacterRow] | None = None,
) -> list[dict[str, str | None]]:
    """Ręcznie zatwierdzone oczekiwanie dla testów golden."""
    rows = rows if rows is not None else DEFAULT_CHARACTER_ROWS
    out: list[dict[str, str | None]] = []
    for row in rows:
        out.append(
            {
                "row_number": str(row.row_number),
                "business_date": row.iso_date(),
                "evidence_number": row.evidence,
                "contractor_name": row.contractor or None,
                "contractor_address": row.address or None,
                "event_description": row.description or None,
                "income_goods_services": _dec(row.income_goods_services),
                "other_income": _dec(row.other_income),
                "total_income": _dec(row.total_income),
                "goods_materials_purchase": _dec(row.goods_materials_purchase),
                "purchase_incidental_costs": _dec(row.purchase_incidental_costs),
                "remuneration": _dec(row.remuneration),
                "other_expenses": _dec(row.other_expenses),
                "total_expenses": _dec(row.total_expenses),
                "notes": row.notes or None,
            }
        )
    return out


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


@dataclass(slots=True)
class _Cells:
    """Zakresy znakowe kolumn odczytane z wiersza numeracji."""

    spans: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def from_ruler(cls, ruler: str) -> _Cells:
        spans, pos = [], 0
        for cell in ruler.split("|")[1:-1]:
            pos += 1
            spans.append((pos, pos + len(cell)))
            pos += len(cell)
        return cls(spans)

    def left(self, number: int) -> int:
        return self.spans[number - 1][0]

    def right(self, number: int) -> int:
        return self.spans[number - 1][1]


def build_character_kpir(
    path: Path,
    rows: list[CharacterRow] | None = None,
    *,
    rows_per_page: int = 4,
    with_summary: bool = True,
    period: str = "01.01.2023 do 30.04.2023",
) -> Path:
    """Zapisuje syntetyczny raport KPiR w wariancie znakowym."""
    rows = rows if rows is not None else DEFAULT_CHARACTER_ROWS
    cells = _Cells.from_ruler(COLUMN_RULER)

    font_size = 6.2
    char_width = pymupdf.get_text_length("0", fontname="cour", fontsize=font_size)
    margin_x, line_height = 20.0, 12.0
    page_width = margin_x * 2 + char_width * (len(COLUMN_RULER) + 2)

    pages = [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)] or [[]]
    page_height = 130 + line_height * (rows_per_page + 6)

    doc = pymupdf.open()
    for index, page_rows in enumerate(pages):
        page = doc.new_page(width=page_width, height=page_height)

        def put(column_char: float, y: float, text: str, page=page) -> None:
            page.insert_text(
                (margin_x + column_char * char_width, y),
                text,
                fontsize=font_size,
                fontname="cour",
            )

        put(0, 26, f"(M) KSIAZKA PRZYCHODOW I ROZCHODOW - Firma Testowa za okres od {period}")
        put(0, 38, f"strona {index + 1}")
        put(0, 54, "| | Data | | | | Przychod |Zakup tow.| Koszty | W Y D A T K I (K O S Z T Y)")
        put(
            0,
            64,
            "| L | zdarze | Nr dowodu | K O N T R A H E N T | |wart.sprz.| pozostale| razem"
            " |handlowych| zakupu |wynagrodz.| pozostale| razem | |badawczo-rozw.| Uwagi |",
        )
        put(
            0,
            74,
            "| P | nia | ksiegowego | imie i nazwisko (firma)| adres |"
            " Opis zdarzenia gospodarczego | i uslug | przychody| (7+8) |cen zakupu| uboczne"
            " |i naturze| wydatki | (12+13) | | opis |wartosc|",
        )
        put(0, 86, COLUMN_RULER)

        y = 100.0
        for row in page_rows:
            text_columns = {
                1: str(row.row_number),
                2: row.date,
                3: row.evidence,
                4: row.contractor,
                5: row.address,
                6: row.description,
                17: row.notes,
            }
            for number, text in text_columns.items():
                if text:
                    put(cells.left(number), y, text)

            money_columns = {
                7: row.income_goods_services,
                8: row.other_income,
                9: row.total_income,
                10: row.goods_materials_purchase,
                11: row.purchase_incidental_costs,
                12: row.remuneration,
                13: row.other_expenses,
                14: row.total_expenses,
            }
            for number in range(7, 16):
                value = _amount(money_columns.get(number))
                if value is not None:
                    put(cells.right(number) - len(value), y, value)
                else:
                    # Puste pole to glif, nie brak tokenu.
                    put(cells.left(number) + 4, y, EMPTY_GLYPH)
            y += line_height

        if with_summary:
            y += line_height * 0.5
            put(0, y, "_" * len(COLUMN_RULER))
            y += line_height
            income = sum((r.total_income or Decimal("0.00")) for r in page_rows)
            expenses = sum((r.total_expenses or Decimal("0.00")) for r in page_rows)
            put(0, y, f"| Suma folio | {income:.2f} 0.00 {income:.2f} 0.00 0.00 {expenses:.2f} |")
            y += line_height
            put(0, y, "| Przeniesienie z folio | 0.00 0.00 0.00 0.00 0.00 0.00 |")
            y += line_height
            put(0, y, f"| Razem | {income:.2f} 0.00 {income:.2f} 0.00 0.00 {expenses:.2f} |")
            y += line_height
            put(0, y, "> Wydruk sporzadzono programem: SYSTEM FIRMA v.48 <")

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.set_metadata({})
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path
