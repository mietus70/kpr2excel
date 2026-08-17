"""Value objects for the KPiR domain.

Rules enforced here:
- money is NEVER a float; it is a ``Decimal`` with scale <= 2 and is serialized
  as canonical decimal text (``"81.30"``);
- an empty value and a zero value are two different states (``None`` vs ``0.00``);
- parsing never silently repairs ambiguous input, it raises ``ParseError`` so the
  caller can create a traceable issue.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

__all__ = [
    "ParseError",
    "Money",
    "BusinessDate",
    "Confidence",
    "ConfidenceBand",
    "BBox",
    "parse_money",
    "parse_business_date",
    "money_to_text",
]


class ParseError(ValueError):
    """Raised when a raw token cannot be parsed unambiguously."""

    def __init__(self, code: str, message: str, raw_text: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_text = raw_text


# --------------------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------------------

_MONEY_MAX_SCALE: Final[int] = 2
# Space-like separators used as thousands separators by accounting software.
_SPACE_CHARS: Final[str] = "\u0020\u00a0\u202f\u2009\u2007"
_MINUS_CHARS: Final[str] = "\u2212\u2013\u2014"

_MONEY_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<sign>[+-]?)(?P<digits>[0-9.,]+)$")


@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount. Always backed by ``Decimal``."""

    amount: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):  # pragma: no cover - defensive
            raise TypeError("Money.amount must be a Decimal")
        if self.amount != self.amount:  # NaN check
            raise ParseError("INVALID_MONEY", "NaN is not a valid amount")
        if -self.amount.as_tuple().exponent > _MONEY_MAX_SCALE:  # type: ignore[operator]
            raise ParseError(
                "MONEY_SCALE_EXCEEDED",
                f"amount has more than {_MONEY_MAX_SCALE} decimal places",
            )

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def as_text(self) -> str:
        """Canonical decimal text with exactly two decimals."""
        return money_to_text(self.amount)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.as_text()


def money_to_text(amount: Decimal) -> str:
    """Canonical storage/transport representation: dot separator, 2 decimals."""
    quantized = amount.quantize(Decimal("0.01"))
    return format(quantized, "f")


def _validate_thousands(digits: str, separator: str | None, raw: str) -> str:
    """Verify that separators group the integer part into blocks of three."""
    if separator is None or separator not in digits:
        if not digits.replace(".", "").replace(",", "").isdigit() and digits != "":
            raise ParseError("INVALID_MONEY", "non numeric integer part", raw)
        return digits.replace(".", "").replace(",", "")
    groups = digits.split(separator)
    if len(groups) < 2:  # pragma: no cover - guarded by the caller
        return digits
    head, *rest = groups
    if head == "" or len(head) > 3 or not head.isdigit():
        raise ParseError("INVALID_MONEY", "invalid thousands grouping", raw)
    for group in rest:
        if len(group) != 3 or not group.isdigit():
            raise ParseError("INVALID_MONEY", "invalid thousands grouping", raw)
    return "".join(groups)


def _strip_spaces(text: str) -> str:
    for ch in _SPACE_CHARS:
        text = text.replace(ch, "")
    return text


def is_empty_marker(raw: str, empty_markers: tuple[str, ...]) -> bool:
    """True when the text is only "empty field" glyphs.

    Must be evaluated on the RAW text, before NFKC normalisation: that mapping
    decomposes some report glyphs (e.g. ``˙`` U+02D9 becomes space + combining
    dot), so a normalised string would never match the configured marker.

    A cell may hold several markers, one per printed sub-column, so the whole
    text counts as empty when every non-space piece is a marker.
    """
    if not empty_markers:
        return False
    text = str(raw).strip()
    if not text:
        return False
    markers = set(empty_markers)
    if text in markers:
        return True
    pieces = text.split()
    if pieces and all(piece in markers for piece in pieces):
        return True
    # Markers printed without separating spaces, e.g. "˙˙".
    distinct = set(text)
    return bool(distinct) and all(char in markers for char in distinct)


def parse_money(raw: str, *, empty_markers: tuple[str, ...] = ()) -> Money | None:
    """Parse a Polish-formatted amount.

    Returns ``None`` for an empty cell (empty string or a profile "empty" glyph).
    Raises :class:`ParseError` for ambiguous or malformed input; the caller turns
    that into an ``INVALID_MONEY`` issue instead of guessing a value.
    """
    if raw is None:
        return None
    # Check markers on the raw text: NFKC would decompose glyphs such as U+02D9.
    if is_empty_marker(raw, empty_markers):
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text == "" or text in empty_markers:
        return None
    for ch in _MINUS_CHARS:
        text = text.replace(ch, "-")
    text = _strip_spaces(text)
    if text == "" or text in empty_markers:
        return None
    # Trailing currency symbols are tolerated but not required.
    text = re.sub(r"(?i)\s*(zł|pln)\.?$", "", text).strip()
    match = _MONEY_RE.match(text)
    if not match:
        raise ParseError("INVALID_MONEY", "unrecognised amount format", raw)

    sign = -1 if match.group("sign") == "-" else 1
    digits = match.group("digits")

    dot_count = digits.count(".")
    comma_count = digits.count(",")

    if comma_count > 1 and dot_count > 1:
        raise ParseError("INVALID_MONEY", "ambiguous separators", raw)

    decimal_sep: str | None
    if comma_count and dot_count:
        # The rightmost separator is the decimal one.
        decimal_sep = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif comma_count:
        decimal_sep = "," if comma_count == 1 else None
    elif dot_count:
        # A single dot with 1-2 trailing digits is decimal; "1.234" is ambiguous
        # (thousands vs decimal) and is rejected rather than guessed.
        if dot_count == 1:
            tail = digits.split(".")[1]
            if len(tail) in (1, 2):
                decimal_sep = "."
            elif len(tail) == 3:
                raise ParseError("INVALID_MONEY", "ambiguous thousands/decimal dot", raw)
            else:
                raise ParseError("INVALID_MONEY", "unexpected decimal length", raw)
        else:
            decimal_sep = None
    else:
        decimal_sep = None

    if decimal_sep is None:
        # Every remaining separator must be a valid thousands separator.
        thousands_sep = "," if comma_count else ("." if dot_count else None)
        integer_part = _validate_thousands(digits, thousands_sep, raw)
        fraction = ""
    else:
        thousands_sep = "." if decimal_sep == "," else ","
        head, _, tail = digits.rpartition(decimal_sep)
        if thousands_sep in tail:
            raise ParseError("INVALID_MONEY", "separator after decimal point", raw)
        integer_part = _validate_thousands(head, thousands_sep, raw)
        fraction = tail

    if not integer_part.isdigit() and integer_part != "":
        raise ParseError("INVALID_MONEY", "non numeric integer part", raw)
    if fraction and not fraction.isdigit():
        raise ParseError("INVALID_MONEY", "non numeric fraction", raw)
    if len(fraction) > _MONEY_MAX_SCALE:
        raise ParseError("MONEY_SCALE_EXCEEDED", "more than two decimal places", raw)

    canonical = f"{integer_part or '0'}.{fraction or '0'}"
    try:
        value = Decimal(canonical) * sign
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ParseError("INVALID_MONEY", "decimal conversion failed", raw) from exc
    return Money(value.quantize(Decimal("0.01")))


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------

_DATE_SEPARATORS: Final[str] = ".-/ "
_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<d>\d{1,2})[.\-/ ](?P<m>\d{1,2})[.\-/ ](?P<y>\d{2}|\d{4})$"
)
_ISO_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$")


@dataclass(frozen=True, slots=True)
class BusinessDate:
    """A business date. Serialized as ISO ``YYYY-MM-DD``."""

    value: _dt.date

    def as_text(self) -> str:
        return self.value.isoformat()

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.as_text()


def parse_business_date(
    raw: str,
    *,
    empty_markers: tuple[str, ...] = (),
    century_pivot_year: int | None = None,
) -> BusinessDate | None:
    """Parse ``DD.MM.RR`` / ``DD.MM.RRRR`` / ISO dates.

    A two-digit year requires an explicit context year (``century_pivot_year``,
    normally the document period year); otherwise the value is ambiguous and a
    :class:`ParseError` is raised instead of guessing the century.
    """
    if raw is None:
        return None
    if is_empty_marker(raw, empty_markers):
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text == "" or text in empty_markers:
        return None
    text = _strip_spaces(text) if text.count(" ") > 2 else text

    iso = _ISO_RE.match(text)
    if iso:
        return _build_date(int(iso["y"]), int(iso["m"]), int(iso["d"]), raw)

    match = _DATE_RE.match(text)
    if not match:
        raise ParseError("INVALID_DATE", "unrecognised date format", raw)
    day, month = int(match["d"]), int(match["m"])
    year_text = match["y"]
    if len(year_text) == 4:
        year = int(year_text)
    else:
        if century_pivot_year is None:
            raise ParseError("INVALID_DATE", "two digit year without period context", raw)
        century = (century_pivot_year // 100) * 100
        year = century + int(year_text)
        # Choose the closest year within +-50 years from the context year.
        if year - century_pivot_year > 50:
            year -= 100
        elif century_pivot_year - year > 50:
            year += 100
    return _build_date(year, month, day, raw)


def _build_date(year: int, month: int, day: int, raw: str) -> BusinessDate:
    try:
        return BusinessDate(_dt.date(year, month, day))
    except ValueError as exc:
        raise ParseError("INVALID_DATE", "date does not exist", raw) from exc


# --------------------------------------------------------------------------------------
# Confidence and geometry
# --------------------------------------------------------------------------------------


class ConfidenceBand:
    HIGH = "high"
    REVIEW = "review"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Confidence:
    """Extraction quality score in ``0..1`` with explaining components."""

    score: float
    components: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError("confidence must be within 0..1")

    def band(self, *, review_below: float, critical_below: float) -> str:
        if self.score < critical_below:
            return ConfidenceBand.CRITICAL
        if self.score < review_below:
            return ConfidenceBand.REVIEW
        return ConfidenceBand.HIGH


@dataclass(frozen=True, slots=True)
class BBox:
    """Rectangle normalised to ``0..1`` relative to the rotated page."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox coordinates must be ordered")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def horizontal_overlap(self, other: BBox) -> float:
        """Overlap length on the X axis (0 when disjoint)."""
        return max(0.0, min(self.x1, other.x1) - max(self.x0, other.x0))

    def vertical_overlap(self, other: BBox) -> float:
        return max(0.0, min(self.y1, other.y1) - max(self.y0, other.y0))

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)
