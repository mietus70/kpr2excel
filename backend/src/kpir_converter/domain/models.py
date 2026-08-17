"""Core domain entities. No FastAPI, no SQLite, no PDF library imports here."""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .values import BBox, Confidence

__all__ = [
    "new_id",
    "utc_now",
    "ValueType",
    "DocumentStatus",
    "PageStatus",
    "RunStatus",
    "RecordStatus",
    "IssueSeverity",
    "IssueStatus",
    "JobType",
    "JobStatus",
    "ExportPolicy",
    "RowSelectionMode",
    "Token",
    "SourceSpan",
    "Cell",
    "KpirRecord",
    "Issue",
    "Correction",
    "Document",
    "Page",
    "ExtractionRun",
    "MoneyRangeFilter",
    "RowSelection",
    "ExportScope",
    "ExportDefinition",
]


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class ValueType(StrEnum):
    TEXT = "text"
    MONEY = "money"
    DATE = "date"
    INTEGER = "integer"
    EMPTY = "empty"


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class PageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecordStatus(StrEnum):
    OK = "ok"
    REVIEW = "review"
    CRITICAL = "critical"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IssueStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class JobType(StrEnum):
    ANALYZE_DOCUMENT = "ANALYZE_DOCUMENT"
    EXTRACT_DOCUMENT = "EXTRACT_DOCUMENT"
    VALIDATE_DOCUMENT = "VALIDATE_DOCUMENT"
    RENDER_PAGE = "RENDER_PAGE"
    EXPORT_XLSX = "EXPORT_XLSX"
    DELETE_DOCUMENT_ARTIFACTS = "DELETE_DOCUMENT_ARTIFACTS"
    REPROCESS_DOCUMENT = "REPROCESS_DOCUMENT"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ExportPolicy(StrEnum):
    STRICT = "strict"
    REVIEWED = "reviewed"
    DRAFT = "draft"


class RowSelectionMode(StrEnum):
    ALL_MATCHING = "ALL_MATCHING"
    EXPLICIT = "EXPLICIT"


# --------------------------------------------------------------------------------------
# Extraction primitives
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Token:
    """An immutable positioned word taken from the PDF text layer."""

    raw_text: str
    bbox: BBox
    page_number: int
    baseline: float
    font_name: str | None = None
    font_size: float | None = None
    block_id: int | None = None
    line_id: int | None = None
    word_id: int | None = None


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Where a value came from in the original document."""

    page_number: int
    bbox: BBox


@dataclass(slots=True)
class Cell:
    column_key: str
    raw_text: str
    normalized_text: str
    parsed_value_text: str | None
    value_type: ValueType
    confidence: Confidence
    source: SourceSpan | None = None
    id: str = field(default_factory=new_id)
    record_id: str | None = None
    is_manual: bool = False
    revision: int = 1
    extraction_meta: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.value_type is ValueType.EMPTY or self.parsed_value_text is None


@dataclass(slots=True)
class KpirRecord:
    logical_index: int
    cells: dict[str, Cell]
    source_page_from: int
    source_page_to: int
    id: str = field(default_factory=new_id)
    extraction_run_id: str | None = None
    status: RecordStatus = RecordStatus.OK
    confidence: float = 1.0
    revision: int = 1

    def cell(self, column_key: str) -> Cell | None:
        return self.cells.get(column_key)


@dataclass(slots=True)
class Issue:
    code: str
    severity: IssueSeverity
    message_key: str
    document_id: str | None = None
    page_number: int | None = None
    record_id: str | None = None
    cell_id: str | None = None
    details: dict = field(default_factory=dict)
    status: IssueStatus = IssueStatus.OPEN
    id: str = field(default_factory=new_id)
    created_at: _dt.datetime = field(default_factory=utc_now)
    resolved_at: _dt.datetime | None = None


@dataclass(slots=True)
class Correction:
    cell_id: str
    old_value_text: str | None
    new_value_text: str | None
    old_value_type: ValueType
    new_value_type: ValueType
    base_revision: int
    reason: str | None = None
    actor: str = "local-user"
    id: str = field(default_factory=new_id)
    created_at: _dt.datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Page:
    document_id: str
    page_number: int
    width: float
    height: float
    rotation: int
    text_available: bool
    status: PageStatus = PageStatus.PENDING
    render_path: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    id: str = field(default_factory=new_id)


@dataclass(slots=True)
class Document:
    original_name: str
    stored_path: str
    sha256: str
    size_bytes: int
    batch_id: str
    page_count: int = 0
    status: DocumentStatus = DocumentStatus.UPLOADED
    detected_profile_id: str | None = None
    declared_period_from: str | None = None
    declared_period_to: str | None = None
    id: str = field(default_factory=new_id)
    created_at: _dt.datetime = field(default_factory=utc_now)
    deleted_at: _dt.datetime | None = None


@dataclass(slots=True)
class ExtractionRun:
    document_id: str
    extractor_version: str
    profile_id: str
    profile_version: int
    config_hash: str
    status: RunStatus = RunStatus.RUNNING
    id: str = field(default_factory=new_id)
    started_at: _dt.datetime = field(default_factory=utc_now)
    finished_at: _dt.datetime | None = None


# --------------------------------------------------------------------------------------
# Export definition (immutable snapshot once an export starts)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MoneyRangeFilter:
    """Inclusive-by-default range filter over a typed money column."""

    column_key: str
    min_text: str | None = None
    max_text: str | None = None
    bounds: str = "inclusive"
    type: str = "money_range"


@dataclass(frozen=True, slots=True)
class RowSelection:
    mode: RowSelectionMode = RowSelectionMode.ALL_MATCHING
    included_record_ids: tuple[str, ...] = ()
    excluded_record_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExportScope:
    type: str  # "documents" | "batch"
    document_ids: tuple[str, ...] = ()
    batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExportDefinition:
    scope: ExportScope
    column_keys: tuple[str, ...]
    row_selection: RowSelection = field(default_factory=RowSelection)
    filters: tuple[MoneyRangeFilter, ...] = ()
    policy: ExportPolicy = ExportPolicy.STRICT
    include_issues_sheet: bool = False
    source_revision: str | None = None
