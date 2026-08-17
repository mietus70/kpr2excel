"""Deterministic extraction pipeline.

    tokens -> profile match -> column boundaries -> row bands
    -> token assignment -> text assembly -> typed parsing -> confidence

Every stage takes an explicit typed input, returns a result plus diagnostics and
is deterministic for the same data and profile version.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..domain.geometry import TextLine, cluster_values, group_tokens_into_lines
from ..domain.models import (
    Cell,
    Issue,
    IssueSeverity,
    KpirRecord,
    SourceSpan,
    Token,
    ValueType,
)
from ..domain.profiles import ColumnType, KpirProfile, ProfileColumn
from ..domain.validation import IssueCode
from ..domain.values import BBox, Confidence, ParseError, parse_business_date, parse_money

EXTRACTOR_VERSION = "1.0.0"

__all__ = [
    "EXTRACTOR_VERSION",
    "ProfileMatch",
    "ColumnLayout",
    "RowBand",
    "PageExtraction",
    "match_profile",
    "detect_column_layout",
    "segment_rows",
    "extract_page",
    "assemble_text",
]


# --------------------------------------------------------------------------------------
# Profile recognition
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class ProfileMatch:
    profile: KpirProfile
    score: float
    components: dict[str, float] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.score >= self.profile.min_match_score


def _normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _collapse_letter_spacing(text: str) -> str:
    """``K S I E G A`` -> ``KSIEGA`` (used only for header matching)."""
    return re.sub(r"(?<=\b\w) (?=\w\b)", "", text)


def match_profile(
    profiles: Sequence[KpirProfile],
    page_texts: Sequence[str],
) -> tuple[ProfileMatch | None, list[str]]:
    """Score every candidate profile against representative pages."""
    diagnostics: list[str] = []
    haystack = _normalize_for_match(" ".join(page_texts))
    haystack_collapsed = _collapse_letter_spacing(haystack)

    scored: list[ProfileMatch] = []
    for profile in profiles:
        phrase_hits = 0
        for phrase in profile.header_phrases:
            needle = _normalize_for_match(phrase)
            if needle and (needle in haystack or needle in haystack_collapsed):
                phrase_hits += 1
        phrase_score = phrase_hits / max(1, len(profile.header_phrases))

        keyword_hits = 0
        keyword_total = 0
        for column in profile.columns:
            for keyword in column.header_keywords:
                keyword_total += 1
                needle = _normalize_for_match(keyword)
                if needle and (needle in haystack or needle in haystack_collapsed):
                    keyword_hits += 1
        keyword_score = keyword_hits / keyword_total if keyword_total else 0.0

        # Column numbers 1..16 printed in the form header.
        numbers = sum(
            1
            for column in profile.columns
            if column.form_number and re.search(rf"\b{re.escape(column.form_number)}\b", haystack)
        )
        number_score = numbers / max(1, len(profile.columns))

        score = 0.5 * phrase_score + 0.35 * keyword_score + 0.15 * number_score
        scored.append(
            ProfileMatch(
                profile=profile,
                score=round(score, 4),
                components={
                    "phrases": round(phrase_score, 4),
                    "keywords": round(keyword_score, 4),
                    "form_numbers": round(number_score, 4),
                },
            )
        )

    if not scored:
        return None, ["no profiles registered"]
    scored.sort(key=lambda m: m.score, reverse=True)
    best = scored[0]
    if not best.is_confident:
        diagnostics.append(
            f"profile score {best.score} below threshold {best.profile.min_match_score}"
        )
        return None, diagnostics
    if len(scored) > 1:
        runner_up = scored[1]
        if best.score - runner_up.score < best.profile.match_margin:
            diagnostics.append("PROFILE_AMBIGUOUS")
            return None, diagnostics
    return best, diagnostics


# --------------------------------------------------------------------------------------
# Column layout
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class ColumnLayout:
    """Resolved column boundaries for a single page."""

    boundaries: list[tuple[str, float, float]]
    confidence: float
    source: str  # "vector_lines" | "header_anchors" | "profile_template"
    diagnostics: list[str] = field(default_factory=list)

    def as_tuples(self) -> list[tuple[str, float, float]]:
        return self.boundaries


def _template_boundaries(profile: KpirProfile) -> list[tuple[str, float, float]]:
    return [(c.key, c.x0, c.x1) for c in sorted(profile.columns, key=lambda c: c.x0)]


def detect_column_layout(
    profile: KpirProfile,
    tokens: Sequence[Token],
    vertical_lines: Sequence[float],
    *,
    table_x0: float | None = None,
    table_x1: float | None = None,
) -> ColumnLayout:
    """Hybrid detection: vector lines first, then header anchors, then the template."""
    diagnostics: list[str] = []
    expected = len(profile.columns)

    # Some accounting programs draw the table with characters instead of vector
    # graphics (monospace reports). Then the printed column-number row
    # "|_1_|__2__|...|_17_|" is the most reliable boundary source available.
    #
    # Vector rule lines still win when present: they mark the true cell edges,
    # while the ruler only approximates them from character positions. The ruler
    # is therefore consulted first only when there are no usable vertical lines.
    has_vector_lines = len(cluster_values(vertical_lines, tolerance=0.006)) >= expected
    if not has_vector_lines:
        char_ruler = _boundaries_from_number_ruler(profile, tokens)
        if char_ruler is not None:
            diagnostics.append("granice z wiersza numeracji kolumn")
            return ColumnLayout(char_ruler, 0.94, "number_ruler", diagnostics)

    clusters = [sum(c) / len(c) for c in cluster_values(vertical_lines, tolerance=0.006)]
    clusters = [x for x in clusters if 0.0 <= x <= 1.0]
    ordered_columns = sorted(profile.columns, key=lambda c: c.x0)

    if len(clusters) >= expected + 1:
        table_left, table_right = clusters[0], clusters[-1]
        span = table_right - table_left
        if span > 0.3:
            if len(clusters) == expected + 1:
                # Exactly one separator per column boundary: use them directly.
                boundaries = [
                    (column.key, clusters[i], clusters[i + 1])
                    for i, column in enumerate(ordered_columns)
                ]
                return ColumnLayout(boundaries, 0.97, "vector_lines", diagnostics)

            # Extra decorative lines: map the template onto the observed frame
            # first, then snap each expected boundary to the nearest separator.
            template_left = ordered_columns[0].x0
            template_span = ordered_columns[-1].x1 - template_left or 1.0

            def scaled(value: float) -> float:
                return table_left + (value - template_left) / template_span * span

            wanted = [scaled(ordered_columns[0].x0)] + [scaled(c.x1) for c in ordered_columns]
            chosen: list[float] = []
            used: set[int] = set()
            tolerance = max(0.02, span / expected * 0.6)
            for target in wanted:
                best_index, best_dist = None, 1.0
                for index, value in enumerate(clusters):
                    if index in used:
                        continue
                    distance = abs(value - target)
                    if distance < best_dist:
                        best_index, best_dist = index, distance
                if best_index is None or best_dist > tolerance:
                    chosen = []
                    break
                used.add(best_index)
                chosen.append(clusters[best_index])
            if len(chosen) == expected + 1:
                chosen.sort()
                boundaries = [
                    (column.key, chosen[i], chosen[i + 1])
                    for i, column in enumerate(ordered_columns)
                ]
                return ColumnLayout(boundaries, 0.95, "vector_lines", diagnostics)
            diagnostics.append("vector lines did not align with the profile template")

    if len(clusters) >= 2:
        # The outermost vertical rules still define the table frame; scaling the
        # relative template onto that frame beats using raw page coordinates.
        table_x0, table_x1 = clusters[0], clusters[-1]
        if table_x1 - table_x0 > 0.3:
            frame_boundaries = _scale_template(profile, table_x0, table_x1)
            diagnostics.append("scaled the profile template onto the detected table frame")
            frame_layout = ColumnLayout(frame_boundaries, 0.8, "table_frame", diagnostics)
        else:
            frame_layout = None
    else:
        frame_layout = None

    # Header anchors: find the x position of each column's keywords. Only a
    # handful of columns carry unambiguous keywords, so a small but well spread
    # and well fitting set is enough to scale the template reliably.
    anchors = _header_anchors(profile, tokens)
    if len(anchors) >= 4 and (max(a[0] for a in anchors) - min(a[0] for a in anchors)) > 0.4:
        _scale, _offset, quality = _fit_linear(anchors)
        if quality > 0.9:
            # Piecewise interpolation rather than one global line: real forms
            # stretch some columns and squeeze others, and a single slope
            # misplaces the narrow outer columns badly.
            boundaries = [
                (
                    column.key,
                    _clamp(_interpolate(column.x0, anchors)),
                    _clamp(_interpolate(column.x1, anchors)),
                )
                for column in ordered_columns
            ]
            boundaries = _repair_monotonic(boundaries)
            return ColumnLayout(boundaries, 0.86, "header_anchors", diagnostics)
        diagnostics.append("header anchor fit quality too low")

    if frame_layout is not None:
        return frame_layout

    if table_x0 is not None and table_x1 is not None and table_x1 > table_x0:
        diagnostics.append("using the caller supplied table frame")
        return ColumnLayout(
            _scale_template(profile, table_x0, table_x1), 0.7, "table_frame", diagnostics
        )

    diagnostics.append("falling back to the raw profile template")
    return ColumnLayout(_template_boundaries(profile), 0.62, "profile_template", diagnostics)


# Wypełniacz wiersza numeracji kolumn zależy od wariantu wydruku:
# "|_1_|__2__|" (podkreślenia) albo "|--1--|---2---|" (myślniki). Oba znaczą
# to samo, więc obie postacie muszą być rozpoznawane. Znaki spoza tej klasy
# celowo NIE są dopuszczane - inaczej zwykły tekst z cyframi mógłby zostać
# wzięty za ruler.
_RULER_FILL = r"[_\-\u2500\u2014\u2013]"
_RULER_CELL_RE = re.compile(rf"^{_RULER_FILL}*(\d{{1,2}}){_RULER_FILL}*$")
_RULER_LINE_RE = re.compile(
    rf"^\|?{_RULER_FILL}*\d{{1,2}}{_RULER_FILL}*(\|{_RULER_FILL}*\d{{1,2}}{_RULER_FILL}*)+\|?$"
)


def _ruler_cells_from_tokens(
    tokens: Sequence[Token],
) -> list[tuple[int, float, float]] | None:
    """Locate the printed column-number row and return each cell's extent.

    Two renderings occur in the wild:

    * separate tokens per cell (``|_1_|`` ``|__2__|`` ...), and
    * one single token holding the whole ruler, because it contains no spaces.

    The second case needs character-level interpolation inside the token: the
    font is monospace here, so a character index maps linearly onto the token's
    horizontal extent.
    """
    # --- case 1: the whole ruler arrived as one token -------------------------
    for token in tokens:
        text = token.raw_text.strip()
        if len(text) < 10 or not _RULER_LINE_RE.match(text):
            continue
        width = token.bbox.x1 - token.bbox.x0
        if width <= 0:
            continue
        step = width / len(text)
        cells: list[tuple[int, float, float]] = []
        start = 0
        for index, char in enumerate(text + "|"):
            if char != "|":
                continue
            segment = text[start:index]
            match = _RULER_CELL_RE.match(segment)
            if match:
                cells.append(
                    (
                        int(match.group(1)),
                        token.bbox.x0 + start * step,
                        token.bbox.x0 + index * step,
                    )
                )
            start = index + 1
        if len(cells) >= 5:
            return cells

    # --- case 2: one token per ruler cell ------------------------------------
    candidates: list[tuple[int, float, float, float]] = []
    for token in tokens:
        match = _RULER_CELL_RE.match(token.raw_text.strip())
        if match:
            candidates.append((int(match.group(1)), token.bbox.x0, token.bbox.x1, token.baseline))
    if len(candidates) < 5:
        return None
    by_line: dict[float, list[tuple[int, float, float, float]]] = {}
    for item in candidates:
        by_line.setdefault(round(item[3], 3), []).append(item)
    ruler = max(by_line.values(), key=len)
    if len(ruler) < 5:
        return None
    ruler.sort(key=lambda item: item[1])
    return [(number, x0, x1) for number, x0, x1, _baseline in ruler]


def _boundaries_from_number_ruler(
    profile: KpirProfile, tokens: Sequence[Token]
) -> list[tuple[str, float, float]] | None:
    """Read column boundaries from a printed column-number row.

    Character-drawn reports render a ruler such as ``|_1_|____2___|...|__17__|``.
    Each cell carries both its form number and its exact horizontal extent, which
    is far more precise than guessing from text positions. Cells are matched to
    profile columns by ``form_number``; a column split into sub-columns (e.g. the
    B+R "opis"/"wartość" pair) shares one ruler cell, which is divided
    proportionally to the template.
    """
    numbers = [c.form_number for c in profile.columns if c.form_number]
    if len(numbers) < 5:
        return None

    ruler = _ruler_cells_from_tokens(tokens)
    if ruler is None or len(ruler) < max(5, int(0.6 * len(set(numbers)))):
        return None

    # The numbers must run 1, 2, 3, ... for this to be a column ruler.
    if [item[0] for item in ruler] != list(range(1, len(ruler) + 1)):
        return None

    extent = {number: (x0, x1) for number, x0, x1 in ruler}
    ordered = sorted(profile.columns, key=lambda c: c.x0)
    boundaries: list[tuple[str, float, float]] = []

    # Older printouts may emit fewer ruler cells than the profile has columns
    # (e.g. a 16-column form read with the 17-column profile). Rather than
    # discarding the ruler entirely - which drops the layout back to the raw
    # template and shifts every column - the trailing profile columns without a
    # ruler cell are interpolated from the last matched one.
    missing = [
        c.key
        for c in ordered
        if not (c.form_number or "").isdigit() or int(c.form_number) not in extent
    ]
    if missing and len(missing) > len(ordered) // 3:
        return None

    for column in ordered:
        try:
            form_number = int(column.form_number) if column.form_number else None
        except ValueError:
            form_number = None
        if form_number is None or form_number not in extent:
            # Brak celi w rulerze: kolumna zostanie dopasowana niżej,
            # po zamknięciu luk między sąsiadami.
            boundaries.append((column.key, float("nan"), float("nan")))
            continue
        siblings = [c for c in ordered if c.form_number == column.form_number]
        x0, x1 = extent[form_number]
        if len(siblings) == 1:
            boundaries.append((column.key, x0, x1))
            continue
        # Shared ruler cell: split it proportionally to the template widths.
        span_start = min(c.x0 for c in siblings)
        span_total = max(c.x1 for c in siblings) - span_start or 1.0
        width = x1 - x0
        boundaries.append(
            (
                column.key,
                x0 + (column.x0 - span_start) / span_total * width,
                x0 + (column.x1 - span_start) / span_total * width,
            )
        )

    # Fill columns that had no ruler cell by splitting the gap between their
    # matched neighbours evenly.
    if any(_is_nan(x0) for _key, x0, _x1 in boundaries):
        boundaries = _fill_missing_boundaries(boundaries)

    # Close the gaps between ruler cells so no token falls between columns.
    closed: list[tuple[str, float, float]] = []
    for index, (key, x0, x1) in enumerate(boundaries):
        left = 0.0 if index == 0 else (boundaries[index - 1][2] + x0) / 2
        right = 1.0 if index == len(boundaries) - 1 else (x1 + boundaries[index + 1][1]) / 2
        closed.append((key, _clamp(left), _clamp(right)))
    return _repair_monotonic(closed)


def _is_nan(value: float) -> bool:
    return value != value


def _fill_missing_boundaries(
    boundaries: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    """Interpolate columns that had no matching ruler cell.

    Consecutive gaps are split evenly between their nearest matched
    neighbours, so column order and monotonicity are preserved.
    """
    filled = list(boundaries)
    index = 0
    while index < len(filled):
        if not _is_nan(filled[index][1]):
            index += 1
            continue
        run_end = index
        while run_end + 1 < len(filled) and _is_nan(filled[run_end + 1][1]):
            run_end += 1
        left = 0.0 if index == 0 else filled[index - 1][2]
        right = 1.0 if run_end + 1 >= len(filled) else filled[run_end + 1][1]
        count = run_end - index + 1
        step = (right - left) / count if right > left else 0.0
        for offset in range(count):
            key = filled[index + offset][0]
            x0 = left + step * offset
            filled[index + offset] = (key, _clamp(x0), _clamp(x0 + step))
        index = run_end + 1
    return filled


def _scale_template(
    profile: KpirProfile, table_x0: float, table_x1: float
) -> list[tuple[str, float, float]]:
    """Map the relative profile template onto an observed table frame."""
    ordered = sorted(profile.columns, key=lambda c: c.x0)
    template_left = ordered[0].x0
    template_span = (ordered[-1].x1 - template_left) or 1.0
    span = table_x1 - table_x0
    return [
        (
            c.key,
            _clamp(table_x0 + (c.x0 - template_left) / template_span * span),
            _clamp(table_x0 + (c.x1 - template_left) / template_span * span),
        )
        for c in ordered
    ]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _interpolate(value: float, anchors: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear map from template X to observed X using matched anchors."""
    points = sorted(anchors)
    if not points:
        return value
    if len(points) == 1:
        return value + (points[0][1] - points[0][0])
    if value <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
    elif value >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
    else:
        (x0, y0), (x1, y1) = points[0], points[1]
        for left, right in zip(points, points[1:], strict=False):
            if left[0] <= value <= right[0]:
                (x0, y0), (x1, y1) = left, right
                break
    if x1 == x0:
        return y0
    return y0 + (value - x0) * (y1 - y0) / (x1 - x0)


def _repair_monotonic(
    boundaries: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    repaired: list[tuple[str, float, float]] = []
    previous_x1 = 0.0
    for key, x0, x1 in boundaries:
        x0 = max(x0, previous_x1)
        x1 = max(x1, x0 + 1e-4)
        repaired.append((key, x0, x1))
        previous_x1 = x1
    return repaired


def _header_anchors(profile: KpirProfile, tokens: Sequence[Token]) -> list[tuple[float, float]]:
    """Pairs of (profile_centre, observed_centre) for matched header keywords."""
    header_tokens = [t for t in tokens if t.bbox.y1 <= profile.header_zone_max_y]
    anchors: list[tuple[float, float]] = []
    for column in profile.columns:
        if not column.header_keywords:
            continue
        needles = [_normalize_for_match(k) for k in column.header_keywords if k]
        matches = [
            t
            for t in header_tokens
            if any(n and n in _normalize_for_match(t.raw_text) for n in needles)
        ]
        if len(matches) == 1:
            expected_centre = (column.x0 + column.x1) / 2
            anchors.append((expected_centre, matches[0].bbox.center_x))
    return anchors


def _fit_linear(anchors: Sequence[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares fit ``observed = scale * expected + offset`` plus R^2."""
    n = len(anchors)
    if n < 2:
        return 1.0, 0.0, 0.0
    mean_x = sum(a[0] for a in anchors) / n
    mean_y = sum(a[1] for a in anchors) / n
    sxx = sum((a[0] - mean_x) ** 2 for a in anchors)
    sxy = sum((a[0] - mean_x) * (a[1] - mean_y) for a in anchors)
    if sxx == 0:
        return 1.0, 0.0, 0.0
    scale = sxy / sxx
    offset = mean_y - scale * mean_x
    ss_tot = sum((a[1] - mean_y) ** 2 for a in anchors)
    ss_res = sum((a[1] - (scale * a[0] + offset)) ** 2 for a in anchors)
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1 - ss_res / ss_tot)
    return scale, offset, r2


# --------------------------------------------------------------------------------------
# Row segmentation
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RowBand:
    y0: float
    y1: float
    lines: list[TextLine]
    confidence: float
    signals: tuple[str, ...] = ()

    @property
    def tokens(self) -> list[Token]:
        return [t for line in self.lines for t in line.tokens]


_INT_RE = re.compile(r"^\d{1,6}[.)]?$")
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")


def detect_table_zone(
    profile: KpirProfile,
    lines: Sequence[TextLine],
) -> tuple[float, float]:
    """Find where the repeated table header ends and the footer begins.

    A fixed profile zone is only a fallback: real exports shift the header when a
    company name wraps, and a hard cut would silently drop the first record.
    """
    header_bottom = 0.0
    footer_top = profile.footer_zone_min_y

    # The strongest signal is the row of printed column numbers (1..17). It is
    # the last line of the header, whatever precedes it.
    form_numbers = [c.form_number for c in profile.columns if c.form_number]
    if form_numbers:
        wanted = set(form_numbers)
        for line in lines:
            texts = [t.raw_text.strip() for t in line.tokens]
            # Character-drawn reports emit the whole ruler as a single token
            # ("|_1_|__2__|...") because it contains no spaces.
            if len(texts) == 1 and _RULER_LINE_RE.match(texts[0]):
                header_bottom = max(header_bottom, line.bottom)
                continue
            if len(texts) < max(3, len(wanted) // 2):
                continue
            hits = sum(1 for t in texts if t in wanted)
            if hits >= max(3, int(0.7 * len(wanted))):
                header_bottom = max(header_bottom, line.bottom)

    if header_bottom == 0.0:
        # Otherwise use the last line dominated by header keywords.
        keywords = {
            _normalize_for_match(k)
            for column in profile.columns
            for k in column.header_keywords
            if k
        }
        keywords |= {_normalize_for_match(p) for p in profile.header_phrases if p}
        keywords.discard("")
        for line in lines:
            if line.top > 0.5:
                break
            if _looks_like_data_row(line):
                # A line that opens with an ordinal number followed by a date is
                # a record, however many header-ish words it happens to contain.
                break
            normalized = _normalize_for_match(line.text)
            if not normalized:
                continue
            hits = sum(1 for k in keywords if k and k in normalized)
            if hits >= 3:
                header_bottom = max(header_bottom, line.bottom)

    if header_bottom == 0.0:
        header_bottom = profile.header_zone_max_y

    # Footer, part 1: trailing lines inside the bottom band of the page.
    # Restricting the page-marker search prevents an evidence number such as
    # "ZK/2018/5" from being mistaken for a "page 1/2" marker.
    for line in reversed(lines):
        if line.top <= max(header_bottom, _FOOTER_SEARCH_FROM):
            break
        if _looks_like_footer(line.text.strip()):
            footer_top = min(footer_top, line.top)
        else:
            break

    # Footer, part 2: an explicit summary row ("Suma folio", "Przeniesienie
    # z folio", "Razem") ends the data no matter where it sits. On the last,
    # partially filled page it appears well above the bottom band, and anything
    # below it — including the totals themselves — must not become a record.
    for line in lines:
        if line.baseline <= header_bottom:
            continue
        stripped = line.text.strip().lstrip("|_=*-—– \t")
        if _SUM_MARKER_RE.match(stripped):
            footer_top = min(footer_top, line.top)
            break

    return header_bottom, footer_top


def _looks_like_data_row(line: TextLine) -> bool:
    """True when the line starts with an ordinal number followed by a date.

    That pairing is the signature of a KPiR record and never occurs in a header,
    so it is a safe guard against classifying the first record as header text.
    """
    texts = [t.raw_text.strip() for t in line.tokens[:4] if t.raw_text.strip()]
    if len(texts) < 2:
        return False
    return bool(_INT_RE.match(texts[0]) and _DATE_TOKEN_RE.match(texts[1]))


_FOOTER_SEARCH_FROM = 0.85
_PAGE_MARKER_RE = re.compile(r"(?i)(\b(strona|str\.?|page)\b|^\s*\d{1,4}\s*/\s*\d{1,4}\s*$)")
# Summary rows are anchored to the start of the line so that an ordinary
# description containing the word "razem" does not disqualify a real record.
# Leading table-drawing characters ("| Suma folio |") are stripped first.
_SUM_MARKER_RE = re.compile(
    r"(?i)^(suma|razem|przeniesienie|z przeniesienia|do przeniesienia|podsumowanie"
    r"|suma folio|suma strony|ogolem|ogółem)\b"
)
_PRINT_MARKER_RE = re.compile(r"(?i)\b(wydruk|sporz[aą]dzono programem|koniec wydruku)\b")
_DECORATION_RE = re.compile(r"^[\s|_=*\-—–+.]+$")


def _looks_like_footer(text: str) -> bool:
    if not text:
        return True
    if _DECORATION_RE.match(text):
        # A pure separator line ("____" / "====") carries no data.
        return True
    stripped = text.lstrip("|_=*-—– \t")
    if _PAGE_MARKER_RE.search(text):
        return True
    if _PRINT_MARKER_RE.search(text):
        return True
    return bool(_SUM_MARKER_RE.match(stripped))


def segment_rows(
    profile: KpirProfile,
    lines: Sequence[TextLine],
    layout: ColumnLayout,
    horizontal_lines: Sequence[float],
    *,
    header_bottom: float | None = None,
    footer_top: float | None = None,
) -> list[RowBand]:
    """Build record bands. Signals ordered by importance (ARCHITECTURE §7.5)."""
    if header_bottom is None or footer_top is None:
        detected_header, detected_footer = detect_table_zone(profile, lines)
        header_bottom = detected_header if header_bottom is None else header_bottom
        footer_top = detected_footer if footer_top is None else footer_top

    body = [line for line in lines if line.baseline > header_bottom and line.top < footer_top]
    # Character-drawn reports separate sections with rules made of "___" or "===".
    # They carry no data and would otherwise be appended to the preceding record.
    body = [line for line in body if not _DECORATION_RE.match(line.text.strip())]
    if not body:
        return []

    rules = [
        y
        for y in (sum(c) / len(c) for c in cluster_values(horizontal_lines, tolerance=0.004))
        if header_bottom - 0.01 <= y <= footer_top + 0.01
    ]

    if len(rules) >= 2:
        bands = _bands_from_rules(body, rules)
        if bands:
            return bands

    return _bands_from_row_numbers(profile, body, layout)


def _bands_from_rules(lines: Sequence[TextLine], rules: Sequence[float]) -> list[RowBand]:
    bands: list[RowBand] = []
    ordered = sorted(rules)
    for top, bottom in zip(ordered, ordered[1:], strict=False):
        if bottom - top < 0.004:
            continue
        inside = [line for line in lines if line.baseline > top and line.baseline <= bottom + 0.002]
        if not inside:
            continue
        bands.append(
            RowBand(y0=top, y1=bottom, lines=inside, confidence=0.96, signals=("rule_lines",))
        )
    return bands


def _bands_from_row_numbers(
    profile: KpirProfile, lines: Sequence[TextLine], layout: ColumnLayout
) -> list[RowBand]:
    """Start a new band on an ordinal number in the first column."""
    bounds = {key: (x0, x1) for key, x0, x1 in layout.boundaries}
    lp_bounds = bounds.get(profile.row_number_column)
    date_bounds = bounds.get(profile.date_column)
    bands: list[RowBand] = []
    current: list[TextLine] = []
    signals = "row_number_anchor"

    def _token_in(line: TextLine, span: tuple[float, float] | None, pattern: re.Pattern[str]):
        if span is None:
            return False
        x0, x1 = span
        # A small tolerance absorbs the residual error of boundary estimation.
        margin = max(0.004, (x1 - x0) * 0.35)
        for token in line.tokens:
            centre = token.bbox.center_x
            if centre < x0 - margin or centre >= x1 + margin:
                continue
            if pattern.match(token.raw_text.strip()):
                return True
        return False

    def starts_record(line: TextLine) -> bool:
        """A record starts on an ordinal number, corroborated by a date."""
        if lp_bounds is None and date_bounds is None:
            return True
        has_lp = _token_in(line, lp_bounds, _INT_RE)
        has_date = _token_in(line, date_bounds, _DATE_TOKEN_RE)
        return has_lp or has_date

    for line in lines:
        if starts_record(line) and current:
            bands.append(_band_from_lines(current, signals))
            current = [line]
        else:
            current.append(line)
    if current:
        bands.append(_band_from_lines(current, signals))

    if not bands:
        return []
    # Without rule lines a band boundary is a weaker signal.
    for band in bands:
        band.confidence = 0.82
    return bands


def _band_from_lines(lines: list[TextLine], signal: str) -> RowBand:
    return RowBand(
        y0=min(line.top for line in lines),
        y1=max(line.bottom for line in lines),
        lines=list(lines),
        confidence=0.82,
        signals=(signal,),
    )


# --------------------------------------------------------------------------------------
# Text assembly
# --------------------------------------------------------------------------------------


def assemble_text(tokens: Sequence[Token], profile: KpirProfile) -> tuple[str, list[str]]:
    """Join tokens by line then X, applying reversible transformations only."""
    if not tokens:
        return "", []
    config = profile.text_assembly
    operations: list[str] = []

    by_line: dict[float, list[Token]] = {}
    for token in tokens:
        key = round(token.baseline, 3)
        by_line.setdefault(key, []).append(token)

    line_texts: list[str] = []
    for baseline in sorted(by_line):
        row = sorted(by_line[baseline], key=lambda t: t.bbox.x0)
        parts: list[str] = []
        for index, token in enumerate(row):
            if index == 0:
                parts.append(token.raw_text)
                continue
            previous = row[index - 1]
            gap = token.bbox.x0 - previous.bbox.x1
            # Two distinct, reversible joins:
            #  * letter spacing: single glyphs separated by a small gap
            #    ("K S I E G A" -> "KSIEGA");
            #  * a mid-word split, where the gap is effectively zero.
            letter_spaced = (
                config.repair_letter_spacing
                and gap <= config.letter_spacing_max_gap
                and len(previous.raw_text) == 1
                and len(token.raw_text) == 1
                and previous.raw_text.isalnum()
                and token.raw_text.isalnum()
            )
            hairline = gap <= config.letter_spacing_max_gap * 0.2
            if letter_spaced or hairline:
                parts[-1] = parts[-1] + token.raw_text
                operation = "letter_spacing_joined" if letter_spaced else "word_split_joined"
                if operation not in operations:
                    operations.append(operation)
            else:
                parts.append(token.raw_text)
        line_texts.append(" ".join(parts))

    text = config.join_lines_with.join(line_texts)
    if config.dehyphenate and "- " in text:
        new_text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
        if new_text != text:
            operations.append("dehyphenated")
            text = new_text
    if config.collapse_whitespace:
        collapsed = re.sub(r"\s+", " ", text).strip()
        if collapsed != text:
            operations.append("whitespace_collapsed")
        text = collapsed
    return text, operations


# --------------------------------------------------------------------------------------
# Page extraction
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class PageExtraction:
    page_number: int
    records: list[KpirRecord]
    issues: list[Issue]
    layout: ColumnLayout
    diagnostics: list[str] = field(default_factory=list)


def extract_page(
    profile: KpirProfile,
    tokens: Sequence[Token],
    horizontal_lines: Sequence[float],
    vertical_lines: Sequence[float],
    *,
    page_number: int,
    document_id: str | None = None,
    profile_score: float = 1.0,
    period_year: int | None = None,
    start_logical_index: int = 0,
    fallback_layout: ColumnLayout | None = None,
) -> PageExtraction:
    """Extract every record visible on a single page.

    ``fallback_layout`` pozwala przenieść pewny układ kolumn z wcześniejszej
    strony. Wiersz numeracji kolumn bywa drukowany tylko na pierwszej stronie
    dokumentu, a kolejne strony mają tę samą siatkę - bez tego spadałyby na
    słabszy detektor i dawały inne granice kolumn niż strona 1.
    """
    issues: list[Issue] = []
    diagnostics: list[str] = []

    layout = detect_column_layout(profile, tokens, vertical_lines)
    if (
        fallback_layout is not None
        and fallback_layout.confidence > layout.confidence
        and len(fallback_layout.boundaries) == len(layout.boundaries)
    ):
        diagnostics.append(
            f"reused the column layout from an earlier page ({fallback_layout.source})"
        )
        layout = fallback_layout
    diagnostics.extend(layout.diagnostics)
    if layout.confidence < 0.7:
        issues.append(
            Issue(
                code=IssueCode.LOW_PROFILE_CONFIDENCE,
                severity=IssueSeverity.WARNING,
                message_key="issue.low_profile_confidence",
                document_id=document_id,
                page_number=page_number,
                details={"layout_source": layout.source, "confidence": layout.confidence},
            )
        )

    lines = group_tokens_into_lines(tokens)
    header_bottom, footer_top = detect_table_zone(profile, lines)
    bands = segment_rows(
        profile,
        lines,
        layout,
        horizontal_lines,
        header_bottom=header_bottom,
        footer_top=footer_top,
    )

    records: list[KpirRecord] = []
    for offset, band in enumerate(bands):
        record, record_issues = _record_from_band(
            profile,
            band,
            layout,
            page_number=page_number,
            document_id=document_id,
            logical_index=start_logical_index + offset,
            profile_score=profile_score,
            period_year=period_year,
        )
        if record is None:
            continue
        records.append(record)
        issues.extend(record_issues)

    return PageExtraction(page_number, records, issues, layout, diagnostics)


def _record_from_band(
    profile: KpirProfile,
    band: RowBand,
    layout: ColumnLayout,
    *,
    page_number: int,
    document_id: str | None,
    logical_index: int,
    profile_score: float,
    period_year: int | None,
) -> tuple[KpirRecord | None, list[Issue]]:
    from ..domain.geometry import assign_token_to_column

    issues: list[Issue] = []
    buckets: dict[str, list[Token]] = {c.key: [] for c in profile.columns}
    crossing = 0

    for token in band.tokens:
        if not token.raw_text.strip():
            continue
        key, _share, crosses = assign_token_to_column(token.bbox, layout.boundaries)
        if key is None:
            continue
        if crosses:
            crossing += 1
        buckets[key].append(token)

    if not any(buckets.values()):
        return None, issues

    # A band without a row number and without a date is header/footer noise.
    lp_tokens = buckets.get(profile.row_number_column, [])
    date_tokens = buckets.get(profile.date_column, [])
    if not lp_tokens and not date_tokens:
        return None, issues

    cells: dict[str, Cell] = {}
    parse_failures = 0
    transformations = 0

    for column in profile.columns:
        column_tokens = buckets.get(column.key, [])
        cell, failed, ops = _build_cell(
            profile,
            column,
            column_tokens,
            page_number=page_number,
            layout_confidence=layout.confidence,
            row_confidence=band.confidence,
            profile_score=profile_score,
            period_year=period_year,
        )
        cells[column.key] = cell
        parse_failures += int(failed)
        transformations += ops
        if failed:
            issues.append(
                Issue(
                    code=(
                        IssueCode.INVALID_MONEY
                        if column.type == ColumnType.MONEY
                        else IssueCode.INVALID_DATE
                        if column.type == ColumnType.DATE
                        else IssueCode.REQUIRED_VALUE_MISSING
                    ),
                    severity=IssueSeverity.CRITICAL,
                    message_key="issue.parse_failed",
                    document_id=document_id,
                    page_number=page_number,
                    cell_id=cell.id,
                    details={"column_key": column.key, "raw_text_length": len(cell.raw_text)},
                )
            )

    if crossing:
        issues.append(
            Issue(
                code=IssueCode.TOKEN_CROSSES_COLUMN,
                severity=IssueSeverity.WARNING,
                message_key="issue.token_crosses_column",
                document_id=document_id,
                page_number=page_number,
                details={"token_count": crossing},
            )
        )

    record = KpirRecord(
        logical_index=logical_index,
        cells=cells,
        source_page_from=page_number,
        source_page_to=page_number,
        confidence=round(
            min((c.confidence.score for c in cells.values() if not c.is_empty), default=1.0), 4
        ),
    )
    for cell in cells.values():
        cell.record_id = record.id
    for issue in issues:
        if issue.record_id is None:
            issue.record_id = record.id
    return record, issues


def _build_cell(
    profile: KpirProfile,
    column: ProfileColumn,
    tokens: Sequence[Token],
    *,
    page_number: int,
    layout_confidence: float,
    row_confidence: float,
    profile_score: float,
    period_year: int | None,
) -> tuple[Cell, bool, int]:
    raw_text, operations = assemble_text(tokens, profile)
    normalized = raw_text.strip()
    empty_markers = profile.empty_markers

    parsed_text: str | None = None
    value_type = ValueType.EMPTY
    failed = False
    parse_score = 1.0

    if normalized and normalized not in empty_markers:
        try:
            if column.type == ColumnType.MONEY:
                money = parse_money(normalized, empty_markers=empty_markers)
                if money is not None:
                    parsed_text = money.as_text()
                    value_type = ValueType.MONEY
            elif column.type == ColumnType.DATE:
                date = parse_business_date(
                    normalized,
                    empty_markers=empty_markers,
                    century_pivot_year=period_year,
                )
                if date is not None:
                    parsed_text = date.as_text()
                    value_type = ValueType.DATE
            elif column.type == ColumnType.INTEGER:
                candidate = normalized.rstrip(".)")
                if candidate.isdigit():
                    parsed_text = str(int(candidate))
                    value_type = ValueType.INTEGER
                else:
                    raise ParseError("INVALID_INTEGER", "not an integer", normalized)
            else:
                parsed_text = normalized
                value_type = ValueType.TEXT
        except ParseError:
            failed = True
            parse_score = 0.2
            parsed_text = None
            value_type = ValueType.EMPTY

    weights = profile.confidence.weights or {
        "profile_match": 0.2,
        "column_boundary": 0.25,
        "row_segmentation": 0.2,
        "type_parse": 0.25,
        "transformations": 0.1,
    }
    transformation_score = max(0.0, 1.0 - 0.15 * len(operations))
    components = {
        "profile_match": profile_score,
        "column_boundary": layout_confidence,
        "row_segmentation": row_confidence,
        "type_parse": parse_score,
        "transformations": transformation_score,
    }
    total_weight = sum(weights.get(k, 0.0) for k in components) or 1.0
    score = sum(components[k] * weights.get(k, 0.0) for k in components) / total_weight

    source = None
    if tokens:
        bbox = tokens[0].bbox
        for token in tokens[1:]:
            bbox = bbox.union(token.bbox)
        source = SourceSpan(page_number=page_number, bbox=bbox)
    elif True:
        # Even an empty cell keeps a traceable position inside its column band.
        source = None

    cell = Cell(
        column_key=column.key,
        raw_text=raw_text,
        normalized_text=normalized,
        parsed_value_text=parsed_text,
        value_type=value_type,
        confidence=Confidence(
            score=round(max(0.0, min(1.0, score)), 4),
            components=tuple(sorted((k, round(v, 4)) for k, v in components.items())),
        ),
        source=source,
        extraction_meta={"operations": operations} if operations else {},
    )
    return cell, failed, len(operations)


def bbox_of(tokens: Sequence[Token]) -> BBox | None:
    if not tokens:
        return None
    bbox = tokens[0].bbox
    for token in tokens[1:]:
        bbox = bbox.union(token.bbox)
    return bbox
