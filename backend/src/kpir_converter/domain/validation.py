"""KPiR validation rules.

Validation runs after extraction and after every user correction. It never
rewrites a source value: a mismatch always becomes an :class:`Issue`.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Sequence
from decimal import Decimal

from .models import Cell, Issue, IssueSeverity, KpirRecord, RecordStatus, ValueType
from .profiles import ColumnType, KpirProfile

__all__ = [
    "IssueCode",
    "validate_record",
    "validate_records",
    "record_status_from",
]


class IssueCode:
    REQUIRED_VALUE_MISSING = "REQUIRED_VALUE_MISSING"
    INVALID_DATE = "INVALID_DATE"
    DATE_OUTSIDE_PERIOD = "DATE_OUTSIDE_PERIOD"
    INVALID_MONEY = "INVALID_MONEY"
    MONEY_SCALE_EXCEEDED = "MONEY_SCALE_EXCEEDED"
    NEGATIVE_MONEY = "NEGATIVE_MONEY"
    INCOME_TOTAL_MISMATCH = "INCOME_TOTAL_MISMATCH"
    EXPENSE_TOTAL_MISMATCH = "EXPENSE_TOTAL_MISMATCH"
    ROW_NUMBER_GAP = "ROW_NUMBER_GAP"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    TOKEN_CROSSES_COLUMN = "TOKEN_CROSSES_COLUMN"
    LOW_PROFILE_CONFIDENCE = "LOW_PROFILE_CONFIDENCE"
    POSSIBLE_ROW_CONTINUATION = "POSSIBLE_ROW_CONTINUATION"
    OCR_REQUIRED = "OCR_REQUIRED"
    LOW_CELL_CONFIDENCE = "LOW_CELL_CONFIDENCE"


_SEVERITY = {
    "info": IssueSeverity.INFO,
    "warning": IssueSeverity.WARNING,
    "critical": IssueSeverity.CRITICAL,
}


def _decimal_of(cell: Cell | None) -> Decimal | None:
    if cell is None or cell.parsed_value_text is None:
        return None
    if cell.value_type is not ValueType.MONEY:
        return None
    try:
        return Decimal(cell.parsed_value_text)
    except Exception:  # noqa: BLE001 - malformed stored value handled as missing
        return None


def validate_record(
    record: KpirRecord,
    profile: KpirProfile,
    *,
    document_id: str | None = None,
    period_from: _dt.date | None = None,
    period_to: _dt.date | None = None,
) -> list[Issue]:
    """Validate a single record against the profile. Returns new issues."""
    issues: list[Issue] = []

    def add(
        code: str, severity: IssueSeverity, cell: Cell | None = None, **details: object
    ) -> None:
        issues.append(
            Issue(
                code=code,
                severity=severity,
                message_key=f"issue.{code.lower()}",
                document_id=document_id,
                record_id=record.id,
                cell_id=cell.id if cell else None,
                page_number=(cell.source.page_number if cell and cell.source else None),
                details=dict(details),
            )
        )

    # 1. Required values -----------------------------------------------------
    for column in profile.required_columns():
        cell = record.cell(column.key)
        if cell is None or cell.is_empty:
            add(
                IssueCode.REQUIRED_VALUE_MISSING,
                IssueSeverity.CRITICAL,
                cell,
                column_key=column.key,
            )

    # 2. Per-cell type checks ------------------------------------------------
    for column in profile.columns:
        cell = record.cell(column.key)
        if cell is None or cell.is_empty:
            continue
        if column.type == ColumnType.MONEY:
            value = _decimal_of(cell)
            if value is None:
                add(IssueCode.INVALID_MONEY, IssueSeverity.CRITICAL, cell, column_key=column.key)
                continue
            if -value.as_tuple().exponent > 2:  # type: ignore[operator]
                add(
                    IssueCode.MONEY_SCALE_EXCEEDED,
                    IssueSeverity.CRITICAL,
                    cell,
                    column_key=column.key,
                )
            if value < 0:
                add(IssueCode.NEGATIVE_MONEY, IssueSeverity.WARNING, cell, column_key=column.key)
        elif column.type == ColumnType.DATE:
            parsed = _as_date(cell)
            if parsed is None:
                add(IssueCode.INVALID_DATE, IssueSeverity.CRITICAL, cell, column_key=column.key)
            elif period_from and parsed < period_from:
                add(
                    IssueCode.DATE_OUTSIDE_PERIOD,
                    IssueSeverity.WARNING,
                    cell,
                    column_key=column.key,
                    period_from=period_from.isoformat(),
                )
            elif period_to and parsed > period_to:
                add(
                    IssueCode.DATE_OUTSIDE_PERIOD,
                    IssueSeverity.WARNING,
                    cell,
                    column_key=column.key,
                    period_to=period_to.isoformat(),
                )

    # 3. Sum rules (validation only, never overwrite the source value) -------
    for rule in profile.sum_rules:
        target_cell = record.cell(rule.target)
        target = _decimal_of(target_cell)
        if target is None:
            continue
        components = [_decimal_of(record.cell(key)) for key in rule.components]
        if all(c is None for c in components):
            continue
        expected = sum((c for c in components if c is not None), Decimal("0.00"))
        if abs(expected - target) > rule.tolerance:
            add(
                rule.code,
                _SEVERITY.get(rule.severity, IssueSeverity.WARNING),
                target_cell,
                column_key=rule.target,
                expected=str(expected),
                found=str(target),
            )

    # 4. Confidence bands ----------------------------------------------------
    thresholds = profile.confidence
    for column in profile.columns:
        cell = record.cell(column.key)
        if cell is None or cell.is_empty:
            continue
        if cell.is_manual:
            continue
        if cell.confidence.score < thresholds.critical_below:
            add(
                IssueCode.LOW_CELL_CONFIDENCE,
                IssueSeverity.CRITICAL,
                cell,
                column_key=column.key,
                confidence=round(cell.confidence.score, 4),
            )
        elif cell.confidence.score < thresholds.review_below:
            add(
                IssueCode.LOW_CELL_CONFIDENCE,
                IssueSeverity.WARNING,
                cell,
                column_key=column.key,
                confidence=round(cell.confidence.score, 4),
            )

    return issues


def _as_date(cell: Cell | None) -> _dt.date | None:
    if cell is None or cell.parsed_value_text is None:
        return None
    try:
        return _dt.date.fromisoformat(cell.parsed_value_text)
    except ValueError:
        return None


def validate_records(
    records: Sequence[KpirRecord],
    profile: KpirProfile,
    *,
    document_id: str | None = None,
    period_from: _dt.date | None = None,
    period_to: _dt.date | None = None,
) -> list[Issue]:
    """Validate a batch of records, including cross-record rules."""
    issues: list[Issue] = []
    for record in records:
        issues.extend(
            validate_record(
                record,
                profile,
                document_id=document_id,
                period_from=period_from,
                period_to=period_to,
            )
        )

    issues.extend(_row_number_continuity(records, profile, document_id))
    issues.extend(_possible_duplicates(records, profile, document_id))
    return issues


def _row_number_continuity(
    records: Sequence[KpirRecord], profile: KpirProfile, document_id: str | None
) -> list[Issue]:
    """Gaps in the ordinal number are a warning, never a hard error."""
    issues: list[Issue] = []
    previous: int | None = None
    for record in records:
        cell = record.cell(profile.row_number_column)
        if cell is None or cell.parsed_value_text is None:
            previous = None
            continue
        try:
            current = int(cell.parsed_value_text)
        except ValueError:
            previous = None
            continue
        if previous is not None and current != previous + 1:
            issues.append(
                Issue(
                    code=IssueCode.ROW_NUMBER_GAP,
                    severity=IssueSeverity.WARNING,
                    message_key="issue.row_number_gap",
                    document_id=document_id,
                    record_id=record.id,
                    cell_id=cell.id,
                    details={"previous": previous, "current": current},
                )
            )
        previous = current
    return issues


def _possible_duplicates(
    records: Sequence[KpirRecord], profile: KpirProfile, document_id: str | None
) -> list[Issue]:
    """Flag likely duplicates. Nothing is removed automatically."""
    seen: dict[tuple[str, ...], str] = {}
    issues: list[Issue] = []
    key_columns = [
        profile.date_column,
        "evidence_number",
        "total_income",
        "total_expenses",
    ]
    for record in records:
        signature = tuple(
            (record.cell(k).parsed_value_text or "") if record.cell(k) else ""
            for k in key_columns
            if profile.has_column(k)
        )
        if not any(signature):
            continue
        if signature in seen:
            issues.append(
                Issue(
                    code=IssueCode.POSSIBLE_DUPLICATE,
                    severity=IssueSeverity.WARNING,
                    message_key="issue.possible_duplicate",
                    document_id=document_id,
                    record_id=record.id,
                    details={"duplicate_of_record_id": seen[signature]},
                )
            )
        else:
            seen[signature] = record.id
    return issues


def record_status_from(issues: Iterable[Issue]) -> RecordStatus:
    status = RecordStatus.OK
    for issue in issues:
        if issue.severity is IssueSeverity.CRITICAL:
            return RecordStatus.CRITICAL
        if issue.severity is IssueSeverity.WARNING:
            status = RecordStatus.REVIEW
    return status
