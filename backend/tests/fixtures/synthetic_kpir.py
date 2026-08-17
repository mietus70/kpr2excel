"""Synthetic KPiR PDF generator used by tests.

The generated documents contain NO real personal data: contractor names, evidence
numbers and addresses are obviously fake, deterministic strings. The generator
reproduces the structural traits that make real KPiR exports hard to parse:

- a two-level table header with numbered columns,
- vector rule lines around every cell,
- a repeated header and footer on each page,
- letter-spaced document title text,
- multi-line event descriptions,
- empty cells rendered as a dash glyph,
- Polish number formatting (``1 234,56``).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import fitz

__all__ = ["SyntheticRow", "build_synthetic_kpir", "DEFAULT_ROWS", "expected_records"]

# Column geometry as a fraction of the printable table width.
_COLUMNS: tuple[tuple[str, str, float], ...] = (
    ("row_number", "1", 0.030),
    ("business_date", "2", 0.055),
    ("evidence_number", "3", 0.070),
    ("contractor_name", "4", 0.095),
    ("contractor_address", "5", 0.085),
    ("event_description", "6", 0.100),
    ("income_goods_services", "7", 0.062),
    ("other_income", "8", 0.058),
    ("total_income", "9", 0.058),
    ("goods_materials_purchase", "10", 0.058),
    ("purchase_incidental_costs", "11", 0.054),
    ("remuneration", "12", 0.054),
    ("other_expenses", "13", 0.054),
    ("total_expenses", "14", 0.054),
    ("research_development_costs", "15", 0.045),
    ("notes", "16", 0.038),
)

_HEADER_LABELS = {
    "row_number": "Lp.",
    "business_date": "Data",
    "evidence_number": "Nr dowodu",
    "contractor_name": "Kontrahent",
    "contractor_address": "adres",
    "event_description": "Opis zdarzenia",
    "income_goods_services": "Sprzedaz towarow",
    "other_income": "Pozostale przychody",
    "total_income": "Razem przychod",
    "goods_materials_purchase": "Zakup towarow",
    "purchase_incidental_costs": "Koszty uboczne",
    "remuneration": "Wynagrodzenia",
    "other_expenses": "Pozostale wydatki",
    "total_expenses": "Razem wydatki",
    "research_development_costs": "badawczo-rozwojowej",
    "notes": "Uwagi",
}


@dataclass(slots=True)
class SyntheticRow:
    row_number: int
    business_date: str  # DD.MM.YYYY as printed
    evidence_number: str
    contractor_name: str
    contractor_address: str
    event_description: str
    income_goods_services: Decimal | None = None
    other_income: Decimal | None = None
    goods_materials_purchase: Decimal | None = None
    purchase_incidental_costs: Decimal | None = None
    remuneration: Decimal | None = None
    other_expenses: Decimal | None = None
    research_development_costs: Decimal | None = None
    notes: str = ""
    extra_description_line: str = ""

    @property
    def total_income(self) -> Decimal | None:
        parts = [p for p in (self.income_goods_services, self.other_income) if p is not None]
        return sum(parts, Decimal("0.00")) if parts else None

    @property
    def total_expenses(self) -> Decimal | None:
        parts = [
            p
            for p in (
                self.goods_materials_purchase,
                self.purchase_incidental_costs,
                self.remuneration,
                self.other_expenses,
            )
            if p is not None
        ]
        return sum(parts, Decimal("0.00")) if parts else None

    def iso_date(self) -> str:
        day, month, year = self.business_date.split(".")
        return _dt.date(int(year), int(month), int(day)).isoformat()


def _pl_money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    text = f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return text


DEFAULT_ROWS: list[SyntheticRow] = [
    SyntheticRow(
        row_number=1,
        business_date="04.01.2018",
        evidence_number="FV/2018/01/001",
        contractor_name="Alfa Testowa Sp. z o.o.",
        contractor_address="ul. Przykladowa 1, 00-001 Miasto",
        event_description="Sprzedaz uslug testowych",
        income_goods_services=Decimal("1230.00"),
        other_income=Decimal("70.50"),
    ),
    SyntheticRow(
        row_number=2,
        business_date="09.01.2018",
        evidence_number="RA/2018/12",
        contractor_name="Beta Fikcyjna S.A.",
        contractor_address="al. Testowa 12, 11-111 Wies",
        event_description="Zakup materialow biurowych",
        goods_materials_purchase=Decimal("415.20"),
        purchase_incidental_costs=Decimal("31.30"),
    ),
    SyntheticRow(
        row_number=3,
        business_date="15.01.2018",
        evidence_number="LP/3/2018",
        contractor_name="Gamma Przyklad",
        contractor_address="ul. Kwiatowa 7, 22-222 Osada",
        event_description="Wynagrodzenie za styczen",
        remuneration=Decimal("3500.00"),
        notes="etat",
        extra_description_line="wraz z premia regulaminowa",
    ),
    SyntheticRow(
        row_number=4,
        business_date="21.01.2018",
        evidence_number="FV/2018/01/044",
        contractor_name="Delta Testowa",
        contractor_address="ul. Morska 4, 33-333 Port",
        event_description="Sprzedaz towarow",
        income_goods_services=Decimal("12345.67"),
        other_expenses=Decimal("89.10"),
    ),
    SyntheticRow(
        row_number=5,
        business_date="28.01.2018",
        evidence_number="ZK/2018/5",
        contractor_name="Epsilon Fikcja",
        contractor_address="pl. Zielony 9, 44-444 Grod",
        event_description="Zakup towarow handlowych",
        goods_materials_purchase=Decimal("980.00"),
        other_expenses=Decimal("20.00"),
        research_development_costs=Decimal("150.00"),
    ),
]


def expected_records(rows: list[SyntheticRow] | None = None) -> list[dict[str, str | None]]:
    """The manually approved expectation used by golden tests."""
    rows = rows or DEFAULT_ROWS
    expected: list[dict[str, str | None]] = []
    for row in rows:
        description = row.event_description
        if row.extra_description_line:
            description = f"{description} {row.extra_description_line}"
        expected.append(
            {
                "row_number": str(row.row_number),
                "business_date": row.iso_date(),
                "evidence_number": row.evidence_number,
                "contractor_name": row.contractor_name,
                "contractor_address": row.contractor_address,
                "event_description": description,
                "income_goods_services": _dec(row.income_goods_services),
                "other_income": _dec(row.other_income),
                "total_income": _dec(row.total_income),
                "goods_materials_purchase": _dec(row.goods_materials_purchase),
                "purchase_incidental_costs": _dec(row.purchase_incidental_costs),
                "remuneration": _dec(row.remuneration),
                "other_expenses": _dec(row.other_expenses),
                "total_expenses": _dec(row.total_expenses),
                "research_development_costs": _dec(row.research_development_costs),
                "notes": row.notes or None,
            }
        )
    return expected


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


@dataclass(slots=True)
class _Layout:
    page_width: float = 842.0  # A4 landscape
    page_height: float = 595.0
    margin_x: float = 20.0
    header_y: float = 62.0
    table_top: float = 120.0
    table_bottom: float = 545.0
    row_height: float = 34.0
    font_size: float = 5.4
    header_font_size: float = 5.0
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)


def build_synthetic_kpir(
    path: Path,
    rows: list[SyntheticRow] | None = None,
    *,
    rows_per_page: int = 3,
    with_rule_lines: bool = True,
    rotation: int = 0,
    letter_spaced_title: bool = True,
) -> Path:
    """Write a synthetic, structurally realistic KPiR PDF."""
    rows = rows if rows is not None else DEFAULT_ROWS
    layout = _Layout()
    doc = fitz.open()

    table_width = layout.page_width - 2 * layout.margin_x
    x_edges: list[float] = [layout.margin_x]
    total_share = sum(share for _, _, share in _COLUMNS)
    for _, _, share in _COLUMNS:
        x_edges.append(x_edges[-1] + table_width * share / total_share)

    pages = [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)] or [[]]
    # With /Rotate set, the stored page box is the *unrotated* one; the viewer
    # turns it so the table still reads as landscape. Mirrors real exports.
    if rotation in (90, 270):
        media_width, media_height = layout.page_height, layout.page_width
    else:
        media_width, media_height = layout.page_width, layout.page_height
    for page_index, page_rows in enumerate(pages):
        page = doc.new_page(width=media_width, height=media_height)
        # Rotation must be applied before drawing so the canvas can compensate.
        if rotation:
            page.set_rotation(rotation)
        canvas = _Canvas(page, rotation)
        _draw_document_header(canvas, layout, page_index + 1, len(pages), letter_spaced_title)
        header_bottom = _draw_table_header(canvas, layout, x_edges, with_rule_lines)
        _draw_rows(canvas, layout, x_edges, page_rows, header_bottom, with_rule_lines)
        _draw_footer(canvas, layout, page_index + 1, len(pages))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.set_metadata({})  # no metadata leakage in fixtures
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


class _Canvas:
    """Draws in canonical (displayed) coordinates on a possibly rotated page.

    With /Rotate set, content coordinates live in the *unrotated* space, so every
    point is pushed through ``page.derotation_matrix`` and glyphs are turned by
    the same angle. This reproduces how real landscape exports are built.
    """

    __slots__ = ("page", "_matrix", "_text_rotation")

    def __init__(self, page: fitz.Page, rotation: int) -> None:
        self.page = page
        self._matrix = page.derotation_matrix
        self._text_rotation = rotation % 360

    def _point(self, x: float, y: float) -> fitz.Point:
        return fitz.Point(x, y) * self._matrix

    def text(self, x: float, y: float, value: str, size: float) -> None:
        self.page.insert_text(
            self._point(x, y),
            value,
            fontsize=size,
            fontname="helv",
            rotate=self._text_rotation,
        )

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.page.draw_line(self._point(x0, y0), self._point(x1, y1), width=0.4)


def _draw_document_header(
    canvas: _Canvas, layout: _Layout, page_no: int, page_count: int, spaced: bool
) -> None:
    title = "PODATKOWA KSIEGA PRZYCHODOW I ROZCHODOW"
    if spaced:
        # Artificially letter-spaced title, like many accounting exports.
        title = " ".join(title.replace(" ", "  "))
    canvas.text(layout.margin_x, 30, title, 7.5)
    canvas.text(
        layout.margin_x,
        46,
        "Firma testowa - dane fikcyjne | Okres: 01.01.2018 - 31.01.2018",
        6,
    )
    canvas.text(layout.page_width - 150, 46, f"Strona {page_no} z {page_count}", 6)


def _draw_table_header(
    canvas: _Canvas, layout: _Layout, x_edges: list[float], with_lines: bool
) -> float:
    top = layout.table_top - 46
    label_y = top + 12
    number_y = top + 34
    bottom = top + 42

    for index, (key, number, _share) in enumerate(_COLUMNS):
        x0, x1 = x_edges[index], x_edges[index + 1]
        label = _HEADER_LABELS[key]
        _insert_wrapped(canvas, label, x0 + 1.5, label_y, x1 - x0 - 3, layout.header_font_size)
        canvas.text((x0 + x1) / 2 - 2, number_y, number, layout.header_font_size)
    if with_lines:
        _hline(canvas, x_edges[0], x_edges[-1], top)
        _hline(canvas, x_edges[0], x_edges[-1], number_y - 9)
        _hline(canvas, x_edges[0], x_edges[-1], bottom)
        for x in x_edges:
            _vline(canvas, x, top, bottom)
    return bottom


def _draw_rows(
    canvas: _Canvas,
    layout: _Layout,
    x_edges: list[float],
    rows: list[SyntheticRow],
    start_y: float,
    with_lines: bool,
) -> None:
    y = start_y
    for row in rows:
        y_next = y + layout.row_height
        values = {
            "row_number": str(row.row_number),
            "business_date": row.business_date,
            "evidence_number": row.evidence_number,
            "contractor_name": row.contractor_name,
            "contractor_address": row.contractor_address,
            "event_description": row.event_description,
            "income_goods_services": _pl_money(row.income_goods_services),
            "other_income": _pl_money(row.other_income),
            "total_income": _pl_money(row.total_income),
            "goods_materials_purchase": _pl_money(row.goods_materials_purchase),
            "purchase_incidental_costs": _pl_money(row.purchase_incidental_costs),
            "remuneration": _pl_money(row.remuneration),
            "other_expenses": _pl_money(row.other_expenses),
            "total_expenses": _pl_money(row.total_expenses),
            "research_development_costs": _pl_money(row.research_development_costs),
            "notes": row.notes or "-",
        }
        for index, (key, _number, _share) in enumerate(_COLUMNS):
            x0, x1 = x_edges[index], x_edges[index + 1]
            text = values[key]
            if key == "event_description" and row.extra_description_line:
                _insert_wrapped(canvas, text, x0 + 1.5, y + 9, x1 - x0 - 3, layout.font_size)
                _insert_wrapped(
                    canvas,
                    row.extra_description_line,
                    x0 + 1.5,
                    y + 20,
                    x1 - x0 - 3,
                    layout.font_size,
                )
            elif key in ("contractor_name", "contractor_address", "event_description", "notes"):
                _insert_wrapped(canvas, text, x0 + 1.5, y + 9, x1 - x0 - 3, layout.font_size)
            else:
                width = fitz.get_text_length(text, fontname="helv", fontsize=layout.font_size)
                right_aligned = key not in ("row_number", "business_date", "evidence_number")
                x = x1 - width - 2 if right_aligned else x0 + 1.5
                canvas.text(x, y + 9, text, layout.font_size)
        if with_lines:
            _hline(canvas, x_edges[0], x_edges[-1], y_next)
            for x in x_edges:
                _vline(canvas, x, y, y_next)
        y = y_next


def _draw_footer(canvas: _Canvas, layout: _Layout, page_no: int, page_count: int) -> None:
    canvas.text(
        layout.margin_x,
        layout.page_height - 22,
        "Suma strony - dokument testowy, dane fikcyjne",
        5.5,
    )
    canvas.text(layout.page_width - 120, layout.page_height - 22, f"{page_no}/{page_count}", 5.5)


def _insert_wrapped(
    canvas: _Canvas, text: str, x: float, y: float, width: float, size: float
) -> None:
    words = text.split()
    line = ""
    offset = 0.0
    for word in words:
        candidate = f"{line} {word}".strip()
        if fitz.get_text_length(candidate, fontname="helv", fontsize=size) > width and line:
            canvas.text(x, y + offset, line, size)
            offset += size + 1.6
            line = word
        else:
            line = candidate
    if line:
        canvas.text(x, y + offset, line, size)


def _hline(canvas: _Canvas, x0: float, x1: float, y: float) -> None:
    canvas.line(x0, y, x1, y)


def _vline(canvas: _Canvas, x: float, y0: float, y1: float) -> None:
    canvas.line(x, y0, x, y1)
