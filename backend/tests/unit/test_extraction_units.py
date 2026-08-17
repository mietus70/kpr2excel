"""Unit tests for extraction stages that golden tests exercise only indirectly."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from kpir_converter.application.extraction import (
    _RULER_CELL_RE,
    _RULER_LINE_RE,
    _fill_missing_boundaries,
    _ruler_cells_from_tokens,
    assemble_text,
    detect_column_layout,
    detect_table_zone,
    extract_page,
    match_profile,
    segment_rows,
)
from kpir_converter.application.services import _detect_period
from kpir_converter.domain.geometry import group_tokens_into_lines
from kpir_converter.domain.models import Token
from kpir_converter.domain.profiles import profile_from_dict
from kpir_converter.domain.values import BBox


def token(text: str, x0: float, y0: float, x1: float, y1: float) -> Token:
    return Token(raw_text=text, bbox=BBox(x0, y0, x1, y1), page_number=1, baseline=y1)


@pytest.fixture()
def tiny_profile():
    """A three-column profile keeps layout assertions readable."""
    return profile_from_dict(
        {
            "id": "tiny",
            "version": 1,
            "header_phrases": ["KSIEGA TESTOWA"],
            "row_number_column": "lp",
            "date_column": "data",
            "default_filter_column": "kwota",
            "empty_markers": ["-"],
            "columns": [
                {
                    "key": "lp",
                    "type": "integer",
                    "x0": 0.0,
                    "x1": 0.2,
                    "form_number": "1",
                    "header_keywords": ["Lp"],
                },
                {
                    "key": "data",
                    "type": "date",
                    "x0": 0.2,
                    "x1": 0.6,
                    "form_number": "2",
                    "header_keywords": ["Data"],
                },
                {
                    "key": "kwota",
                    "type": "money",
                    "x0": 0.6,
                    "x1": 1.0,
                    "form_number": "3",
                    "header_keywords": ["Kwota"],
                },
            ],
        }
    )


class TestProfileMatching:
    # A realistic page carries the full two-level header.
    FULL_HEADER = (
        "Lp. Data zdarzenia gospodarczego Nr dowodu ksiegowego Kontrahent imie i nazwisko firma "
        "adres Opis zdarzenia gospodarczego Sprzedaz towarow i uslug Pozostale przychody "
        "Razem przychod Zakup towarow handlowych i materialow Koszty uboczne zakupu "
        "Wynagrodzenia Pozostale wydatki Razem wydatki badawczo rozwojowej Uwagi "
        "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16"
    )

    def test_letter_spaced_title_still_matches(self, profile) -> None:
        """The title is often letter-spaced glyph by glyph in real exports."""
        spaced = "P O D A T K O W A  K S I E G A  P R Z Y C H O D O W  I  R O Z C H O D O W"
        match, diagnostics = match_profile([profile], [f"{spaced} {self.FULL_HEADER}"])
        assert match is not None, diagnostics
        assert match.profile.id == "kpir_pl_2018"

    def test_partial_header_is_below_threshold(self, profile) -> None:
        """Too few anchors must not be forced onto the profile."""
        match, diagnostics = match_profile(
            [profile], ["PODATKOWA KSIEGA PRZYCHODOW I ROZCHODOW Lp. Data"]
        )
        assert match is None
        assert any("below threshold" in d for d in diagnostics)

    def test_unrelated_document_is_rejected(self, profile) -> None:
        match, diagnostics = match_profile([profile], ["Faktura VAT nr 5 Zamowienie kurier"])
        assert match is None
        assert diagnostics

    def test_no_profiles_registered(self) -> None:
        match, diagnostics = match_profile([], ["cokolwiek"])
        assert match is None
        assert diagnostics == ["no profiles registered"]

    def test_score_components_are_reported(self, profile) -> None:
        text = f"PODATKOWA KSIEGA PRZYCHODOW I ROZCHODOW {self.FULL_HEADER}"
        match, diagnostics = match_profile([profile], [text])
        assert match is not None, diagnostics
        assert set(match.components) == {"phrases", "keywords", "form_numbers"}
        assert 0.0 <= match.score <= 1.0


class TestColumnLayout:
    def test_exact_vector_lines_win(self, tiny_profile) -> None:
        layout = detect_column_layout(tiny_profile, [], [0.0, 0.25, 0.55, 1.0])
        assert layout.source == "vector_lines"
        assert layout.confidence >= 0.95
        assert layout.boundaries[0] == ("lp", 0.0, 0.25)
        assert layout.boundaries[2] == ("kwota", 0.55, 1.0)

    def test_extra_decorative_lines_are_snapped(self, tiny_profile) -> None:
        # Two extra lines that do not correspond to column separators.
        lines = [0.0, 0.02, 0.2, 0.4, 0.6, 0.98, 1.0]
        layout = detect_column_layout(tiny_profile, [], lines)
        assert layout.source in ("vector_lines", "table_frame")
        keys = [b[0] for b in layout.boundaries]
        assert keys == ["lp", "data", "kwota"]

    def test_frame_only_scales_the_template(self, tiny_profile) -> None:
        # Only the outer frame is drawn: the template is scaled onto it.
        layout = detect_column_layout(tiny_profile, [], [0.1, 0.9])
        assert layout.source == "table_frame"
        assert layout.boundaries[0][1] == pytest.approx(0.1)
        assert layout.boundaries[-1][2] == pytest.approx(0.9)

    def test_falls_back_to_raw_template(self, tiny_profile) -> None:
        layout = detect_column_layout(tiny_profile, [], [])
        assert layout.source == "profile_template"
        assert layout.confidence < 0.7  # low confidence must be reported
        assert layout.diagnostics

    def test_boundaries_never_overlap(self, tiny_profile) -> None:
        layout = detect_column_layout(tiny_profile, [], [0.05, 0.3, 0.62, 0.95])
        for left, right in zip(layout.boundaries, layout.boundaries[1:], strict=False):
            assert left[2] <= right[1] + 1e-9


class TestTableZone:
    def test_form_number_row_ends_the_header(self, tiny_profile) -> None:
        tokens = [
            token("Lp.", 0.05, 0.05, 0.12, 0.07),
            token("Data", 0.25, 0.05, 0.35, 0.07),
            token("Kwota", 0.65, 0.05, 0.75, 0.07),
            token("1", 0.08, 0.10, 0.10, 0.12),
            token("2", 0.38, 0.10, 0.40, 0.12),
            token("3", 0.78, 0.10, 0.80, 0.12),
            token("1", 0.08, 0.20, 0.10, 0.22),
        ]
        header_bottom, footer_top = detect_table_zone(tiny_profile, group_tokens_into_lines(tokens))
        assert header_bottom == pytest.approx(0.12)
        assert footer_top > 0.2

    def test_evidence_number_is_not_mistaken_for_a_page_marker(self, tiny_profile) -> None:
        """Regression: "ZK/2018/5" once looked like "page 1/2" and ate the row."""
        tokens = [
            token("1", 0.05, 0.05, 0.10, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.10, 0.32),
            token("ZK/2018/5", 0.25, 0.30, 0.45, 0.32),
        ]
        _header_bottom, footer_top = detect_table_zone(
            tiny_profile, group_tokens_into_lines(tokens)
        )
        assert footer_top > 0.32

    def test_real_footer_is_detected(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.05, 0.10, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.10, 0.32),
            token("Strona", 0.05, 0.95, 0.15, 0.97),
            token("1", 0.16, 0.95, 0.18, 0.97),
        ]
        _header_bottom, footer_top = detect_table_zone(
            tiny_profile, group_tokens_into_lines(tokens)
        )
        assert footer_top <= 0.95


class TestRowSegmentation:
    def _layout(self, tiny_profile):
        return detect_column_layout(tiny_profile, [], [0.0, 0.2, 0.6, 1.0])

    def test_rule_lines_define_bands(self, tiny_profile) -> None:
        tokens = [token("1", 0.05, 0.32, 0.09, 0.34), token("2", 0.05, 0.42, 0.09, 0.44)]
        lines = group_tokens_into_lines(tokens)
        bands = segment_rows(
            tiny_profile,
            lines,
            self._layout(tiny_profile),
            [0.30, 0.40, 0.50],
            header_bottom=0.28,
            footer_top=0.9,
        )
        assert len(bands) == 2
        assert all(b.confidence > 0.9 for b in bands)
        assert bands[0].signals == ("rule_lines",)

    def test_row_number_anchors_without_lines(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.32, 0.09, 0.34),
            token("opis", 0.25, 0.36, 0.40, 0.38),  # continuation line
            token("2", 0.05, 0.42, 0.09, 0.44),
        ]
        bands = segment_rows(
            tiny_profile,
            group_tokens_into_lines(tokens),
            self._layout(tiny_profile),
            [],
            header_bottom=0.28,
            footer_top=0.9,
        )
        assert len(bands) == 2
        # The continuation line belongs to the first record.
        assert len(bands[0].lines) == 2
        assert bands[0].confidence < 0.9  # weaker signal is reported as such

    def test_date_alone_can_start_a_record(self, tiny_profile) -> None:
        tokens = [
            token("04.01.2018", 0.25, 0.32, 0.45, 0.34),
            token("09.01.2018", 0.25, 0.42, 0.45, 0.44),
        ]
        bands = segment_rows(
            tiny_profile,
            group_tokens_into_lines(tokens),
            self._layout(tiny_profile),
            [],
            header_bottom=0.28,
            footer_top=0.9,
        )
        assert len(bands) == 2

    def test_no_body_lines(self, tiny_profile) -> None:
        tokens = [token("naglowek", 0.05, 0.05, 0.2, 0.07)]
        bands = segment_rows(
            tiny_profile,
            group_tokens_into_lines(tokens),
            self._layout(tiny_profile),
            [],
            header_bottom=0.28,
            footer_top=0.9,
        )
        assert bands == []


class TestTextAssembly:
    def test_letter_spacing_is_repaired(self, profile) -> None:
        tokens = [
            token("K", 0.100, 0.1, 0.103, 0.11),
            token("P", 0.1045, 0.1, 0.1075, 0.11),
            token("R", 0.109, 0.1, 0.112, 0.11),
        ]
        text, operations = assemble_text(tokens, profile)
        assert text == "KPR"
        assert "letter_spacing_joined" in operations

    def test_normal_words_are_not_glued(self, profile) -> None:
        tokens = [
            token("Alfa", 0.10, 0.1, 0.16, 0.11),
            token("Testowa", 0.18, 0.1, 0.28, 0.11),
        ]
        text, operations = assemble_text(tokens, profile)
        assert text == "Alfa Testowa"
        assert "letter_spacing_joined" not in operations

    def test_lines_join_in_reading_order(self, profile) -> None:
        tokens = [
            token("druga", 0.10, 0.20, 0.20, 0.21),
            token("pierwsza", 0.10, 0.10, 0.22, 0.11),
        ]
        text, _ = assemble_text(tokens, profile)
        assert text == "pierwsza druga"

    def test_whitespace_is_collapsed(self, profile) -> None:
        tokens = [token("a   b", 0.1, 0.1, 0.3, 0.11)]
        text, operations = assemble_text(tokens, profile)
        assert text == "a b"
        assert "whitespace_collapsed" in operations

    def test_empty_input(self, profile) -> None:
        assert assemble_text([], profile) == ("", [])


class TestExtractPageEdgeCases:
    def test_page_without_tokens(self, profile) -> None:
        result = extract_page(profile, [], [], [], page_number=1)
        assert result.records == []

    def test_low_layout_confidence_raises_an_issue(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.30, 0.09, 0.32),
            token("04.01.2018", 0.25, 0.30, 0.45, 0.32),
        ]
        result = extract_page(tiny_profile, tokens, [], [], page_number=1)
        assert "LOW_PROFILE_CONFIDENCE" in {i.code for i in result.issues}

    def test_unparseable_money_creates_an_issue(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.05, 0.09, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.09, 0.32),
            token("04.01.2018", 0.25, 0.30, 0.45, 0.32),
            token("12,,34", 0.65, 0.30, 0.85, 0.32),
        ]
        result = extract_page(
            tiny_profile, tokens, [0.28, 0.35], [0.0, 0.2, 0.6, 1.0], page_number=1
        )
        assert result.records
        assert "INVALID_MONEY" in {i.code for i in result.issues}
        # A failed parse must not invent a value.
        assert result.records[0].cell("kwota").parsed_value_text is None

    def test_header_only_band_is_not_a_record(self, tiny_profile) -> None:
        tokens = [
            token("Lp.", 0.05, 0.05, 0.12, 0.07),
            token("Data", 0.25, 0.05, 0.35, 0.07),
            token("Kwota", 0.65, 0.05, 0.75, 0.07),
        ]
        result = extract_page(tiny_profile, tokens, [], [0.0, 0.2, 0.6, 1.0], page_number=1)
        assert result.records == []

    def test_confidence_components_are_recorded(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.05, 0.09, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.09, 0.32),
            token("04.01.2018", 0.25, 0.30, 0.45, 0.32),
            token("100,00", 0.65, 0.30, 0.85, 0.32),
        ]
        result = extract_page(
            tiny_profile,
            tokens,
            [0.28, 0.35],
            [0.0, 0.2, 0.6, 1.0],
            page_number=1,
            profile_score=0.9,
        )
        cell = result.records[0].cell("kwota")
        assert cell.parsed_value_text == "100.00"
        names = {name for name, _ in cell.confidence.components}
        assert names == {
            "profile_match",
            "column_boundary",
            "row_segmentation",
            "type_parse",
            "transformations",
        }

    def test_empty_marker_is_not_zero(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.05, 0.09, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.09, 0.32),
            token("04.01.2018", 0.25, 0.30, 0.45, 0.32),
            token("-", 0.65, 0.30, 0.70, 0.32),
        ]
        result = extract_page(
            tiny_profile, tokens, [0.28, 0.35], [0.0, 0.2, 0.6, 1.0], page_number=1
        )
        assert result.records[0].cell("kwota").is_empty

    def test_logical_index_offset_is_respected(self, tiny_profile) -> None:
        tokens = [
            token("1", 0.05, 0.05, 0.09, 0.07),
            token("2", 0.35, 0.05, 0.40, 0.07),
            token("3", 0.75, 0.05, 0.80, 0.07),
            token("1", 0.05, 0.30, 0.09, 0.32),
            token("04.01.2018", 0.25, 0.30, 0.45, 0.32),
        ]
        result = extract_page(
            tiny_profile,
            tokens,
            [0.28, 0.35],
            [0.0, 0.2, 0.6, 1.0],
            page_number=2,
            start_logical_index=7,
        )
        assert result.records[0].logical_index == 7
        assert result.records[0].source_page_from == 2


class TestPeriodDetection:
    """Okres musi pochodzić z nagłówka, nie z numerów dowodów."""

    def test_period_from_header_range(self) -> None:
        start, end = _detect_period(
            ["KSIAZKA PRZYCHODOW I ROZCHODOW za okres od 01.01.2023 do 30.04.2023"]
        )
        assert (start, end) == (_dt.date(2023, 1, 1), _dt.date(2023, 4, 30))

    def test_evidence_numbers_do_not_create_period(self) -> None:
        assert _detect_period(["XX/YY/99/1/0002 11/02/0333 ABC/00009/07/44"]) == (None, None)

    def test_header_range_wins_over_stray_numbers(self) -> None:
        start, end = _detect_period(["za okres od 01.01.2018 do 31.12.2018 11/02/0002 22/09/0333"])
        assert start.year == 2018 and end.year == 2018

    def test_implausible_year_is_rejected(self) -> None:
        assert _detect_period(["11.02.0002"]) == (None, None)

    def test_standalone_dates_still_detected(self) -> None:
        start, end = _detect_period(["02.01.2018", "31.12.2018"])
        assert (start, end) == (_dt.date(2018, 1, 2), _dt.date(2018, 12, 31))


class TestRulerWithMissingCells:
    """Ruler z brakującymi celami nie może unieważniać całego układu."""

    def test_trailing_gaps_are_interpolated(self) -> None:
        nan = float("nan")
        filled = _fill_missing_boundaries(
            [("a", 0.0, 0.1), ("b", 0.1, 0.5), ("c", nan, nan), ("d", nan, nan)]
        )
        assert all(x0 == x0 for _key, x0, _x1 in filled)
        assert [key for key, _x0, _x1 in filled] == ["a", "b", "c", "d"]
        assert all(filled[i][1] <= filled[i + 1][1] for i in range(len(filled) - 1))

    def test_middle_gap_stays_between_neighbours(self) -> None:
        nan = float("nan")
        filled = _fill_missing_boundaries([("a", 0.0, 0.1), ("b", nan, nan), ("c", 0.5, 0.6)])
        assert 0.1 <= filled[1][1] <= 0.5


class TestRulerFillerVariants:
    """Wiersz numeracji bywa rysowany myślnikami zamiast podkreśleń."""

    @staticmethod
    def _ruler(filler: str) -> str:
        widths = [5, 9, 15, 24, 38, 40] + [9] * 10 + [12]
        return "|" + "|".join(str(i + 1).center(w, filler) for i, w in enumerate(widths)) + "|"

    def test_underscore_ruler_is_recognised(self) -> None:
        assert _RULER_LINE_RE.match(self._ruler("_"))

    def test_dash_ruler_is_recognised(self) -> None:
        # Wariant wydruków 2018-2021: "|--1--|---2---|".
        assert _RULER_LINE_RE.match(self._ruler("-"))

    def test_decorative_dash_line_is_not_a_ruler(self) -> None:
        # Ta sama długość co ruler, ale bez cyfr - to tylko ozdobnik.
        assert not _RULER_LINE_RE.match("-" * 267)

    def test_decorative_underscore_line_is_not_a_ruler(self) -> None:
        assert not _RULER_LINE_RE.match("_" * 267)

    @pytest.mark.parametrize(
        "text",
        ["faktura 12 z dnia 3", "1 234,56", "02.01.2018", "|wart.sprz.|", "pozostałe|"],
    )
    def test_ordinary_text_is_not_a_ruler(self, text: str) -> None:
        assert not _RULER_LINE_RE.match(text)

    def test_dash_cells_are_parsed(self) -> None:
        assert _RULER_CELL_RE.match("--7--").group(1) == "7"
        assert _RULER_CELL_RE.match("---17---").group(1) == "17"

    def test_dash_ruler_yields_every_cell(self) -> None:
        token = Token(
            raw_text=self._ruler("-"),
            bbox=BBox(0.0155, 0.140, 0.9841, 0.150),
            baseline=0.1454,
            page_number=1,
        )
        cells = _ruler_cells_from_tokens([token])
        assert cells is not None
        assert [number for number, _x0, _x1 in cells] == list(range(1, 18))


class TestLayoutCarriedAcrossPages:
    """Ruler bywa drukowany tylko na stronie 1; kolejne strony dziedziczą układ."""

    @staticmethod
    def _tokens(with_ruler: bool) -> list[Token]:
        widths = [5, 9, 15, 24, 38, 40] + [9] * 10 + [12]
        ruler = "|" + "|".join(str(i + 1).center(w, "-") for i, w in enumerate(widths)) + "|"
        tokens = []
        if with_ruler:
            tokens.append(
                Token(
                    raw_text=ruler,
                    bbox=BBox(0.0155, 0.140, 0.9841, 0.150),
                    baseline=0.1454,
                    page_number=1,
                )
            )
        for index in range(3):
            y = 0.30 + index * 0.03
            tokens.append(
                Token(
                    raw_text=str(index + 1),
                    bbox=BBox(0.030, y, 0.034, y + 0.01),
                    baseline=y,
                    page_number=1,
                )
            )
            tokens.append(
                Token(
                    raw_text="02.01.2018",
                    bbox=BBox(0.052, y, 0.088, y + 0.01),
                    baseline=y,
                    page_number=1,
                )
            )
        return tokens

    def _profile(self):
        from kpir_converter.infrastructure.storage.profiles_loader import load_registry

        return load_registry(Path("profiles")).get("kpir_pl_system_firma@1")

    def test_page_without_ruler_reuses_previous_layout(self) -> None:
        profile = self._profile()
        first = extract_page(profile, self._tokens(True), [], [], page_number=1)
        assert first.layout.source == "number_ruler"

        without = extract_page(profile, self._tokens(False), [], [], page_number=2)
        assert without.layout.source != "number_ruler"

        with_carry = extract_page(
            profile,
            self._tokens(False),
            [],
            [],
            page_number=2,
            fallback_layout=first.layout,
        )
        assert with_carry.layout.source == "number_ruler"
        assert with_carry.layout.boundaries == first.layout.boundaries

    def test_weaker_layout_never_overrides_a_stronger_one(self) -> None:
        profile = self._profile()
        strong = extract_page(profile, self._tokens(True), [], [], page_number=1)
        weak = extract_page(profile, self._tokens(False), [], [], page_number=2)
        # Przeniesienie działa tylko w stronę większej pewności.
        result = extract_page(
            profile,
            self._tokens(True),
            [],
            [],
            page_number=3,
            fallback_layout=weak.layout,
        )
        assert result.layout.source == "number_ruler"
        assert result.layout.boundaries == strong.layout.boundaries
