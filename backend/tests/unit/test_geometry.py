"""Geometry: line grouping, clustering and column assignment."""

from __future__ import annotations

import pytest

from kpir_converter.domain.geometry import (
    assign_token_to_column,
    cluster_values,
    group_tokens_into_lines,
    tokens_bbox,
)
from kpir_converter.domain.models import Token
from kpir_converter.domain.values import BBox

BOUNDS = [("a", 0.0, 0.2), ("b", 0.2, 0.5), ("c", 0.5, 1.0)]


def token(text: str, x0: float, y0: float, x1: float, y1: float) -> Token:
    return Token(raw_text=text, bbox=BBox(x0, y0, x1, y1), page_number=1, baseline=y1)


class TestLineGrouping:
    def test_tokens_cluster_by_baseline(self) -> None:
        tokens = [
            token("b", 0.5, 0.10, 0.6, 0.12),
            token("a", 0.1, 0.10, 0.2, 0.12),
            token("c", 0.1, 0.30, 0.2, 0.32),
        ]
        lines = group_tokens_into_lines(tokens)
        assert len(lines) == 2
        # Reading order is restored regardless of the PDF's internal ordering.
        assert lines[0].text == "a b"
        assert lines[1].text == "c"

    def test_small_baseline_jitter_stays_one_line(self) -> None:
        tokens = [
            token("a", 0.1, 0.100, 0.2, 0.1200),
            token("b", 0.3, 0.1005, 0.4, 0.1205),
        ]
        assert len(group_tokens_into_lines(tokens)) == 1

    def test_distinct_rows_stay_separate(self) -> None:
        tokens = [
            token("a", 0.1, 0.10, 0.2, 0.12),
            token("b", 0.1, 0.20, 0.2, 0.22),
        ]
        assert len(group_tokens_into_lines(tokens)) == 2

    def test_empty_input(self) -> None:
        assert group_tokens_into_lines([]) == []

    def test_line_bounds(self) -> None:
        # Same baseline (y1), different heights: a taller glyph extends the top.
        tokens = [token("a", 0.1, 0.10, 0.2, 0.12), token("b", 0.3, 0.09, 0.5, 0.12)]
        line = group_tokens_into_lines(tokens)[0]
        assert line.top == pytest.approx(0.09)
        assert line.bottom == pytest.approx(0.12)
        assert line.bbox.x0 == pytest.approx(0.1)


class TestClustering:
    def test_close_values_merge(self) -> None:
        assert cluster_values([0.10, 0.101, 0.5], tolerance=0.005) == [[0.10, 0.101], [0.5]]

    def test_far_values_stay_apart(self) -> None:
        assert len(cluster_values([0.1, 0.2, 0.3], tolerance=0.01)) == 3

    def test_empty(self) -> None:
        assert cluster_values([], tolerance=0.01) == []


class TestColumnAssignment:
    def test_token_fully_inside_a_column(self) -> None:
        key, share, crosses = assign_token_to_column(BBox(0.25, 0.1, 0.35, 0.12), BOUNDS)
        assert key == "b"
        assert share == pytest.approx(1.0)
        assert crosses is False

    def test_token_mostly_in_one_column(self) -> None:
        key, _share, crosses = assign_token_to_column(BBox(0.18, 0.1, 0.30, 0.12), BOUNDS)
        assert key == "b"
        assert crosses is True

    def test_evenly_split_token_uses_centre(self) -> None:
        # Straddles a/b symmetrically; the centre at 0.20 belongs to b.
        key, _share, crosses = assign_token_to_column(BBox(0.15, 0.1, 0.25, 0.12), BOUNDS)
        assert key == "b"
        assert crosses is True

    def test_zero_width_token_uses_centre(self) -> None:
        key, _share, _crosses = assign_token_to_column(BBox(0.6, 0.1, 0.6, 0.12), BOUNDS)
        assert key == "c"

    def test_token_outside_all_columns_snaps_to_nearest(self) -> None:
        key, share, crosses = assign_token_to_column(BBox(1.2, 0.1, 1.3, 0.12), BOUNDS)
        assert key == "c"
        assert share == 0.0
        assert crosses is True

    def test_no_boundaries(self) -> None:
        key, _share, _crosses = assign_token_to_column(BBox(0.1, 0.1, 0.2, 0.12), [])
        assert key is None


class TestTokensBBox:
    def test_union_of_tokens(self) -> None:
        box = tokens_bbox([token("a", 0.1, 0.2, 0.3, 0.25), token("b", 0.4, 0.18, 0.6, 0.24)])
        assert box.as_tuple() == pytest.approx((0.1, 0.18, 0.6, 0.25))

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            tokens_bbox([])
