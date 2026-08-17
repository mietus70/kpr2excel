"""Pure geometry helpers used by the extractor.

Everything here works on normalised ``0..1`` coordinates and is deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .models import Token
from .values import BBox

__all__ = [
    "TextLine",
    "group_tokens_into_lines",
    "cluster_values",
    "assign_token_to_column",
    "tokens_bbox",
]


@dataclass(slots=True)
class TextLine:
    """Tokens that share a baseline, ordered left to right."""

    baseline: float
    tokens: list[Token]

    @property
    def bbox(self) -> BBox:
        return tokens_bbox(self.tokens)

    @property
    def text(self) -> str:
        return " ".join(t.raw_text for t in self.tokens)

    @property
    def top(self) -> float:
        return min(t.bbox.y0 for t in self.tokens)

    @property
    def bottom(self) -> float:
        return max(t.bbox.y1 for t in self.tokens)


def tokens_bbox(tokens: Sequence[Token]) -> BBox:
    if not tokens:
        raise ValueError("cannot compute bbox of an empty token sequence")
    x0 = min(t.bbox.x0 for t in tokens)
    y0 = min(t.bbox.y0 for t in tokens)
    x1 = max(t.bbox.x1 for t in tokens)
    y1 = max(t.bbox.y1 for t in tokens)
    return BBox(x0, y0, x1, y1)


def group_tokens_into_lines(tokens: Sequence[Token], *, tolerance: float = 0.004) -> list[TextLine]:
    """Cluster tokens by baseline. Independent of the PDF's internal text order."""
    ordered = sorted(tokens, key=lambda t: (t.baseline, t.bbox.x0))
    lines: list[TextLine] = []
    for token in ordered:
        if lines and abs(token.baseline - lines[-1].baseline) <= tolerance:
            lines[-1].tokens.append(token)
            # Running average keeps the cluster centred.
            count = len(lines[-1].tokens)
            lines[-1].baseline += (token.baseline - lines[-1].baseline) / count
        else:
            lines.append(TextLine(baseline=token.baseline, tokens=[token]))
    for line in lines:
        line.tokens.sort(key=lambda t: t.bbox.x0)
    return lines


def cluster_values(values: Sequence[float], *, tolerance: float) -> list[list[float]]:
    """1-D clustering used for detecting rule lines and column anchors."""
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def assign_token_to_column(
    token_bbox: BBox,
    boundaries: Sequence[tuple[str, float, float]],
) -> tuple[str | None, float, bool]:
    """Assign a token to a column.

    Resolution order (ARCHITECTURE §7.6):
      1. largest overlapping area share,
      2. token centre position,
      3. caller-side expected type (handled by the extractor),
      4. otherwise report ambiguity instead of guessing.

    Returns ``(column_key, share, crosses_boundary)``.
    """
    width = token_bbox.width
    if width <= 0:
        centre = token_bbox.center_x
        for key, x0, x1 in boundaries:
            if x0 <= centre < x1:
                return key, 1.0, False
        return None, 0.0, False

    shares: list[tuple[str, float]] = []
    for key, x0, x1 in boundaries:
        overlap = max(0.0, min(token_bbox.x1, x1) - max(token_bbox.x0, x0))
        if overlap > 0:
            shares.append((key, overlap / width))
    if not shares:
        centre = token_bbox.center_x
        best: tuple[str, float] | None = None
        for key, x0, x1 in boundaries:
            distance = 0.0 if x0 <= centre < x1 else min(abs(centre - x0), abs(centre - x1))
            if best is None or distance < best[1]:
                best = (key, distance)
        return (best[0] if best else None), 0.0, True

    shares.sort(key=lambda item: item[1], reverse=True)
    top_key, top_share = shares[0]
    crosses = len(shares) > 1 and shares[1][1] > 0.15
    if crosses and len(shares) > 1 and abs(top_share - shares[1][1]) < 0.1:
        # Nearly equal split - fall back to the centre rule.
        centre = token_bbox.center_x
        for key, x0, x1 in boundaries:
            if x0 <= centre < x1:
                return key, top_share, True
    return top_key, top_share, crosses
