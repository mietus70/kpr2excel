"""Versioned KPiR layout profiles.

A profile is declarative data (YAML/JSON). It never executes arbitrary code and is
validated against this schema at load time. Column semantics, header phrases,
validation dependencies, confidence thresholds and XLSX formatting all live here so
that neither the UI nor the extraction algorithm hardcodes a legal layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

__all__ = [
    "ProfileError",
    "ColumnType",
    "ProfileColumn",
    "SumRule",
    "ConfidenceConfig",
    "TextAssemblyConfig",
    "KpirProfile",
    "ProfileRegistry",
    "profile_from_dict",
]


class ProfileError(ValueError):
    """Raised when a profile document does not satisfy the schema."""


class ColumnType:
    TEXT = "text"
    MONEY = "money"
    DATE = "date"
    INTEGER = "integer"

    ALL = (TEXT, MONEY, DATE, INTEGER)


@dataclass(frozen=True, slots=True)
class ProfileColumn:
    key: str
    label_pl: str
    type: str
    x0: float
    x1: float
    form_number: str | None = None
    header_keywords: tuple[str, ...] = ()
    required: bool = False
    xlsx_format: str | None = None
    xlsx_width: float = 16.0
    align: str = "left"

    @property
    def is_money(self) -> bool:
        return self.type == ColumnType.MONEY

    def contains_x(self, x: float) -> bool:
        return self.x0 <= x < self.x1


@dataclass(frozen=True, slots=True)
class SumRule:
    """``target`` must equal the sum of ``components`` (validation only!).

    A mismatch produces an issue; the extracted value is never overwritten.
    """

    code: str
    target: str
    components: tuple[str, ...]
    tolerance: Decimal = Decimal("0.00")
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class ConfidenceConfig:
    weights: dict[str, float]
    review_below: float = 0.85
    critical_below: float = 0.6


@dataclass(frozen=True, slots=True)
class TextAssemblyConfig:
    join_lines_with: str = " "
    dehyphenate: bool = True
    repair_letter_spacing: bool = True
    letter_spacing_max_gap: float = 0.004
    collapse_whitespace: bool = True


@dataclass(frozen=True, slots=True)
class KpirProfile:
    id: str
    version: int
    display_name: str
    columns: tuple[ProfileColumn, ...]
    header_phrases: tuple[str, ...] = ()
    min_match_score: float = 0.55
    match_margin: float = 0.1
    header_zone_max_y: float = 0.28
    footer_zone_min_y: float = 0.94
    empty_markers: tuple[str, ...] = ()
    date_formats: tuple[str, ...] = ("DD.MM.YYYY", "DD.MM.YY")
    sum_rules: tuple[SumRule, ...] = ()
    confidence: ConfidenceConfig = field(
        default_factory=lambda: ConfidenceConfig(weights={}, review_below=0.85, critical_below=0.6)
    )
    text_assembly: TextAssemblyConfig = field(default_factory=TextAssemblyConfig)
    row_number_column: str = "row_number"
    date_column: str = "business_date"
    default_filter_column: str = "total_expenses"
    default_export_columns: tuple[str, ...] = ()
    issues_sheet_name: str = "Problemy"
    sheet_name: str = "KPiR"

    # ---------------------------------------------------------------- lookups
    @property
    def qualified_id(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def column_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.columns)

    def column(self, key: str) -> ProfileColumn:
        for column in self.columns:
            if column.key == key:
                return column
        raise ProfileError(f"unknown column key: {key}")

    def has_column(self, key: str) -> bool:
        return any(c.key == key for c in self.columns)

    def money_columns(self) -> tuple[ProfileColumn, ...]:
        return tuple(c for c in self.columns if c.is_money)

    def required_columns(self) -> tuple[ProfileColumn, ...]:
        return tuple(c for c in self.columns if c.required)

    def column_at_x(self, x: float) -> ProfileColumn | None:
        for column in self.columns:
            if column.contains_x(x):
                return column
        return None

    def export_columns_default(self) -> tuple[str, ...]:
        return self.default_export_columns or self.column_keys


# --------------------------------------------------------------------------------------
# Parsing / validation
# --------------------------------------------------------------------------------------


def _require(data: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in data:
        raise ProfileError(f"missing required profile key: {key}")
    value = data[key]
    if not isinstance(value, kind):
        raise ProfileError(f"profile key {key} has invalid type {type(value).__name__}")
    return value


def _column_from_dict(data: dict[str, Any], index: int) -> ProfileColumn:
    if not isinstance(data, dict):
        raise ProfileError(f"column #{index} must be a mapping")
    key = _require(data, "key", str)
    ctype = _require(data, "type", str)
    if ctype not in ColumnType.ALL:
        raise ProfileError(f"column {key}: unsupported type {ctype}")
    x0 = float(_require(data, "x0", (int, float)))
    x1 = float(_require(data, "x1", (int, float)))
    if not (0.0 <= x0 < x1 <= 1.0):
        raise ProfileError(f"column {key}: x0/x1 must satisfy 0 <= x0 < x1 <= 1")
    return ProfileColumn(
        key=key,
        label_pl=str(data.get("label_pl", key)),
        type=ctype,
        x0=x0,
        x1=x1,
        form_number=(str(data["form_number"]) if data.get("form_number") is not None else None),
        header_keywords=tuple(str(k) for k in data.get("header_keywords", ())),
        required=bool(data.get("required", False)),
        xlsx_format=(str(data["xlsx_format"]) if data.get("xlsx_format") else None),
        xlsx_width=float(data.get("xlsx_width", 16.0)),
        align=str(data.get("align", "left")),
    )


def profile_from_dict(data: dict[str, Any]) -> KpirProfile:
    """Build and validate a profile from a plain mapping (YAML/JSON document)."""
    if not isinstance(data, dict):
        raise ProfileError("profile document must be a mapping")

    raw_columns = _require(data, "columns", list)
    if not raw_columns:
        raise ProfileError("profile must define at least one column")
    columns = tuple(_column_from_dict(c, i) for i, c in enumerate(raw_columns))

    keys = [c.key for c in columns]
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        raise ProfileError(f"duplicate column keys: {sorted(duplicates)}")

    ordered = sorted(columns, key=lambda c: c.x0)
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right.x0 + 1e-9 < left.x1:
            raise ProfileError(f"columns {left.key} and {right.key} overlap on the X axis")

    sum_rules: list[SumRule] = []
    for rule in data.get("sum_rules", ()) or ():
        target = _require(rule, "target", str)
        components = tuple(str(c) for c in _require(rule, "components", list))
        for key in (target, *components):
            if key not in keys:
                raise ProfileError(f"sum rule references unknown column: {key}")
        sum_rules.append(
            SumRule(
                code=str(rule.get("code", f"{target.upper()}_MISMATCH")),
                target=target,
                components=components,
                tolerance=Decimal(str(rule.get("tolerance", "0.00"))),
                severity=str(rule.get("severity", "warning")),
            )
        )

    conf_raw = data.get("confidence", {}) or {}
    confidence = ConfidenceConfig(
        weights={str(k): float(v) for k, v in (conf_raw.get("weights", {}) or {}).items()},
        review_below=float(conf_raw.get("review_below", 0.85)),
        critical_below=float(conf_raw.get("critical_below", 0.6)),
    )
    if not (0.0 <= confidence.critical_below <= confidence.review_below <= 1.0):
        raise ProfileError("confidence thresholds must satisfy 0 <= critical <= review <= 1")

    ta_raw = data.get("text_assembly", {}) or {}
    text_assembly = TextAssemblyConfig(
        join_lines_with=str(ta_raw.get("join_lines_with", " ")),
        dehyphenate=bool(ta_raw.get("dehyphenate", True)),
        repair_letter_spacing=bool(ta_raw.get("repair_letter_spacing", True)),
        letter_spacing_max_gap=float(ta_raw.get("letter_spacing_max_gap", 0.004)),
        collapse_whitespace=bool(ta_raw.get("collapse_whitespace", True)),
    )

    default_export = tuple(str(k) for k in data.get("default_export_columns", ()) or ())
    for key in default_export:
        if key not in keys:
            raise ProfileError(f"default_export_columns references unknown column: {key}")

    for name in ("row_number_column", "date_column", "default_filter_column"):
        value = data.get(name)
        if value is not None and str(value) not in keys:
            raise ProfileError(f"{name} references unknown column: {value}")

    profile = KpirProfile(
        id=_require(data, "id", str),
        version=int(_require(data, "version", int)),
        display_name=str(data.get("display_name", data["id"])),
        columns=columns,
        header_phrases=tuple(str(p) for p in data.get("header_phrases", ()) or ()),
        min_match_score=float(data.get("min_match_score", 0.55)),
        match_margin=float(data.get("match_margin", 0.1)),
        header_zone_max_y=float(data.get("header_zone_max_y", 0.28)),
        footer_zone_min_y=float(data.get("footer_zone_min_y", 0.94)),
        empty_markers=tuple(str(m) for m in data.get("empty_markers", ()) or ()),
        date_formats=tuple(str(f) for f in data.get("date_formats", ()) or ("DD.MM.YYYY",)),
        sum_rules=tuple(sum_rules),
        confidence=confidence,
        text_assembly=text_assembly,
        row_number_column=str(data.get("row_number_column", "row_number")),
        date_column=str(data.get("date_column", "business_date")),
        default_filter_column=str(data.get("default_filter_column", "total_expenses")),
        default_export_columns=default_export,
        issues_sheet_name=str(data.get("issues_sheet_name", "Problemy")),
        sheet_name=str(data.get("sheet_name", "KPiR")),
    )
    return profile


@dataclass(slots=True)
class ProfileRegistry:
    """In-memory set of validated profiles keyed by ``id@version``."""

    profiles: dict[str, KpirProfile] = field(default_factory=dict)

    def add(self, profile: KpirProfile) -> None:
        self.profiles[profile.qualified_id] = profile

    def get(self, qualified_id: str) -> KpirProfile:
        if qualified_id in self.profiles:
            return self.profiles[qualified_id]
        # Allow lookups without an explicit version -> latest version wins.
        candidates = [p for p in self.profiles.values() if p.id == qualified_id]
        if not candidates:
            raise ProfileError(f"unknown profile: {qualified_id}")
        return max(candidates, key=lambda p: p.version)

    def all(self) -> tuple[KpirProfile, ...]:
        return tuple(self.profiles.values())
