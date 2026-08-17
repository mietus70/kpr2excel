"""Export projection domain logic.

Key invariants (see AGENTS.md §3 items 10-13):
- exporting never mutates extraction results or corrections;
- filters use *effective* values (manual correction wins over extraction);
- money bounds are exact ``Decimal`` comparisons and inclusive by default;
- an empty or invalid amount never satisfies an active money filter;
- filters are applied first, then the manual row selection is applied:
  ``EXPLICIT`` intersects with the included ids, ``ALL_MATCHING`` subtracts
  the excluded ids.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import (
    ExportDefinition,
    KpirRecord,
    MoneyRangeFilter,
    RowSelectionMode,
    ValueType,
)
from .profiles import ColumnType, KpirProfile

__all__ = [
    "ExportValidationError",
    "FilterOutcome",
    "validate_definition",
    "effective_decimal",
    "record_matches_filters",
    "select_records",
    "project_row",
]


class ExportValidationError(ValueError):
    """Raised for a definition that must not produce a file."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(slots=True)
class FilterOutcome:
    """Result of applying filters + selection to a record sequence."""

    records: list[KpirRecord]
    excluded_for_null_or_invalid: int = 0
    excluded_by_range: int = 0
    excluded_by_selection: int = 0


def _parse_bound(text: str | None, field: str) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        value = Decimal(str(text).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ExportValidationError(
            "INVALID_FILTER_BOUND", f"'{text}' is not a valid decimal amount", field
        ) from exc
    if value != value:  # NaN
        raise ExportValidationError("INVALID_FILTER_BOUND", "NaN bound", field)
    return value


def validate_definition(definition: ExportDefinition, profile: KpirProfile) -> None:
    """Server-side validation. Never trust what the frontend hid."""
    if not definition.column_keys:
        raise ExportValidationError(
            "NO_COLUMNS_SELECTED", "at least one column must be selected", "columnKeys"
        )
    seen: set[str] = set()
    for key in definition.column_keys:
        if not profile.has_column(key):
            raise ExportValidationError(
                "UNKNOWN_COLUMN", f"column '{key}' does not exist in the profile", "columnKeys"
            )
        if key in seen:
            raise ExportValidationError(
                "DUPLICATE_COLUMN", f"column '{key}' selected twice", "columnKeys"
            )
        seen.add(key)

    for index, flt in enumerate(definition.filters):
        field = f"filters[{index}]"
        if flt.type != "money_range":
            raise ExportValidationError(
                "UNSUPPORTED_FILTER", f"unsupported filter type '{flt.type}'", field
            )
        if not profile.has_column(flt.column_key):
            raise ExportValidationError(
                "UNKNOWN_FILTER_COLUMN",
                f"filter column '{flt.column_key}' does not exist in the profile",
                field,
            )
        column = profile.column(flt.column_key)
        if column.type != ColumnType.MONEY:
            raise ExportValidationError(
                "FILTER_COLUMN_TYPE_MISMATCH",
                f"column '{flt.column_key}' is not a money column",
                field,
            )
        if flt.bounds not in ("inclusive", "exclusive"):
            raise ExportValidationError("INVALID_BOUNDS", f"unknown bounds '{flt.bounds}'", field)
        low = _parse_bound(flt.min_text, f"{field}.min")
        high = _parse_bound(flt.max_text, f"{field}.max")
        if low is None and high is None:
            raise ExportValidationError(
                "EMPTY_FILTER", "a money range filter needs at least one bound", field
            )
        if low is not None and high is not None and low > high:
            raise ExportValidationError(
                "MIN_GREATER_THAN_MAX", "filter minimum is greater than maximum", field
            )

    selection = definition.row_selection
    if selection.mode is RowSelectionMode.EXPLICIT and not selection.included_record_ids:
        raise ExportValidationError(
            "NO_ROWS_SELECTED", "EXPLICIT selection requires at least one record", "rowSelection"
        )


def effective_decimal(record: KpirRecord, column_key: str) -> Decimal | None:
    """Effective money value of a cell (manual correction already applied upstream)."""
    cell = record.cell(column_key)
    if cell is None or cell.parsed_value_text is None:
        return None
    if cell.value_type is not ValueType.MONEY:
        return None
    try:
        return Decimal(cell.parsed_value_text)
    except (InvalidOperation, ValueError):
        return None


def _matches_single(record: KpirRecord, flt: MoneyRangeFilter) -> tuple[bool, str | None]:
    """Return (matches, rejection_reason)."""
    value = effective_decimal(record, flt.column_key)
    if value is None:
        # Empty or invalid amount never satisfies an active money filter.
        return False, "null_or_invalid"
    low = _parse_bound(flt.min_text, "min")
    high = _parse_bound(flt.max_text, "max")
    inclusive = flt.bounds == "inclusive"
    if low is not None:
        if inclusive and value < low:
            return False, "range"
        if not inclusive and value <= low:
            return False, "range"
    if high is not None:
        if inclusive and value > high:
            return False, "range"
        if not inclusive and value >= high:
            return False, "range"
    return True, None


def record_matches_filters(
    record: KpirRecord, filters: Sequence[MoneyRangeFilter]
) -> tuple[bool, str | None]:
    """Filters are combined with AND."""
    reason: str | None = None
    for flt in filters:
        ok, why = _matches_single(record, flt)
        if not ok:
            # "null_or_invalid" is the more informative reason for the preview.
            reason = why if reason is None or why == "null_or_invalid" else reason
            return False, reason
    return True, None


def select_records(
    records: Iterable[KpirRecord],
    definition: ExportDefinition,
) -> FilterOutcome:
    """Apply filters then the manual row selection, preserving source order."""
    outcome = FilterOutcome(records=[])
    selection = definition.row_selection
    included = set(selection.included_record_ids)
    excluded = set(selection.excluded_record_ids)

    for record in records:
        matches, reason = record_matches_filters(record, definition.filters)
        if not matches:
            if reason == "null_or_invalid":
                outcome.excluded_for_null_or_invalid += 1
            else:
                outcome.excluded_by_range += 1
            continue
        if selection.mode is RowSelectionMode.EXPLICIT:
            if record.id not in included:
                outcome.excluded_by_selection += 1
                continue
        elif record.id in excluded:
            outcome.excluded_by_selection += 1
            continue
        outcome.records.append(record)
    return outcome


def project_row(
    record: KpirRecord, column_keys: Sequence[str]
) -> list[tuple[str, ValueType, str | None]]:
    """Project a record onto the chosen columns, in the chosen order."""
    row: list[tuple[str, ValueType, str | None]] = []
    for key in column_keys:
        cell = record.cell(key)
        if cell is None:
            row.append((key, ValueType.EMPTY, None))
        else:
            row.append((key, cell.value_type, cell.parsed_value_text))
    return row
