"""Golden tests: expected cells, geometry and diagnostics per page.

Never regenerate these expectations automatically without inspecting the diff.
An extraction quality regression is a bug even when the technical tests pass.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fixtures.synthetic_kpir import (
    DEFAULT_ROWS,
    SyntheticRow,
    build_synthetic_kpir,
    expected_records,
)
from kpir_converter.application.extraction import extract_page, match_profile
from kpir_converter.infrastructure.pdf.adapter import PdfDocumentAdapter


def extract_all(pdf: Path, profile, *, period_year: int | None = 2018):
    records = []
    issues = []
    with PdfDocumentAdapter(pdf) as adapter:
        texts = [
            " ".join(t.raw_text for t in adapter.page_content(n).tokens)
            for n in range(1, adapter.page_count + 1)
        ]
        match, _diag = match_profile([profile], texts)
        score = match.score if match else 0.5
        index = 0
        for number in range(1, adapter.page_count + 1):
            content = adapter.page_content(number)
            result = extract_page(
                profile,
                content.tokens,
                content.horizontal_lines,
                content.vertical_lines,
                page_number=number,
                profile_score=score,
                period_year=period_year,
                start_logical_index=index,
            )
            records.extend(result.records)
            issues.extend(result.issues)
            index += len(result.records)
    return records, issues


class TestGoldenCorpus:
    def test_profile_is_recognised(self, sample_pdf: Path, profile) -> None:
        with PdfDocumentAdapter(sample_pdf) as adapter:
            texts = [" ".join(t.raw_text for t in adapter.page_content(1).tokens)]
        match, diagnostics = match_profile([profile], texts)
        assert match is not None, diagnostics
        assert match.profile.id == "kpir_pl_2018"
        assert match.score >= profile.min_match_score

    def test_record_detection_accuracy(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        assert len(records) == len(DEFAULT_ROWS)

    def test_every_cell_matches_expectation(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        expected = expected_records()
        mismatches: list[str] = []
        for index, (record, want) in enumerate(zip(records, expected, strict=True)):
            for key, want_value in want.items():
                cell = record.cell(key)
                got = cell.parsed_value_text if cell else None
                if got != want_value:
                    mismatches.append(
                        f"row {index} col {key}: expected {want_value!r}, got {got!r}"
                    )
        assert not mismatches, "\n".join(mismatches)

    def test_no_issues_on_clean_document(self, sample_pdf: Path, profile) -> None:
        _records, issues = extract_all(sample_pdf, profile)
        codes = sorted({i.code for i in issues})
        assert codes == [], f"unexpected issues: {codes}"

    def test_amounts_never_shift_between_columns(self, sample_pdf: Path, profile) -> None:
        """The critical accounting failure mode: a value in the neighbouring column."""
        records, _ = extract_all(sample_pdf, profile)
        for record, row in zip(records, DEFAULT_ROWS, strict=True):
            for key in (
                "income_goods_services",
                "other_income",
                "goods_materials_purchase",
                "purchase_incidental_costs",
                "remuneration",
                "other_expenses",
                "research_development_costs",
            ):
                cell = record.cell(key)
                want = getattr(row, key)
                got = Decimal(cell.parsed_value_text) if cell and cell.parsed_value_text else None
                assert got == want, f"row {row.row_number} column {key}"

    def test_source_spans_are_normalised(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        for record in records:
            for cell in record.cells.values():
                if cell.source is None:
                    continue
                x0, y0, x1, y1 = cell.source.bbox.as_tuple()
                assert 0.0 <= x0 <= x1 <= 1.0
                assert 0.0 <= y0 <= y1 <= 1.0
                assert cell.source.page_number >= 1

    def test_source_span_lands_in_its_column(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        record = records[0]
        date_cell = record.cell("business_date")
        expenses_cell = record.cell("total_income")
        assert date_cell.source is not None and expenses_cell.source is not None
        assert date_cell.source.bbox.x1 < expenses_cell.source.bbox.x0

    def test_multiline_description_is_joined(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        description = records[2].cell("event_description").parsed_value_text
        assert description == "Wynagrodzenie za styczen wraz z premia regulaminowa"

    def test_empty_marker_becomes_empty_not_zero(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        cell = records[0].cell("remuneration")
        assert cell.parsed_value_text is None
        assert cell.is_empty

    def test_raw_text_is_preserved(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        cell = records[0].cell("income_goods_services")
        assert cell.raw_text  # the original printed form, e.g. "1 230,00"
        assert cell.parsed_value_text == "1230.00"

    def test_determinism(self, sample_pdf: Path, profile) -> None:
        first, _ = extract_all(sample_pdf, profile)
        second, _ = extract_all(sample_pdf, profile)
        as_text = lambda rs: [  # noqa: E731
            {k: c.parsed_value_text for k, c in r.cells.items()} for r in rs
        ]
        assert as_text(first) == as_text(second)

    def test_confidence_is_high_for_clean_pages(self, sample_pdf: Path, profile) -> None:
        records, _ = extract_all(sample_pdf, profile)
        for record in records:
            for cell in record.cells.values():
                if not cell.is_empty:
                    assert cell.confidence.score >= profile.confidence.review_below


class TestLayoutVariants:
    def test_rotated_pages(self, tmp_path: Path, profile) -> None:
        pdf = build_synthetic_kpir(tmp_path / "rot.pdf", rotation=90)
        records, _ = extract_all(pdf, profile)
        assert len(records) == len(DEFAULT_ROWS)
        assert records[0].cell("business_date").parsed_value_text == "2018-01-04"

    def test_without_vector_rule_lines(self, tmp_path: Path, profile) -> None:
        """Falls back to row-number anchors when the table has no drawn lines."""
        pdf = build_synthetic_kpir(tmp_path / "nolines.pdf", with_rule_lines=False)
        records, _ = extract_all(pdf, profile)
        assert len(records) == len(DEFAULT_ROWS)
        assert records[0].cell("evidence_number").parsed_value_text == "FV/2018/01/001"

    def test_repeated_header_and_footer_are_skipped(self, tmp_path: Path, profile) -> None:
        pdf = build_synthetic_kpir(tmp_path / "multi.pdf", rows_per_page=1)
        records, _ = extract_all(pdf, profile)
        assert len(records) == len(DEFAULT_ROWS)
        numbers = [r.cell("row_number").parsed_value_text for r in records]
        assert numbers == ["1", "2", "3", "4", "5"]

    def test_zero_amount_is_not_empty(self, tmp_path: Path, profile) -> None:
        rows = [
            SyntheticRow(
                row_number=1,
                business_date="04.01.2018",
                evidence_number="FV/1",
                contractor_name="Zero Testowa",
                contractor_address="ul. Zerowa 0",
                event_description="Korekta do zera",
                income_goods_services=Decimal("0.00"),
            )
        ]
        pdf = build_synthetic_kpir(tmp_path / "zero.pdf", rows)
        records, _ = extract_all(pdf, profile)
        cell = records[0].cell("income_goods_services")
        assert cell.parsed_value_text == "0.00"
        assert not cell.is_empty
        assert records[0].cell("other_income").is_empty

    def test_large_amount_with_thousands_separator(self, tmp_path: Path, profile) -> None:
        records, _ = extract_all(build_synthetic_kpir(tmp_path / "big.pdf"), profile)
        assert records[3].cell("income_goods_services").parsed_value_text == "12345.67"


class TestPageIsolation:
    def test_corrupted_pdf_is_rejected(self, tmp_path: Path) -> None:
        from kpir_converter.infrastructure.pdf.adapter import PdfError

        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot really a pdf\n")
        with pytest.raises(PdfError), PdfDocumentAdapter(broken) as adapter:
            adapter.page_content(1)
