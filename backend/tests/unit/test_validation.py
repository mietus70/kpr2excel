"""Validation rules: required fields, sums, continuity, duplicates, confidence."""

from __future__ import annotations

import datetime as _dt

import pytest

from kpir_converter.domain.models import (
    Cell,
    IssueSeverity,
    KpirRecord,
    RecordStatus,
    ValueType,
)
from kpir_converter.domain.validation import (
    IssueCode,
    record_status_from,
    validate_record,
    validate_records,
)
from kpir_converter.domain.values import Confidence


def cell(key: str, value: str | None, vtype: ValueType, confidence: float = 1.0) -> Cell:
    return Cell(
        column_key=key,
        raw_text=value or "",
        normalized_text=value or "",
        parsed_value_text=value,
        value_type=vtype if value is not None else ValueType.EMPTY,
        confidence=Confidence(confidence),
    )


def record(**values: Cell) -> KpirRecord:
    return KpirRecord(logical_index=0, cells=dict(values), source_page_from=1, source_page_to=1)


def complete_record(**overrides: Cell) -> KpirRecord:
    cells = {
        "row_number": cell("row_number", "1", ValueType.INTEGER),
        "business_date": cell("business_date", "2018-01-04", ValueType.DATE),
        "evidence_number": cell("evidence_number", "FV/1", ValueType.TEXT),
    }
    cells.update(overrides)
    return record(**cells)


def codes(issues) -> set[str]:
    return {i.code for i in issues}


class TestRequiredFields:
    def test_missing_required_is_critical(self, profile) -> None:
        rec = complete_record(evidence_number=cell("evidence_number", None, ValueType.EMPTY))
        issues = validate_record(rec, profile)
        assert IssueCode.REQUIRED_VALUE_MISSING in codes(issues)
        assert any(i.severity is IssueSeverity.CRITICAL for i in issues)

    def test_complete_record_is_clean(self, profile) -> None:
        assert validate_record(complete_record(), profile) == []

    def test_optional_empty_is_fine(self, profile) -> None:
        rec = complete_record(notes=cell("notes", None, ValueType.EMPTY))
        assert validate_record(rec, profile) == []


class TestMoneyRules:
    def test_negative_amount_warns(self, profile) -> None:
        rec = complete_record(total_expenses=cell("total_expenses", "-5.00", ValueType.MONEY))
        issues = validate_record(rec, profile)
        assert IssueCode.NEGATIVE_MONEY in codes(issues)

    def test_scale_exceeded_is_critical(self, profile) -> None:
        rec = complete_record(total_expenses=cell("total_expenses", "5.123", ValueType.MONEY))
        issues = validate_record(rec, profile)
        assert IssueCode.MONEY_SCALE_EXCEEDED in codes(issues)

    def test_unparseable_money_is_critical(self, profile) -> None:
        broken = cell("total_expenses", "abc", ValueType.MONEY)
        rec = complete_record(total_expenses=broken)
        issues = validate_record(rec, profile)
        assert IssueCode.INVALID_MONEY in codes(issues)

    def test_zero_is_valid(self, profile) -> None:
        rec = complete_record(total_expenses=cell("total_expenses", "0.00", ValueType.MONEY))
        assert validate_record(rec, profile) == []


class TestSumRules:
    def test_income_mismatch_creates_issue_without_overwriting(self, profile) -> None:
        rec = complete_record(
            income_goods_services=cell("income_goods_services", "100.00", ValueType.MONEY),
            other_income=cell("other_income", "50.00", ValueType.MONEY),
            total_income=cell("total_income", "200.00", ValueType.MONEY),  # wrong on purpose
        )
        issues = validate_record(rec, profile)
        assert IssueCode.INCOME_TOTAL_MISMATCH in codes(issues)
        # The source value must survive untouched.
        assert rec.cell("total_income").parsed_value_text == "200.00"
        detail = next(i for i in issues if i.code == IssueCode.INCOME_TOTAL_MISMATCH)
        assert detail.details["expected"] == "150.00"
        assert detail.details["found"] == "200.00"

    def test_matching_sum_is_silent(self, profile) -> None:
        rec = complete_record(
            income_goods_services=cell("income_goods_services", "100.00", ValueType.MONEY),
            other_income=cell("other_income", "50.00", ValueType.MONEY),
            total_income=cell("total_income", "150.00", ValueType.MONEY),
        )
        assert validate_record(rec, profile) == []

    def test_expense_mismatch_detected(self, profile) -> None:
        rec = complete_record(
            goods_materials_purchase=cell("goods_materials_purchase", "10.00", ValueType.MONEY),
            remuneration=cell("remuneration", "20.00", ValueType.MONEY),
            total_expenses=cell("total_expenses", "99.00", ValueType.MONEY),
        )
        assert IssueCode.EXPENSE_TOTAL_MISMATCH in codes(validate_record(rec, profile))

    def test_missing_components_skip_the_rule(self, profile) -> None:
        rec = complete_record(total_income=cell("total_income", "150.00", ValueType.MONEY))
        assert validate_record(rec, profile) == []


class TestDates:
    def test_date_before_period_warns(self, profile) -> None:
        rec = complete_record(business_date=cell("business_date", "2017-12-31", ValueType.DATE))
        issues = validate_record(
            rec, profile, period_from=_dt.date(2018, 1, 1), period_to=_dt.date(2018, 1, 31)
        )
        assert IssueCode.DATE_OUTSIDE_PERIOD in codes(issues)

    def test_date_after_period_warns(self, profile) -> None:
        rec = complete_record(business_date=cell("business_date", "2018-02-05", ValueType.DATE))
        issues = validate_record(
            rec, profile, period_from=_dt.date(2018, 1, 1), period_to=_dt.date(2018, 1, 31)
        )
        assert IssueCode.DATE_OUTSIDE_PERIOD in codes(issues)

    def test_date_inside_period_is_clean(self, profile) -> None:
        issues = validate_record(
            complete_record(),
            profile,
            period_from=_dt.date(2018, 1, 1),
            period_to=_dt.date(2018, 1, 31),
        )
        assert issues == []

    def test_malformed_date_is_critical(self, profile) -> None:
        rec = complete_record(business_date=cell("business_date", "not-a-date", ValueType.DATE))
        assert IssueCode.INVALID_DATE in codes(validate_record(rec, profile))


class TestConfidenceBands:
    def test_low_confidence_warns(self, profile) -> None:
        rec = complete_record(
            contractor_name=cell("contractor_name", "X", ValueType.TEXT, confidence=0.7)
        )
        issues = validate_record(rec, profile)
        assert IssueCode.LOW_CELL_CONFIDENCE in codes(issues)
        assert all(i.severity is IssueSeverity.WARNING for i in issues)

    def test_very_low_confidence_is_critical(self, profile) -> None:
        rec = complete_record(
            contractor_name=cell("contractor_name", "X", ValueType.TEXT, confidence=0.3)
        )
        issues = validate_record(rec, profile)
        assert any(i.severity is IssueSeverity.CRITICAL for i in issues)

    def test_manual_cells_skip_confidence_check(self, profile) -> None:
        manual = cell("contractor_name", "X", ValueType.TEXT, confidence=0.1)
        manual.is_manual = True
        assert validate_record(complete_record(contractor_name=manual), profile) == []


class TestCrossRecordRules:
    def _numbered(self, numbers: list[int]) -> list[KpirRecord]:
        records = []
        for index, number in enumerate(numbers):
            rec = KpirRecord(
                logical_index=index,
                cells={
                    "row_number": cell("row_number", str(number), ValueType.INTEGER),
                    "business_date": cell("business_date", "2018-01-04", ValueType.DATE),
                    "evidence_number": cell("evidence_number", f"FV/{number}", ValueType.TEXT),
                },
                source_page_from=1,
                source_page_to=1,
            )
            records.append(rec)
        return records

    def test_gap_is_a_warning_not_an_error(self, profile) -> None:
        issues = validate_records(self._numbered([1, 2, 5]), profile)
        gaps = [i for i in issues if i.code == IssueCode.ROW_NUMBER_GAP]
        assert len(gaps) == 1
        assert gaps[0].severity is IssueSeverity.WARNING
        assert gaps[0].details == {"previous": 2, "current": 5}

    def test_continuous_numbering_is_clean(self, profile) -> None:
        issues = validate_records(self._numbered([1, 2, 3]), profile)
        assert not [i for i in issues if i.code == IssueCode.ROW_NUMBER_GAP]

    def test_duplicates_are_flagged_not_removed(self, profile) -> None:
        records = self._numbered([1, 2])
        # Make the second record identical on the signature columns.
        records[1].cells["evidence_number"] = cell("evidence_number", "FV/1", ValueType.TEXT)
        issues = validate_records(records, profile)
        duplicates = [i for i in issues if i.code == IssueCode.POSSIBLE_DUPLICATE]
        assert len(duplicates) == 1
        assert duplicates[0].details["duplicate_of_record_id"] == records[0].id
        assert len(records) == 2  # nothing was deleted


class TestRecordStatus:
    def test_status_from_issues(self) -> None:
        from kpir_converter.domain.models import Issue

        def issue(severity: IssueSeverity) -> Issue:
            return Issue(code="X", severity=severity, message_key="x")

        assert record_status_from([]) is RecordStatus.OK
        assert record_status_from([issue(IssueSeverity.INFO)]) is RecordStatus.OK
        assert record_status_from([issue(IssueSeverity.WARNING)]) is RecordStatus.REVIEW
        assert (
            record_status_from([issue(IssueSeverity.WARNING), issue(IssueSeverity.CRITICAL)])
            is RecordStatus.CRITICAL
        )


class TestProfileSchema:
    def test_overlapping_columns_rejected(self) -> None:
        from kpir_converter.domain.profiles import ProfileError, profile_from_dict

        with pytest.raises(ProfileError, match="overlap"):
            profile_from_dict(
                {
                    "id": "x",
                    "version": 1,
                    "columns": [
                        {"key": "a", "type": "text", "x0": 0.0, "x1": 0.5},
                        {"key": "b", "type": "text", "x0": 0.3, "x1": 0.9},
                    ],
                }
            )

    def test_duplicate_keys_rejected(self) -> None:
        from kpir_converter.domain.profiles import ProfileError, profile_from_dict

        with pytest.raises(ProfileError, match="duplicate"):
            profile_from_dict(
                {
                    "id": "x",
                    "version": 1,
                    "columns": [
                        {"key": "a", "type": "text", "x0": 0.0, "x1": 0.4},
                        {"key": "a", "type": "text", "x0": 0.4, "x1": 0.9},
                    ],
                }
            )

    def test_unknown_column_type_rejected(self) -> None:
        from kpir_converter.domain.profiles import ProfileError, profile_from_dict

        with pytest.raises(ProfileError, match="unsupported type"):
            profile_from_dict(
                {
                    "id": "x",
                    "version": 1,
                    "columns": [{"key": "a", "type": "wat", "x0": 0.0, "x1": 0.4}],
                }
            )

    def test_sum_rule_with_unknown_column_rejected(self) -> None:
        from kpir_converter.domain.profiles import ProfileError, profile_from_dict

        with pytest.raises(ProfileError, match="unknown column"):
            profile_from_dict(
                {
                    "id": "x",
                    "version": 1,
                    "columns": [{"key": "a", "type": "money", "x0": 0.0, "x1": 0.4}],
                    "sum_rules": [{"target": "a", "components": ["ghost"]}],
                }
            )

    def test_bad_coordinates_rejected(self) -> None:
        from kpir_converter.domain.profiles import ProfileError, profile_from_dict

        with pytest.raises(ProfileError):
            profile_from_dict(
                {
                    "id": "x",
                    "version": 1,
                    "columns": [{"key": "a", "type": "text", "x0": 0.9, "x1": 0.2}],
                }
            )

    def test_real_profile_is_consistent(self, profile) -> None:
        assert profile.qualified_id == "kpir_pl_2018@1"
        # Wzór KPiR ma 17 kolumn: 15 jest "wolna", 16 to koszty B+R, 17 to uwagi.
        assert len(profile.columns) == 17
        assert [c.form_number for c in profile.columns] == [str(n) for n in range(1, 18)]
        assert profile.column("free_column").form_number == "15"
        assert profile.column("research_development_costs").form_number == "16"
        assert profile.column("notes").form_number == "17"
        assert profile.column("total_expenses").is_money
        assert profile.default_filter_column == "total_expenses"
        assert set(profile.export_columns_default()) <= set(profile.column_keys)
        # Columns must tile the row without overlapping.
        ordered = sorted(profile.columns, key=lambda c: c.x0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            assert right.x0 >= left.x1 - 1e-9

    def test_registry_resolves_latest_version(self, profile) -> None:
        from kpir_converter.domain.profiles import ProfileError, ProfileRegistry

        registry = ProfileRegistry()
        registry.add(profile)
        assert registry.get("kpir_pl_2018").version == 1
        assert registry.get("kpir_pl_2018@1") is profile
        with pytest.raises(ProfileError):
            registry.get("nope")
