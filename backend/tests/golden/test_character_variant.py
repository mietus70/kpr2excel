"""Golden tests dla wariantu znakowego KPiR (raport typu "SYSTEM FIRMA").

Regresja z realnego zgłoszenia: dokument o 17 kolumnach, rysowany znakami
zamiast linii wektorowych, z glifem ``˙`` jako pustą komórką i datami
dwucyfrowymi. Wcześniej wszystkie 598 wierszy takiego pliku miały problemy
krytyczne i eksport był blokowany.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fixtures.synthetic_character_kpir import (
    DEFAULT_CHARACTER_ROWS,
    CharacterRow,
    build_character_kpir,
    expected_character_records,
)
from kpir_converter.application.extraction import (
    detect_column_layout,
    detect_table_zone,
    extract_page,
    match_profile,
)
from kpir_converter.domain.geometry import group_tokens_into_lines
from kpir_converter.infrastructure.pdf.adapter import PdfDocumentAdapter
from kpir_converter.infrastructure.storage.profiles_loader import load_registry

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def registry():
    return load_registry(REPO / "profiles")


@pytest.fixture(scope="module")
def char_profile(registry):
    return registry.get("kpir_pl_system_firma")


@pytest.fixture()
def char_pdf(tmp_path: Path) -> Path:
    return build_character_kpir(tmp_path / "character.pdf")


def extract(pdf: Path, registry, *, period_year: int = 2023):
    records, issues = [], []
    with PdfDocumentAdapter(pdf) as adapter:
        texts = [
            " ".join(t.raw_text for t in adapter.page_content(n).tokens)
            for n in range(1, adapter.page_count + 1)
        ]
        match, diagnostics = match_profile(registry.all(), texts)
        assert match is not None, f"nie dopasowano profilu: {diagnostics}"
        index = 0
        for number in range(1, adapter.page_count + 1):
            content = adapter.page_content(number)
            result = extract_page(
                match.profile,
                content.tokens,
                content.horizontal_lines,
                content.vertical_lines,
                page_number=number,
                profile_score=match.score,
                period_year=period_year,
                start_logical_index=index,
            )
            records.extend(result.records)
            issues.extend(result.issues)
            index += len(result.records)
    return match, records, issues


class TestProfileSelection:
    def test_character_variant_wins_over_base_profile(self, char_pdf, registry) -> None:
        match, _records, _issues = extract(char_pdf, registry)
        assert match.profile.id == "kpir_pl_system_firma"

    def test_document_has_no_vector_lines(self, char_pdf) -> None:
        """Sedno problemu: tabela jest rysowana znakami, nie grafiką wektorową."""
        with PdfDocumentAdapter(char_pdf) as adapter:
            content = adapter.page_content(1)
        assert content.vertical_lines == []
        assert content.horizontal_lines == []


class TestColumnRuler:
    def test_boundaries_come_from_the_number_ruler(self, char_pdf, char_profile) -> None:
        with PdfDocumentAdapter(char_pdf) as adapter:
            content = adapter.page_content(1)
        layout = detect_column_layout(char_profile, content.tokens, content.vertical_lines)
        assert layout.source == "number_ruler"
        assert layout.confidence >= 0.9

    def test_ruler_covers_every_profile_column(self, char_pdf, char_profile) -> None:
        with PdfDocumentAdapter(char_pdf) as adapter:
            content = adapter.page_content(1)
        layout = detect_column_layout(char_profile, content.tokens, content.vertical_lines)
        assert [b[0] for b in layout.boundaries] == [c.key for c in char_profile.columns]
        for left, right in zip(layout.boundaries, layout.boundaries[1:], strict=False):
            assert left[2] <= right[1] + 1e-9


class TestGoldenRecords:
    def test_all_records_detected(self, char_pdf, registry) -> None:
        _match, records, _issues = extract(char_pdf, registry)
        assert len(records) == len(DEFAULT_CHARACTER_ROWS)

    def test_no_issues_on_clean_document(self, char_pdf, registry) -> None:
        _match, _records, issues = extract(char_pdf, registry)
        assert sorted({i.code for i in issues}) == []

    def test_every_cell_matches_expectation(self, char_pdf, registry) -> None:
        _match, records, _issues = extract(char_pdf, registry)
        expected = expected_character_records()
        mismatches = []
        for index, (record, want) in enumerate(zip(records, expected, strict=True)):
            for key, want_value in want.items():
                cell = record.cell(key)
                got = cell.parsed_value_text if cell else None
                if got != want_value:
                    mismatches.append(
                        f"wiersz {index} kol {key}: oczekiwano {want_value!r}, jest {got!r}"
                    )
        assert not mismatches, "\n".join(mismatches)

    def test_two_digit_year_resolved_from_period(self, char_pdf, registry) -> None:
        _match, records, _issues = extract(char_pdf, registry)
        assert records[0].cell("business_date").parsed_value_text == "2023-01-02"

    def test_dot_above_glyph_is_empty_not_zero(self, char_pdf, registry) -> None:
        """``˙`` oznacza pustkę; zamiana na 0.00 byłaby błędem księgowym."""
        _match, records, _issues = extract(char_pdf, registry)
        cell = records[0].cell("income_goods_services")
        assert cell.is_empty
        assert cell.parsed_value_text is None

    def test_internal_document_without_contractor(self, char_pdf, registry) -> None:
        _match, records, _issues = extract(char_pdf, registry)
        record = records[2]
        assert record.cell("evidence_number").parsed_value_text == "1 WEW"
        assert record.cell("contractor_name").is_empty
        assert record.cell("remuneration").parsed_value_text == "4049.70"

    def test_amounts_land_in_the_right_columns(self, char_pdf, registry) -> None:
        """Najgroźniejszy błąd: kwota w sąsiedniej kolumnie."""
        _match, records, _issues = extract(char_pdf, registry)
        for record, row in zip(records, DEFAULT_CHARACTER_ROWS, strict=True):
            for key in (
                "income_goods_services",
                "other_income",
                "remuneration",
                "other_expenses",
            ):
                cell = record.cell(key)
                got = Decimal(cell.parsed_value_text) if cell and cell.parsed_value_text else None
                assert got == getattr(row, key), f"wiersz {row.row_number}, kolumna {key}"

    def test_notes_column_is_captured(self, char_pdf, registry) -> None:
        _match, records, _issues = extract(char_pdf, registry)
        assert records[3].cell("notes").parsed_value_text == "(75% z 89.69)"

    def test_determinism(self, char_pdf, registry) -> None:
        _m1, first, _i1 = extract(char_pdf, registry)
        _m2, second, _i2 = extract(char_pdf, registry)
        as_text = [{k: c.parsed_value_text for k, c in r.cells.items()} for r in first]
        assert as_text == [{k: c.parsed_value_text for k, c in r.cells.items()} for r in second]


class TestSummaryRows:
    def test_summary_rows_are_not_records(self, char_pdf, registry) -> None:
        """ "Suma folio" / "Razem" nie mogą stać się wierszami danych."""
        _match, records, _issues = extract(char_pdf, registry)
        assert len(records) == len(DEFAULT_CHARACTER_ROWS)
        for record in records:
            joined = " ".join((c.parsed_value_text or "") for c in record.cells.values()).lower()
            assert "suma folio" not in joined
            assert "przeniesienie" not in joined
            assert "system firma" not in joined

    def test_summary_detected_above_the_bottom_band(self, char_pdf, char_profile) -> None:
        """Na niepełnej stronie podsumowanie wypada wysoko - i tak kończy dane."""
        with PdfDocumentAdapter(char_pdf) as adapter:
            content = adapter.page_content(1)
        lines = group_tokens_into_lines(content.tokens)
        _header_bottom, footer_top = detect_table_zone(char_profile, lines)
        assert footer_top < 0.85

    def test_page_without_summary_still_works(self, tmp_path: Path, registry) -> None:
        pdf = build_character_kpir(tmp_path / "nosum.pdf", with_summary=False)
        _match, records, issues = extract(pdf, registry)
        assert len(records) == len(DEFAULT_CHARACTER_ROWS)
        assert sorted({i.code for i in issues}) == []


class TestMultiPage:
    def test_repeated_header_and_summary_per_page(self, tmp_path: Path, registry) -> None:
        pdf = build_character_kpir(tmp_path / "multi.pdf", rows_per_page=2)
        _match, records, issues = extract(pdf, registry)
        assert len(records) == len(DEFAULT_CHARACTER_ROWS)
        assert [r.cell("row_number").parsed_value_text for r in records] == ["1", "2", "3", "4"]
        assert sorted({i.code for i in issues}) == []


class TestSumRules:
    def test_expense_total_is_columns_12_plus_13(self, char_pdf, registry, char_profile) -> None:
        """Wzór KPiR: kolumna 14 = 12 + 13. Kolumny 10 i 11 nie wchodzą."""
        rule = next(r for r in char_profile.sum_rules if r.target == "total_expenses")
        assert set(rule.components) == {"remuneration", "other_expenses"}
        _match, _records, issues = extract(char_pdf, registry)
        assert "EXPENSE_TOTAL_MISMATCH" not in {i.code for i in issues}

    def test_purchase_columns_do_not_trigger_false_mismatch(self, tmp_path: Path, registry) -> None:
        """Regresja: wliczanie kol. 10-11 do sumy dawało fałszywy alarm."""
        rows = [
            CharacterRow(
                row_number=1,
                date="05.01.23",
                evidence="FV/9",
                contractor="Delta Hurt",
                address="Miasto Handlowa 1",
                description="zakup towarow handlowych",
                goods_materials_purchase=Decimal("5000.00"),
                purchase_incidental_costs=Decimal("120.00"),
                other_expenses=Decimal("300.00"),
            )
        ]
        pdf = build_character_kpir(tmp_path / "purchase.pdf", rows)
        _match, records, issues = extract(pdf, registry)
        assert "EXPENSE_TOTAL_MISMATCH" not in {i.code for i in issues}
        # Kolumna 14 zawiera wyłącznie 12 + 13, czyli tutaj same "pozostałe wydatki".
        assert records[0].cell("total_expenses").parsed_value_text == "300.00"
        assert records[0].cell("goods_materials_purchase").parsed_value_text == "5000.00"


class TestBaseProfileUnaffected:
    def test_vector_variant_still_uses_vector_lines(self, tmp_path: Path, registry) -> None:
        """Nowy detektor nie może zepsuć dokumentów z liniami wektorowymi."""
        from fixtures.synthetic_kpir import build_synthetic_kpir

        pdf = build_synthetic_kpir(tmp_path / "vector.pdf")
        with PdfDocumentAdapter(pdf) as adapter:
            content = adapter.page_content(1)
        profile = registry.get("kpir_pl_2018")
        layout = detect_column_layout(profile, content.tokens, content.vertical_lines)
        assert layout.source == "vector_lines"
