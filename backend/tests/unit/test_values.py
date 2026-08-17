"""Unit tests for money and date parsing (PL formats)."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from kpir_converter.domain.values import (
    BBox,
    Confidence,
    ConfidenceBand,
    ParseError,
    money_to_text,
    parse_business_date,
    parse_money,
)


class TestParseMoney:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("81,30", "81.30"),
            ("81.30", "81.30"),
            ("1 234,56", "1234.56"),
            ("1\u00a0234,56", "1234.56"),  # non breaking space
            ("1\u202f234,56", "1234.56"),  # narrow nbsp
            ("1.234,56", "1234.56"),  # dot thousands, comma decimal
            ("1,234.56", "1234.56"),  # comma thousands, dot decimal
            ("0,00", "0.00"),
            ("12345678,99", "12345678.99"),
            ("-45,20", "-45.20"),
            ("\u221245,20", "-45.20"),  # unicode minus
            ("100", "100.00"),
            ("100,5", "100.50"),
            ("1 000 000,00", "1000000.00"),
            ("81,30 zł", "81.30"),
            ("81,30 PLN", "81.30"),
        ],
    )
    def test_valid_formats(self, raw: str, expected: str) -> None:
        money = parse_money(raw)
        assert money is not None
        assert money.as_text() == expected
        assert isinstance(money.amount, Decimal)

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_is_none_not_zero(self, raw) -> None:
        assert parse_money(raw) is None

    def test_profile_empty_marker_is_none(self) -> None:
        assert parse_money("-", empty_markers=("-", "·")) is None
        assert parse_money("·", empty_markers=("-", "·")) is None

    def test_zero_and_empty_are_different_states(self) -> None:
        assert parse_money("0,00") is not None
        assert parse_money("0,00").as_text() == "0.00"
        assert parse_money("") is None

    @pytest.mark.parametrize("raw", ["abc", "12,,34", "1.234", "12,345", "$12.00", "1,2,3", "--5"])
    def test_ambiguous_raises(self, raw: str) -> None:
        with pytest.raises(ParseError):
            parse_money(raw)

    def test_scale_exceeded_raises(self) -> None:
        with pytest.raises(ParseError) as exc:
            parse_money("12,345")
        assert exc.value.code in ("MONEY_SCALE_EXCEEDED", "INVALID_MONEY")

    def test_never_uses_float(self) -> None:
        money = parse_money("0,10")
        total = money.amount * 3
        assert total == Decimal("0.30")  # a float would give 0.30000000000000004

    def test_money_to_text_quantizes(self) -> None:
        assert money_to_text(Decimal("5")) == "5.00"
        assert money_to_text(Decimal("5.1")) == "5.10"


class TestParseDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("04.01.2018", "2018-01-04"),
            ("4.1.2018", "2018-01-04"),
            ("04-01-2018", "2018-01-04"),
            ("04/01/2018", "2018-01-04"),
            ("2018-01-04", "2018-01-04"),
            ("31.12.2018", "2018-12-31"),
        ],
    )
    def test_valid(self, raw: str, expected: str) -> None:
        date = parse_business_date(raw)
        assert date is not None
        assert date.as_text() == expected

    def test_two_digit_year_needs_context(self) -> None:
        with pytest.raises(ParseError):
            parse_business_date("04.01.18")

    def test_two_digit_year_with_context(self) -> None:
        date = parse_business_date("04.01.18", century_pivot_year=2018)
        assert date is not None
        assert date.value == _dt.date(2018, 1, 4)

    def test_impossible_date_raises(self) -> None:
        with pytest.raises(ParseError) as exc:
            parse_business_date("31.02.2018")
        assert exc.value.code == "INVALID_DATE"

    def test_empty_marker(self) -> None:
        assert parse_business_date("-", empty_markers=("-",)) is None
        assert parse_business_date("") is None


class TestConfidence:
    def test_band_thresholds(self) -> None:
        assert Confidence(0.95).band(review_below=0.85, critical_below=0.6) == ConfidenceBand.HIGH
        assert Confidence(0.7).band(review_below=0.85, critical_below=0.6) == ConfidenceBand.REVIEW
        assert (
            Confidence(0.4).band(review_below=0.85, critical_below=0.6) == ConfidenceBand.CRITICAL
        )

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            Confidence(1.5)


class TestBBox:
    def test_geometry_invariants(self) -> None:
        box = BBox(0.1, 0.2, 0.4, 0.5)
        assert box.width == pytest.approx(0.3)
        assert box.height == pytest.approx(0.3)
        assert box.center_x == pytest.approx(0.25)

    def test_ordering_enforced(self) -> None:
        with pytest.raises(ValueError):
            BBox(0.5, 0.0, 0.1, 0.2)

    def test_union_and_overlap(self) -> None:
        a = BBox(0.0, 0.0, 0.2, 0.1)
        b = BBox(0.1, 0.0, 0.3, 0.1)
        assert a.horizontal_overlap(b) == pytest.approx(0.1)
        assert a.union(b).as_tuple() == (0.0, 0.0, 0.3, 0.1)
