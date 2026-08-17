"""Application services: use cases wired to repositories and adapters."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Settings
from ..domain.export import (
    ExportValidationError,
    select_records,
    validate_definition,
)
from ..domain.models import (
    Document,
    DocumentStatus,
    ExportDefinition,
    ExportPolicy,
    ExportScope,
    Issue,
    IssueSeverity,
    JobStatus,
    JobType,
    KpirRecord,
    MoneyRangeFilter,
    Page,
    PageStatus,
    RowSelection,
    RowSelectionMode,
    ValueType,
    new_id,
)
from ..domain.profiles import ColumnType, KpirProfile, ProfileRegistry
from ..domain.validation import IssueCode, record_status_from, validate_records
from ..infrastructure.db.database import Database
from ..infrastructure.db.repositories import (
    BatchRepository,
    DocumentRepository,
    ExportRepository,
    IssueRepository,
    JobRepository,
    PageRepository,
    PresetRepository,
    RecordRepository,
    RunRepository,
)
from ..infrastructure.pdf.adapter import PdfDocumentAdapter, PdfError
from ..infrastructure.xlsx.exporter import write_export
from .extraction import EXTRACTOR_VERSION, extract_page, match_profile

logger = logging.getLogger(__name__)

__all__ = [
    "AppContext",
    "ImportService",
    "ExtractionService",
    "ReviewService",
    "ExportService",
    "ExportPreview",
    "ImportError_",
    "definition_from_dict",
    "definition_to_dict",
]

_PDF_MAGIC = b"%PDF-"
_SAFE_NAME_RE = re.compile(r"[^\w \-.()\[\]ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", re.UNICODE)


class ImportError_(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class AppContext:
    """Composition root shared by the API and the worker."""

    settings: Settings
    db: Database
    profiles: ProfileRegistry

    batches: BatchRepository = field(init=False)
    documents: DocumentRepository = field(init=False)
    pages: PageRepository = field(init=False)
    runs: RunRepository = field(init=False)
    records: RecordRepository = field(init=False)
    issues: IssueRepository = field(init=False)
    jobs: JobRepository = field(init=False)
    exports: ExportRepository = field(init=False)
    presets: PresetRepository = field(init=False)

    def __post_init__(self) -> None:
        self.batches = BatchRepository(self.db)
        self.documents = DocumentRepository(self.db)
        self.pages = PageRepository(self.db)
        self.runs = RunRepository(self.db)
        self.records = RecordRepository(self.db)
        self.issues = IssueRepository(self.db)
        self.jobs = JobRepository(self.db)
        self.exports = ExportRepository(self.db)
        self.presets = PresetRepository(self.db)

    @property
    def default_profile(self) -> KpirProfile:
        return self.profiles.get("kpir_pl_2018")


def safe_display_name(name: str) -> str:
    """Sanitised metadata only. Files on disk are always stored under a UUID."""
    cleaned = _SAFE_NAME_RE.sub("_", Path(name).name).strip() or "dokument.pdf"
    return cleaned[:120]


# --------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------


class ImportService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def create_batch(self, display_name: str | None = None) -> str:
        return self.ctx.batches.create(safe_display_name(display_name or "Paczka"))

    def import_stream(
        self,
        batch_id: str,
        original_name: str,
        chunks: Iterable[bytes],
    ) -> Document:
        """Stream an upload to disk while hashing and enforcing limits."""
        settings = self.ctx.settings
        settings.ensure_directories()
        document_id = new_id()
        temp_path = settings.temp_dir / f"upload-{document_id}.part"
        digest = hashlib.sha256()
        size = 0
        first_chunk = True

        try:
            with temp_path.open("wb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    if first_chunk:
                        if not chunk.startswith(_PDF_MAGIC):
                            raise ImportError_("NOT_A_PDF", "the file is not a PDF document")
                        first_chunk = False
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise ImportError_(
                            "FILE_TOO_LARGE",
                            f"the file exceeds the {settings.max_upload_mb} MB limit",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if first_chunk or size == 0:
                raise ImportError_("EMPTY_FILE", "the uploaded file is empty")

            target_dir = settings.originals_dir / document_id
            target_dir.mkdir(parents=True, exist_ok=True)
            final_path = target_dir / "source.pdf"
            shutil.move(str(temp_path), str(final_path))
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        document = Document(
            original_name=safe_display_name(original_name),
            stored_path=str(final_path),
            sha256=digest.hexdigest(),
            size_bytes=size,
            batch_id=batch_id,
            id=document_id,
        )
        self.ctx.documents.create(document)
        self.ctx.batches.touch(batch_id)
        self.ctx.jobs.enqueue(
            JobType.EXTRACT_DOCUMENT.value,
            document_id=document.id,
            batch_id=batch_id,
            priority=100,
        )
        logger.info("document imported", extra={"document_id": document.id, "size_bytes": size})
        return document

    def delete_document(self, document_id: str) -> None:
        """Delete the original, renders, derived data, corrections and exports."""
        row = self.ctx.documents.get(document_id)
        if row is None:
            raise KeyError(document_id)
        self.ctx.documents.set_status(document_id, DocumentStatus.DELETING.value)
        for job in self.ctx.jobs.list(document_id=document_id, status="RUNNING"):
            self.ctx.jobs.request_cancel(job["id"])
        for job in self.ctx.jobs.list(document_id=document_id, status="QUEUED"):
            self.ctx.jobs.request_cancel(job["id"])

        settings = self.ctx.settings
        for path in (
            settings.originals_dir / document_id,
            settings.pages_dir / document_id,
        ):
            shutil.rmtree(path, ignore_errors=True)
        # Exports that referenced only this document are removed with their files.
        for export in self.ctx.exports.list(limit=1000):
            docs = self.ctx.exports.documents(export["id"])
            if docs and set(docs) <= {document_id}:
                if export["path"]:
                    shutil.rmtree(Path(export["path"]).parent, ignore_errors=True)
                self.ctx.exports.delete(export["id"])
        self.ctx.documents.hard_delete(document_id)
        logger.info("document deleted", extra={"document_id": document_id})


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------

# Zakres z nagłówka: "za okres od 01.01.2023 do 30.04.2023".
# Fraza "od ... do ..." jest kotwicą - bez niej łapaliśmy fragmenty numerów
# dowodów ("XX/YY/99/1/0002" -> rok 0002) i okres wychodził absurdalny.
_PERIOD_RANGE_RE = re.compile(
    r"(?i)okres\w*\s*(?:od\s*)?"
    r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"
    r"\s*(?:do|-|–|—|\.\.)\s*"
    r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"
)
# Zapasowo pojedyncze pełne daty, ale tylko jako samodzielne słowa.
_PERIOD_RE = re.compile(r"(?<![\d/.\-])(\d{2})[.\-/](\d{2})[.\-/](\d{4})(?![\d/.\-])")
# Rozsądny zakres lat dla dokumentu księgowego.
_PLAUSIBLE_YEARS = (1990, 2100)


class ExtractionService:
    """Runs the pipeline page by page with a checkpoint after every page."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def run(
        self,
        document_id: str,
        *,
        job_id: str | None = None,
        should_cancel: object = None,
        progress: object = None,
    ) -> str:
        document = self.ctx.documents.get(document_id)
        if document is None:
            raise KeyError(document_id)
        path = Path(document["stored_path"])
        self.ctx.documents.set_status(document_id, DocumentStatus.ANALYZING.value)

        try:
            adapter = PdfDocumentAdapter(path)
        except PdfError as exc:
            self.ctx.documents.set_status(
                document_id,
                DocumentStatus.FAILED.value,
                error_code=exc.code,
                error_summary=exc.message,
            )
            self.ctx.issues.insert_many(
                [
                    Issue(
                        code=exc.code,
                        severity=IssueSeverity.CRITICAL,
                        message_key=f"issue.{exc.code.lower()}",
                        document_id=document_id,
                    )
                ]
            )
            raise

        with adapter:
            page_count = adapter.page_count
            if page_count > self.ctx.settings.max_pages_per_document:
                self.ctx.documents.set_status(
                    document_id,
                    DocumentStatus.FAILED.value,
                    error_code="TOO_MANY_PAGES",
                    error_summary="document exceeds the page limit",
                )
                raise ImportError_("TOO_MANY_PAGES", "document exceeds the page limit")

            # ---------------------------------------------------------- preflight
            sample_numbers = sorted({1, max(1, page_count // 2), page_count})[:3]
            samples = [adapter.page_content(n) for n in sample_numbers]
            sample_texts = [" ".join(t.raw_text for t in s.tokens) for s in samples]
            if not any(text.strip() for text in sample_texts):
                self.ctx.documents.set_status(
                    document_id,
                    DocumentStatus.FAILED.value,
                    error_code=IssueCode.OCR_REQUIRED,
                    error_summary="no usable text layer",
                )
                self.ctx.issues.insert_many(
                    [
                        Issue(
                            code=IssueCode.OCR_REQUIRED,
                            severity=IssueSeverity.CRITICAL,
                            message_key="issue.ocr_required",
                            document_id=document_id,
                        )
                    ]
                )
                raise ImportError_(IssueCode.OCR_REQUIRED, "no usable text layer")

            match, diagnostics = match_profile(self.ctx.profiles.all(), sample_texts)
            if match is None:
                profile = self.ctx.default_profile
                profile_score = 0.5
                self.ctx.issues.insert_many(
                    [
                        Issue(
                            code=IssueCode.LOW_PROFILE_CONFIDENCE,
                            severity=IssueSeverity.WARNING,
                            message_key="issue.low_profile_confidence",
                            document_id=document_id,
                            details={"diagnostics": diagnostics},
                        )
                    ]
                )
            else:
                profile = match.profile
                profile_score = match.score

            period_from, period_to = _detect_period(sample_texts)
            self.ctx.documents.set_analysis(
                document_id,
                page_count=page_count,
                profile_id=profile.qualified_id,
                period_from=period_from.isoformat() if period_from else None,
                period_to=period_to.isoformat() if period_to else None,
            )

            page_models = []
            for number in range(1, page_count + 1):
                geometry = adapter.page_geometry(number)
                page_models.append(
                    Page(
                        document_id=document_id,
                        page_number=number,
                        width=geometry.width,
                        height=geometry.height,
                        rotation=geometry.rotation,
                        text_available=True,
                    )
                )
            self.ctx.pages.upsert_many(page_models)

            run_id = self.ctx.runs.create(
                document_id,
                extractor_version=EXTRACTOR_VERSION,
                profile_id=profile.id,
                profile_version=profile.version,
                config_hash=_config_hash(profile),
            )
            self.ctx.documents.set_status(document_id, DocumentStatus.EXTRACTING.value)

            # ---------------------------------------------------------- per page
            logical_index = 0
            cancelled = False
            all_records: list[KpirRecord] = []
            for number in range(1, page_count + 1):
                if callable(should_cancel) and should_cancel():
                    cancelled = True
                    break
                try:
                    content = adapter.page_content(number)
                    result = extract_page(
                        profile,
                        content.tokens,
                        content.horizontal_lines,
                        content.vertical_lines,
                        page_number=number,
                        document_id=document_id,
                        profile_score=profile_score,
                        period_year=period_from.year if period_from else None,
                        start_logical_index=logical_index,
                    )
                    page_issues = list(result.issues)
                    validation_issues = validate_records(
                        result.records,
                        profile,
                        document_id=document_id,
                        period_from=period_from,
                        period_to=period_to,
                    )
                    page_issues.extend(validation_issues)

                    by_record: dict[str, list[Issue]] = {}
                    for issue in page_issues:
                        if issue.record_id:
                            by_record.setdefault(issue.record_id, []).append(issue)
                    for record in result.records:
                        record.status = record_status_from(by_record.get(record.id, []))

                    # Checkpoint: one short transaction per page.
                    self.ctx.records.insert_records(run_id, document_id, result.records)
                    self.ctx.issues.insert_many(page_issues)
                    self.ctx.runs.checkpoint(run_id, number)
                    self.ctx.pages.set_status(document_id, number, PageStatus.DONE.value)
                    logical_index += len(result.records)
                    all_records.extend(result.records)
                except Exception as exc:  # noqa: BLE001 - one page must not kill the batch
                    logger.warning(
                        "page extraction failed",
                        extra={"document_id": document_id, "page": number},
                    )
                    self.ctx.pages.set_status(
                        document_id,
                        number,
                        PageStatus.FAILED.value,
                        error_code="PAGE_EXTRACTION_FAILED",
                        error_summary=type(exc).__name__,
                    )
                    self.ctx.issues.insert_many(
                        [
                            Issue(
                                code="PAGE_EXTRACTION_FAILED",
                                severity=IssueSeverity.CRITICAL,
                                message_key="issue.page_extraction_failed",
                                document_id=document_id,
                                page_number=number,
                                details={"error_type": type(exc).__name__},
                            )
                        ]
                    )
                if callable(progress):
                    progress(number, page_count, "extract")

            # Cross-page validation (row continuity, duplicates).
            if not cancelled and all_records:
                cross = _cross_page_issues(
                    all_records, profile, document_id, period_from, period_to
                )
                self.ctx.issues.insert_many(cross)

            status = JobStatus.CANCELLED if cancelled else JobStatus.SUCCEEDED
            self.ctx.runs.finish(run_id, "cancelled" if cancelled else "succeeded")
            self.ctx.documents.set_status(
                document_id,
                DocumentStatus.READY.value if not cancelled else DocumentStatus.UPLOADED.value,
            )
            logger.info(
                "extraction finished",
                extra={
                    "document_id": document_id,
                    "records": logical_index,
                    "status": status.value,
                },
            )
        return run_id

    def render_page(self, document_id: str, page_number: int) -> Path:
        document = self.ctx.documents.get(document_id)
        if document is None:
            raise KeyError(document_id)
        target = self.ctx.settings.pages_dir / document_id / f"{page_number}.webp"
        if target.exists():
            return target
        alternative = target.with_suffix(".png")
        if alternative.exists():
            return alternative
        with PdfDocumentAdapter(Path(document["stored_path"])) as adapter:
            produced = adapter.render_page(page_number, target, dpi=self.ctx.settings.render_dpi)
        self.ctx.pages.set_render(document_id, page_number, str(produced))
        return produced


def _cross_page_issues(
    records: Sequence[KpirRecord],
    profile: KpirProfile,
    document_id: str,
    period_from: _dt.date | None,
    period_to: _dt.date | None,
) -> list[Issue]:
    from ..domain.validation import _possible_duplicates, _row_number_continuity

    issues = list(_row_number_continuity(records, profile, document_id))
    issues.extend(_possible_duplicates(records, profile, document_id))
    return issues


def _detect_period(texts: Sequence[str]) -> tuple[_dt.date | None, _dt.date | None]:
    """Best effort period detection; never fabricated when unclear.

    Preferowane jest jawne "za okres od X do Y" z nagłówka. Dopiero gdy go nie
    ma, bierzemy pod uwagę pojedyncze pełne daty - i tylko takie, które są
    samodzielnymi słowami oraz mają sensowny rok. Bez tego fragmenty numerów
    dowodów (np. "XX/YY/99/1/0002") dawały okres z roku 0002, co następnie
    psuło rozwijanie wszystkich dat dwucyfrowych.
    """
    for text in texts:
        match = _PERIOD_RANGE_RE.search(text)
        if not match:
            continue
        d1, m1, y1, d2, m2, y2 = (int(g) for g in match.groups())
        try:
            start, end = _dt.date(y1, m1, d1), _dt.date(y2, m2, d2)
        except ValueError:
            continue
        if start <= end:
            return start, end

    dates: list[_dt.date] = []
    for text in texts:
        for day, month, year in _PERIOD_RE.findall(text):
            if not _PLAUSIBLE_YEARS[0] <= int(year) <= _PLAUSIBLE_YEARS[1]:
                continue
            try:
                dates.append(_dt.date(int(year), int(month), int(day)))
            except ValueError:
                continue
    if not dates:
        return None, None
    return min(dates), max(dates)


def _config_hash(profile: KpirProfile) -> str:
    payload = f"{profile.qualified_id}|{EXTRACTOR_VERSION}|{','.join(profile.column_keys)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------------------


class ReviewService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def parse_value(
        self, profile: KpirProfile, column_key: str, raw: str | None
    ) -> tuple[str | None, ValueType]:
        """Validate a user supplied value against the column type."""
        from ..domain.values import ParseError, parse_business_date, parse_money

        column = profile.column(column_key)
        if raw is None or str(raw).strip() == "":
            return None, ValueType.EMPTY
        text = str(raw).strip()
        if column.type == ColumnType.MONEY:
            money = parse_money(text, empty_markers=profile.empty_markers)
            if money is None:
                return None, ValueType.EMPTY
            return money.as_text(), ValueType.MONEY
        if column.type == ColumnType.DATE:
            date = parse_business_date(text, empty_markers=profile.empty_markers)
            if date is None:
                return None, ValueType.EMPTY
            return date.as_text(), ValueType.DATE
        if column.type == ColumnType.INTEGER:
            candidate = text.rstrip(".)")
            if not candidate.isdigit():
                raise ParseError("INVALID_INTEGER", "not an integer", text)
            return str(int(candidate)), ValueType.INTEGER
        return text, ValueType.TEXT

    def revalidate_record(self, record_id: str, profile: KpirProfile) -> None:
        """Re-run validation for one record after an edit."""
        record = self.ctx.records.get_record(record_id)
        if record is None:
            return
        row = self.ctx.db.query_one("SELECT document_id FROM records WHERE id = ?", (record_id,))
        document_id = row["document_id"] if row else None
        period_from = period_to = None
        if document_id:
            document = self.ctx.documents.get(document_id)
            if document:
                period_from = (
                    _dt.date.fromisoformat(document["declared_period_from"])
                    if document["declared_period_from"]
                    else None
                )
                period_to = (
                    _dt.date.fromisoformat(document["declared_period_to"])
                    if document["declared_period_to"]
                    else None
                )
        self.ctx.issues.delete_for_record(record_id)
        issues = validate_records(
            [record],
            profile,
            document_id=document_id,
            period_from=period_from,
            period_to=period_to,
        )
        self.ctx.issues.insert_many(issues)
        self.ctx.records.update_record_status(record_id, record_status_from(issues))


# --------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class ExportPreview:
    matching_row_count: int
    column_count: int
    excluded_for_null_or_invalid_count: int
    excluded_by_range_count: int
    excluded_by_selection_count: int
    total_row_count: int
    issue_counts: dict[str, int]
    blocking_issue_count: int
    sample_rows: list[dict]
    source_revision: str
    column_labels: list[dict[str, str]]
    active_filters: list[dict]
    policy: str


def definition_from_dict(data: dict, profile: KpirProfile) -> ExportDefinition:
    """Build a definition from the API payload (validated separately)."""
    scope_raw = data.get("scope") or {}
    scope = ExportScope(
        type=str(scope_raw.get("type", "documents")),
        document_ids=tuple(str(d) for d in scope_raw.get("documentIds", []) or []),
        batch_id=scope_raw.get("batchId"),
    )
    selection_raw = data.get("rowSelection") or {}
    mode_text = str(selection_raw.get("mode", "ALL_MATCHING")).upper()
    try:
        mode = RowSelectionMode(mode_text)
    except ValueError as exc:
        raise ExportValidationError(
            "INVALID_SELECTION_MODE", f"unknown row selection mode '{mode_text}'", "rowSelection"
        ) from exc
    selection = RowSelection(
        mode=mode,
        included_record_ids=tuple(str(i) for i in selection_raw.get("includedRecordIds", []) or []),
        excluded_record_ids=tuple(str(i) for i in selection_raw.get("excludedRecordIds", []) or []),
    )
    filters = []
    for raw in data.get("filters", []) or []:
        filters.append(
            MoneyRangeFilter(
                column_key=str(raw.get("columnKey", "")),
                min_text=_none_if_blank(raw.get("min")),
                max_text=_none_if_blank(raw.get("max")),
                bounds=str(raw.get("bounds", "inclusive")),
                type=str(raw.get("type", "money_range")),
            )
        )
    policy_text = str(data.get("policy", "strict")).lower()
    try:
        policy = ExportPolicy(policy_text)
    except ValueError as exc:
        raise ExportValidationError(
            "INVALID_POLICY", f"unknown export policy '{policy_text}'", "policy"
        ) from exc
    return ExportDefinition(
        scope=scope,
        column_keys=tuple(str(k) for k in data.get("columnKeys", []) or []),
        row_selection=selection,
        filters=tuple(filters),
        policy=policy,
        include_issues_sheet=bool(data.get("includeIssuesSheet", False)),
        source_revision=data.get("sourceRevision"),
    )


def _none_if_blank(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def definition_to_dict(definition: ExportDefinition) -> dict:
    data = asdict(definition)
    data["policy"] = definition.policy.value
    data["row_selection"]["mode"] = definition.row_selection.mode.value
    return data


class ExportService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ scope
    def resolve_documents(self, definition: ExportDefinition) -> list[str]:
        scope = definition.scope
        if scope.type == "batch":
            if not scope.batch_id:
                raise ExportValidationError("MISSING_BATCH", "batch scope needs a batchId", "scope")
            rows = self.ctx.documents.list_for_batch(scope.batch_id)
            return [r["id"] for r in rows]
        if not scope.document_ids:
            raise ExportValidationError(
                "MISSING_DOCUMENTS", "document scope needs at least one documentId", "scope"
            )
        resolved = []
        for document_id in scope.document_ids:
            if self.ctx.documents.get(document_id) is None:
                raise ExportValidationError(
                    "UNKNOWN_DOCUMENT", f"document {document_id} does not exist", "scope"
                )
            resolved.append(document_id)
        return resolved

    def profile_for(self, document_ids: Sequence[str]) -> KpirProfile:
        for document_id in document_ids:
            row = self.ctx.documents.get(document_id)
            if row and row["detected_profile_id"]:
                try:
                    return self.ctx.profiles.get(row["detected_profile_id"])
                except Exception:  # noqa: BLE001
                    continue
        return self.ctx.default_profile

    # ------------------------------------------------------------------ preview
    def preview(self, definition: ExportDefinition, *, sample_size: int = 5) -> ExportPreview:
        document_ids = self.resolve_documents(definition)
        profile = self.profile_for(document_ids)
        validate_definition(definition, profile)

        outcome = select_records(self.ctx.records.iter_records(document_ids), definition)
        critical_ids = self.ctx.issues.critical_record_ids(document_ids)
        selected_ids = {r.id for r in outcome.records}
        blocking = len(critical_ids & selected_ids)

        samples = []
        for record in outcome.records[:sample_size]:
            samples.append(
                {
                    "recordId": record.id,
                    "values": {
                        key: (record.cell(key).parsed_value_text if record.cell(key) else None)
                        for key in definition.column_keys
                    },
                }
            )

        return ExportPreview(
            matching_row_count=len(outcome.records),
            column_count=len(definition.column_keys),
            excluded_for_null_or_invalid_count=outcome.excluded_for_null_or_invalid,
            excluded_by_range_count=outcome.excluded_by_range,
            excluded_by_selection_count=outcome.excluded_by_selection,
            total_row_count=self.ctx.records.count(document_ids),
            issue_counts=self.ctx.issues.counts_by_severity(document_ids),
            blocking_issue_count=blocking,
            sample_rows=samples,
            source_revision=self.ctx.records.source_revision(document_ids),
            column_labels=[
                {"key": key, "label": profile.column(key).label_pl}
                for key in definition.column_keys
            ],
            active_filters=[
                {
                    "columnKey": f.column_key,
                    "label": profile.column(f.column_key).label_pl,
                    "min": f.min_text,
                    "max": f.max_text,
                    "bounds": f.bounds,
                }
                for f in definition.filters
            ],
            policy=definition.policy.value,
        )

    # ------------------------------------------------------------------ create
    def create(self, definition: ExportDefinition) -> tuple[str, str]:
        """Persist the immutable snapshot and enqueue the export job."""
        document_ids = self.resolve_documents(definition)
        profile = self.profile_for(document_ids)
        validate_definition(definition, profile)

        current_revision = self.ctx.records.source_revision(document_ids)
        if definition.source_revision and definition.source_revision != current_revision:
            raise ExportValidationError(
                "STALE_PREVIEW",
                "the data changed since the preview; refresh before exporting",
                "sourceRevision",
            )

        if definition.policy is ExportPolicy.STRICT:
            preview = self.preview(definition, sample_size=0)
            if preview.blocking_issue_count > 0:
                raise ExportValidationError(
                    "BLOCKING_ISSUES",
                    f"{preview.blocking_issue_count} selected rows have open critical issues",
                    "policy",
                )

        export_id = self.ctx.exports.create(
            definition_json=json.dumps(definition_to_dict(definition), ensure_ascii=False),
            policy=definition.policy.value,
            document_ids=document_ids,
            source_revision=current_revision,
            batch_id=definition.scope.batch_id,
        )
        job_id = self.ctx.jobs.enqueue(
            JobType.EXPORT_XLSX.value,
            export_id=export_id,
            batch_id=definition.scope.batch_id,
            priority=50,
        )
        self.ctx.exports.attach_job(export_id, job_id)
        return export_id, job_id

    # ------------------------------------------------------------------ build
    def build(self, export_id: str, *, should_cancel: object = None) -> Path:
        """Executed by the worker. Reads only the frozen snapshot."""
        export = self.ctx.exports.get(export_id)
        if export is None:
            raise KeyError(export_id)
        definition = _definition_from_snapshot(json.loads(export["definition_json"]))
        document_ids = self.ctx.exports.documents(export_id)
        profile = self.profile_for(document_ids)

        outcome = select_records(self.ctx.records.iter_records(document_ids), definition)

        issues_payload: list[dict] = []
        if definition.include_issues_sheet:
            rows, _total = self.ctx.issues.list(document_ids, status=None, limit=5000)
            selected = {r.id for r in outcome.records}
            for row in rows:
                if row["record_id"] and row["record_id"] not in selected:
                    continue
                details = json.loads(row["details_json"]) if row["details_json"] else {}
                issues_payload.append(
                    {
                        "code": row["code"],
                        "severity": row["severity"],
                        "page_number": row["page_number"],
                        "column_key": details.get("column_key", ""),
                        "details": json.dumps(details, ensure_ascii=False) if details else "",
                    }
                )

        target = self.ctx.settings.exports_dir / export_id / "result.xlsx"
        metadata = {
            "Wersja ekstraktora": EXTRACTOR_VERSION,
            "Profil": profile.qualified_id,
            "Polityka eksportu": definition.policy.value,
            "Liczba wierszy": str(len(outcome.records)),
            "Kolumny": ", ".join(definition.column_keys),
            "Wersja danych": export["source_revision"] or "",
            "Utworzono (UTC)": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        }
        result = write_export(
            target,
            profile,
            definition.column_keys,
            outcome.records,
            issues=issues_payload,
            include_issues_sheet=definition.include_issues_sheet,
            metadata=metadata if definition.policy is ExportPolicy.DRAFT else None,
        )
        self.ctx.exports.mark_ready(
            export_id,
            path=str(result.path),
            sha256=result.sha256,
            size_bytes=result.size_bytes,
            row_count=result.row_count,
            column_count=result.column_count,
            extractor_version=EXTRACTOR_VERSION,
            profile_id=profile.id,
            profile_version=profile.version,
        )
        return result.path


def _definition_from_snapshot(data: dict) -> ExportDefinition:
    scope = data["scope"]
    selection = data["row_selection"]
    return ExportDefinition(
        scope=ExportScope(
            type=scope["type"],
            document_ids=tuple(scope.get("document_ids") or ()),
            batch_id=scope.get("batch_id"),
        ),
        column_keys=tuple(data["column_keys"]),
        row_selection=RowSelection(
            mode=RowSelectionMode(selection["mode"]),
            included_record_ids=tuple(selection.get("included_record_ids") or ()),
            excluded_record_ids=tuple(selection.get("excluded_record_ids") or ()),
        ),
        filters=tuple(
            MoneyRangeFilter(
                column_key=f["column_key"],
                min_text=f.get("min_text"),
                max_text=f.get("max_text"),
                bounds=f.get("bounds", "inclusive"),
                type=f.get("type", "money_range"),
            )
            for f in data.get("filters") or ()
        ),
        policy=ExportPolicy(data.get("policy", "strict")),
        include_issues_sheet=bool(data.get("include_issues_sheet", False)),
        source_revision=data.get("source_revision"),
    )
