"""API DTOs. Money is always a decimal string; dates are ISO strings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# ------------------------------------------------------------------ errors


class ErrorBody(ApiModel):
    code: str
    message: str
    field: str | None = None
    correlation_id: str = Field(alias="correlationId")


class ErrorResponse(ApiModel):
    error: ErrorBody


# ------------------------------------------------------------------ profiles


class ProfileColumnOut(ApiModel):
    key: str
    label: str
    type: str
    form_number: str | None = Field(default=None, alias="formNumber")
    required: bool = False


class ProfileOut(ApiModel):
    id: str
    version: int
    qualified_id: str = Field(alias="qualifiedId")
    display_name: str = Field(alias="displayName")
    columns: list[ProfileColumnOut]
    default_export_columns: list[str] = Field(alias="defaultExportColumns")
    default_filter_column: str = Field(alias="defaultFilterColumn")


# ------------------------------------------------------------------ batches / documents


class BatchCreate(ApiModel):
    display_name: str | None = Field(default=None, alias="displayName")


class BatchOut(ApiModel):
    id: str
    display_name: str = Field(alias="displayName")
    created_at: str = Field(alias="createdAt")
    document_count: int = Field(default=0, alias="documentCount")


class DocumentOut(ApiModel):
    id: str
    batch_id: str = Field(alias="batchId")
    original_name: str = Field(alias="originalName")
    size_bytes: int = Field(alias="sizeBytes")
    page_count: int = Field(alias="pageCount")
    status: str
    detected_profile_id: str | None = Field(default=None, alias="detectedProfileId")
    period_from: str | None = Field(default=None, alias="periodFrom")
    period_to: str | None = Field(default=None, alias="periodTo")
    created_at: str = Field(alias="createdAt")
    record_count: int = Field(default=0, alias="recordCount")
    issue_counts: dict[str, int] = Field(default_factory=dict, alias="issueCounts")
    error_code: str | None = Field(default=None, alias="errorCode")


class DocumentImportOut(ApiModel):
    documents: list[DocumentOut]
    job_ids: list[str] = Field(alias="jobIds")


# ------------------------------------------------------------------ records


class SourceSpanOut(ApiModel):
    page: int
    bbox: list[float]


class CellOut(ApiModel):
    id: str
    column_key: str = Field(alias="columnKey")
    value: str | None
    value_type: str = Field(alias="valueType")
    raw_text: str = Field(alias="rawText")
    extracted_value: str | None = Field(default=None, alias="extractedValue")
    is_manual: bool = Field(alias="isManual")
    confidence: float
    confidence_parts: dict[str, float] = Field(default_factory=dict, alias="confidenceParts")
    revision: int
    source: SourceSpanOut | None = None


class RecordOut(ApiModel):
    id: str
    document_id: str = Field(alias="documentId")
    logical_index: int = Field(alias="logicalIndex")
    page_from: int = Field(alias="pageFrom")
    page_to: int = Field(alias="pageTo")
    status: str
    confidence: float
    revision: int
    cells: dict[str, CellOut]


class RecordPageOut(ApiModel):
    items: list[RecordOut]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    total: int


class CellPatch(ApiModel):
    value: str | None = None
    base_revision: int = Field(alias="baseRevision")
    reason: str | None = None


class CellRevert(ApiModel):
    base_revision: int = Field(alias="baseRevision")


class CellPatchOut(ApiModel):
    cell: CellOut
    record_status: str = Field(alias="recordStatus")


class CellConflictOut(ApiModel):
    code: str = "REVISION_CONFLICT"
    message: str
    current_revision: int = Field(alias="currentRevision")
    current_value: str | None = Field(default=None, alias="currentValue")


class CorrectionHistoryOut(ApiModel):
    id: str
    action: str
    old_value: str | None = Field(default=None, alias="oldValue")
    new_value: str | None = Field(default=None, alias="newValue")
    reason: str | None = None
    actor: str
    created_at: str = Field(alias="createdAt")


# ------------------------------------------------------------------ issues


class IssueOut(ApiModel):
    id: str
    code: str
    severity: str
    message_key: str = Field(alias="messageKey")
    document_id: str | None = Field(default=None, alias="documentId")
    page_number: int | None = Field(default=None, alias="pageNumber")
    record_id: str | None = Field(default=None, alias="recordId")
    cell_id: str | None = Field(default=None, alias="cellId")
    status: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")


class IssuePageOut(ApiModel):
    items: list[IssueOut]
    total: int


# ------------------------------------------------------------------ jobs


class JobOut(ApiModel):
    id: str
    type: str
    status: str
    document_id: str | None = Field(default=None, alias="documentId")
    batch_id: str | None = Field(default=None, alias="batchId")
    export_id: str | None = Field(default=None, alias="exportId")
    progress_current: int = Field(alias="progressCurrent")
    progress_total: int = Field(alias="progressTotal")
    progress_phase: str | None = Field(default=None, alias="progressPhase")
    attempt: int
    error_code: str | None = Field(default=None, alias="errorCode")
    created_at: str = Field(alias="createdAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")


class JobListOut(ApiModel):
    items: list[JobOut]


# ------------------------------------------------------------------ export


class ExportScopeIn(ApiModel):
    type: Literal["documents", "batch"] = "documents"
    document_ids: list[str] = Field(default_factory=list, alias="documentIds")
    batch_id: str | None = Field(default=None, alias="batchId")


class RowSelectionIn(ApiModel):
    mode: Literal["ALL_MATCHING", "EXPLICIT"] = "ALL_MATCHING"
    included_record_ids: list[str] = Field(default_factory=list, alias="includedRecordIds")
    excluded_record_ids: list[str] = Field(default_factory=list, alias="excludedRecordIds")


class MoneyRangeFilterIn(ApiModel):
    type: Literal["money_range"] = "money_range"
    column_key: str = Field(alias="columnKey")
    min: str | None = None
    max: str | None = None
    bounds: Literal["inclusive", "exclusive"] = "inclusive"


class ExportDefinitionIn(ApiModel):
    scope: ExportScopeIn
    column_keys: list[str] = Field(alias="columnKeys")
    row_selection: RowSelectionIn = Field(default_factory=RowSelectionIn, alias="rowSelection")
    filters: list[MoneyRangeFilterIn] = Field(default_factory=list)
    policy: Literal["strict", "reviewed", "draft"] = "strict"
    include_issues_sheet: bool = Field(default=False, alias="includeIssuesSheet")
    source_revision: str | None = Field(default=None, alias="sourceRevision")


class SampleRowOut(ApiModel):
    record_id: str = Field(alias="recordId")
    values: dict[str, str | None]


class ColumnLabelOut(ApiModel):
    key: str
    label: str


class ActiveFilterOut(ApiModel):
    column_key: str = Field(alias="columnKey")
    label: str
    min: str | None = None
    max: str | None = None
    bounds: str


class ExportPreviewOut(ApiModel):
    matching_row_count: int = Field(alias="matchingRowCount")
    column_count: int = Field(alias="columnCount")
    excluded_for_null_or_invalid_count: int = Field(alias="excludedForNullOrInvalidCount")
    excluded_by_range_count: int = Field(alias="excludedByRangeCount")
    excluded_by_selection_count: int = Field(alias="excludedBySelectionCount")
    total_row_count: int = Field(alias="totalRowCount")
    issue_counts: dict[str, int] = Field(alias="issueCounts")
    blocking_issue_count: int = Field(alias="blockingIssueCount")
    sample_rows: list[SampleRowOut] = Field(alias="sampleRows")
    source_revision: str = Field(alias="sourceRevision")
    column_labels: list[ColumnLabelOut] = Field(alias="columnLabels")
    active_filters: list[ActiveFilterOut] = Field(alias="activeFilters")
    policy: str


class ExportOut(ApiModel):
    id: str
    status: str
    policy: str
    job_id: str | None = Field(default=None, alias="jobId")
    row_count: int | None = Field(default=None, alias="rowCount")
    column_count: int | None = Field(default=None, alias="columnCount")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    sha256: str | None = None
    source_revision: str | None = Field(default=None, alias="sourceRevision")
    created_at: str = Field(alias="createdAt")
    error_code: str | None = Field(default=None, alias="errorCode")
    download_url: str | None = Field(default=None, alias="downloadUrl")


class ExportListOut(ApiModel):
    items: list[ExportOut]


class PresetIn(ApiModel):
    name: str
    profile_id: str = Field(alias="profileId")
    definition: dict[str, Any]


class PresetOut(ApiModel):
    id: str
    name: str
    profile_id: str = Field(alias="profileId")
    definition: dict[str, Any]
    created_at: str = Field(alias="createdAt")
