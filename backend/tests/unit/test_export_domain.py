"""Export projection: columns, row selection and inclusive money bounds."""

from __future__ import annotations

import pytest

from kpir_converter.domain.export import (
    ExportValidationError,
    project_row,
    record_matches_filters,
    select_records,
    validate_definition,
)
from kpir_converter.domain.models import (
    Cell,
    ExportDefinition,
    ExportScope,
    KpirRecord,
    MoneyRangeFilter,
    RowSelection,
    RowSelectionMode,
    ValueType,
)
from kpir_converter.domain.values import Confidence


def make_record(index: int, values: dict[str, str | None], record_id: str | None = None):
    cells: dict[str, Cell] = {}
    for key, value in values.items():
        if key in ("total_expenses", "total_income", "remuneration", "other_expenses"):
            value_type = ValueType.MONEY if value is not None else ValueType.EMPTY
        elif key == "business_date":
            value_type = ValueType.DATE if value is not None else ValueType.EMPTY
        else:
            value_type = ValueType.TEXT if value is not None else ValueType.EMPTY
        cells[key] = Cell(
            column_key=key,
            raw_text=value or "",
            normalized_text=value or "",
            parsed_value_text=value,
            value_type=value_type,
            confidence=Confidence(1.0),
        )
    record = KpirRecord(logical_index=index, cells=cells, source_page_from=1, source_page_to=1)
    if record_id:
        record.id = record_id
    return record


@pytest.fixture()
def records():
    return [
        make_record(0, {"total_expenses": "50.00", "evidence_number": "A"}, "r0"),
        make_record(1, {"total_expenses": "100.00", "evidence_number": "B"}, "r1"),
        make_record(2, {"total_expenses": "500.00", "evidence_number": "C"}, "r2"),
        make_record(3, {"total_expenses": "1000.00", "evidence_number": "D"}, "r3"),
        make_record(4, {"total_expenses": "1500.00", "evidence_number": "E"}, "r4"),
        make_record(5, {"total_expenses": None, "evidence_number": "F"}, "r5"),
    ]


def definition(**kwargs) -> ExportDefinition:
    base = {
        "scope": ExportScope(type="documents", document_ids=("doc",)),
        "column_keys": ("business_date", "total_expenses"),
    }
    base.update(kwargs)
    return ExportDefinition(**base)


class TestMoneyBounds:
    def test_bounds_are_inclusive_by_default(self, records) -> None:
        d = definition(
            filters=(MoneyRangeFilter("total_expenses", min_text="100.00", max_text="1000.00"),)
        )
        result = select_records(records, d)
        assert [r.id for r in result.records] == ["r1", "r2", "r3"]

    def test_only_min_bound(self, records) -> None:
        d = definition(filters=(MoneyRangeFilter("total_expenses", min_text="1000.00"),))
        assert [r.id for r in select_records(records, d).records] == ["r3", "r4"]

    def test_only_max_bound(self, records) -> None:
        d = definition(filters=(MoneyRangeFilter("total_expenses", max_text="100.00"),))
        assert [r.id for r in select_records(records, d).records] == ["r0", "r1"]

    def test_exclusive_bounds(self, records) -> None:
        d = definition(
            filters=(
                MoneyRangeFilter(
                    "total_expenses", min_text="100.00", max_text="1000.00", bounds="exclusive"
                ),
            )
        )
        assert [r.id for r in select_records(records, d).records] == ["r2"]

    def test_empty_amount_never_matches_active_filter(self, records) -> None:
        d = definition(filters=(MoneyRangeFilter("total_expenses", min_text="0.00"),))
        result = select_records(records, d)
        assert "r5" not in [r.id for r in result.records]
        assert result.excluded_for_null_or_invalid == 1

    def test_no_filter_keeps_empty_amount(self, records) -> None:
        result = select_records(records, definition())
        assert len(result.records) == 6

    def test_exact_decimal_comparison(self) -> None:
        rows = [make_record(0, {"total_expenses": "0.10"}, "a")]
        d = definition(
            filters=(MoneyRangeFilter("total_expenses", min_text="0.10", max_text="0.10"),)
        )
        assert len(select_records(rows, d).records) == 1

    def test_filters_combine_with_and(self) -> None:
        rows = [
            make_record(0, {"total_expenses": "500.00", "total_income": "100.00"}, "a"),
            make_record(1, {"total_expenses": "500.00", "total_income": "900.00"}, "b"),
        ]
        d = definition(
            filters=(
                MoneyRangeFilter("total_expenses", min_text="100.00"),
                MoneyRangeFilter("total_income", min_text="500.00"),
            )
        )
        assert [r.id for r in select_records(rows, d).records] == ["b"]


class TestRowSelection:
    def test_all_matching_minus_exclusions(self, records) -> None:
        d = definition(
            row_selection=RowSelection(
                mode=RowSelectionMode.ALL_MATCHING, excluded_record_ids=("r1", "r3")
            )
        )
        assert [r.id for r in select_records(records, d).records] == ["r0", "r2", "r4", "r5"]

    def test_explicit_intersects_with_filter(self, records) -> None:
        d = definition(
            row_selection=RowSelection(
                mode=RowSelectionMode.EXPLICIT, included_record_ids=("r0", "r2", "r4")
            ),
            filters=(MoneyRangeFilter("total_expenses", min_text="100.00", max_text="1000.00"),),
        )
        # r0 (50) and r4 (1500) fall outside the filter, only r2 survives.
        assert [r.id for r in select_records(records, d).records] == ["r2"]

    def test_filters_apply_before_selection(self, records) -> None:
        d = definition(
            row_selection=RowSelection(
                mode=RowSelectionMode.ALL_MATCHING, excluded_record_ids=("r2",)
            ),
            filters=(MoneyRangeFilter("total_expenses", min_text="100.00"),),
        )
        assert [r.id for r in select_records(records, d).records] == ["r1", "r3", "r4"]

    def test_source_order_is_preserved(self, records) -> None:
        shuffled = [records[3], records[0], records[2]]
        d = definition(
            row_selection=RowSelection(
                mode=RowSelectionMode.EXPLICIT, included_record_ids=("r0", "r2", "r3")
            )
        )
        assert [r.id for r in select_records(shuffled, d).records] == ["r3", "r0", "r2"]


class TestValidation:
    def test_min_greater_than_max_is_rejected(self, profile) -> None:
        d = definition(
            filters=(MoneyRangeFilter("total_expenses", min_text="1000.00", max_text="100.00"),)
        )
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "MIN_GREATER_THAN_MAX"

    def test_no_columns_rejected(self, profile) -> None:
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(definition(column_keys=()), profile)
        assert exc.value.code == "NO_COLUMNS_SELECTED"

    def test_unknown_column_rejected(self, profile) -> None:
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(definition(column_keys=("nope",)), profile)
        assert exc.value.code == "UNKNOWN_COLUMN"

    def test_filter_on_text_column_rejected(self, profile) -> None:
        d = definition(filters=(MoneyRangeFilter("evidence_number", min_text="1.00"),))
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "FILTER_COLUMN_TYPE_MISMATCH"

    def test_filter_without_bounds_rejected(self, profile) -> None:
        d = definition(filters=(MoneyRangeFilter("total_expenses"),))
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "EMPTY_FILTER"

    def test_invalid_bound_rejected(self, profile) -> None:
        d = definition(filters=(MoneyRangeFilter("total_expenses", min_text="abc"),))
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "INVALID_FILTER_BOUND"

    def test_duplicate_column_rejected(self, profile) -> None:
        d = definition(column_keys=("total_expenses", "total_expenses"))
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "DUPLICATE_COLUMN"

    def test_explicit_without_ids_rejected(self, profile) -> None:
        d = definition(row_selection=RowSelection(mode=RowSelectionMode.EXPLICIT))
        with pytest.raises(ExportValidationError) as exc:
            validate_definition(d, profile)
        assert exc.value.code == "NO_ROWS_SELECTED"

    def test_filter_column_need_not_be_exported(self, profile, records) -> None:
        """Filter by total_expenses but do not export that column."""
        d = definition(
            column_keys=("evidence_number",),
            filters=(MoneyRangeFilter("total_expenses", min_text="100.00", max_text="1000.00"),),
        )
        validate_definition(d, profile)  # must not raise
        result = select_records(records, d)
        assert [r.id for r in result.records] == ["r1", "r2", "r3"]
        row = project_row(result.records[0], d.column_keys)
        assert [c[0] for c in row] == ["evidence_number"]


class TestProjection:
    def test_column_order_follows_selection(self, records) -> None:
        row = project_row(records[0], ("total_expenses", "evidence_number"))
        assert [c[0] for c in row] == ["total_expenses", "evidence_number"]

    def test_missing_column_projects_as_empty(self, records) -> None:
        row = project_row(records[0], ("notes",))
        assert row[0] == ("notes", ValueType.EMPTY, None)


class TestEffectiveValues:
    def test_filter_uses_corrected_value(self) -> None:
        """A manually corrected amount drives filtering, not the extracted one."""
        record = make_record(0, {"total_expenses": "2000.00"}, "x")
        record.cells["total_expenses"].is_manual = True
        record.cells["total_expenses"].extraction_meta["extracted_value"] = "20.00"
        matches, _ = record_matches_filters(
            record, [MoneyRangeFilter("total_expenses", min_text="1000.00")]
        )
        assert matches is True
