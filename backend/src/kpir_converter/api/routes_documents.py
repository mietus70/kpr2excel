"""Import, documents, records, cells, issues."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..application.services import ImportError_, ImportService, ReviewService, safe_display_name
from ..domain.values import ParseError
from .deps import (
    api_error,
    cell_to_out,
    get_context,
    issue_to_out,
    record_to_out,
    verify_csrf,
)
from .schemas import (
    BatchCreate,
    BatchOut,
    CellPatch,
    CellPatchOut,
    CellRevert,
    CorrectionHistoryOut,
    DocumentImportOut,
    DocumentOut,
    IssuePageOut,
    ProfileColumnOut,
    ProfileOut,
    RecordOut,
    RecordPageOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["documents"])

CHUNK_SIZE = 1024 * 1024


# ------------------------------------------------------------------ profiles


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(request: Request) -> list[ProfileOut]:
    ctx = get_context(request)
    return [
        ProfileOut(
            id=p.id,
            version=p.version,
            qualifiedId=p.qualified_id,
            displayName=p.display_name,
            columns=[
                ProfileColumnOut(
                    key=c.key,
                    label=c.label_pl,
                    type=c.type,
                    formNumber=c.form_number,
                    required=c.required,
                )
                for c in p.columns
            ],
            defaultExportColumns=list(p.export_columns_default()),
            defaultFilterColumn=p.default_filter_column,
        )
        for p in ctx.profiles.all()
    ]


# ------------------------------------------------------------------ batches


@router.post("/batches", response_model=BatchOut, status_code=201)
def create_batch(
    request: Request, body: BatchCreate, _: Annotated[None, Depends(verify_csrf)]
) -> BatchOut:
    ctx = get_context(request)
    batch_id = ImportService(ctx).create_batch(body.display_name)
    row = ctx.batches.get(batch_id)
    assert row is not None
    return BatchOut(
        id=row["id"],
        displayName=row["display_name"],
        createdAt=row["created_at"],
        documentCount=0,
    )


@router.get("/batches", response_model=list[BatchOut])
def list_batches(request: Request) -> list[BatchOut]:
    ctx = get_context(request)
    result = []
    for row in ctx.batches.list():
        documents = ctx.documents.list_for_batch(row["id"])
        result.append(
            BatchOut(
                id=row["id"],
                displayName=row["display_name"],
                createdAt=row["created_at"],
                documentCount=len(documents),
            )
        )
    return result


@router.get("/batches/{batch_id}", response_model=BatchOut)
def get_batch(request: Request, batch_id: str) -> BatchOut:
    ctx = get_context(request)
    row = ctx.batches.get(batch_id)
    if row is None:
        raise api_error(404, "BATCH_NOT_FOUND", "paczka nie istnieje")
    return BatchOut(
        id=row["id"],
        displayName=row["display_name"],
        createdAt=row["created_at"],
        documentCount=len(ctx.documents.list_for_batch(batch_id)),
    )


@router.post("/batches/{batch_id}/documents", response_model=DocumentImportOut, status_code=201)
async def upload_documents(
    request: Request,
    batch_id: str,
    _: Annotated[None, Depends(verify_csrf)],
    files: Annotated[list[UploadFile], File(...)],
) -> DocumentImportOut:
    ctx = get_context(request)
    if ctx.batches.get(batch_id) is None:
        raise api_error(404, "BATCH_NOT_FOUND", "paczka nie istnieje")
    service = ImportService(ctx)

    imported = []
    job_ids: list[str] = []
    for upload in files:
        # The upload is streamed to disk chunk by chunk: a multi-hundred-megabyte
        # PDF must never be materialised in memory. ``SpooledTemporaryFile``
        # behind UploadFile already spills to disk, so we read it synchronously
        # inside the generator that ImportService consumes.
        source = upload.file
        source.seek(0)

        def chunks(handle=source):  # default arg binds the current file object
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    return
                yield chunk

        try:
            document = service.import_stream(
                batch_id, safe_display_name(upload.filename or "dokument.pdf"), chunks()
            )
        except ImportError_ as exc:
            raise api_error(400, exc.code, _pl_import_error(exc.code, exc.message)) from exc
        finally:
            await upload.close()
        imported.append(_document_out(ctx, document.id))
        jobs = ctx.jobs.list(document_id=document.id, limit=1)
        if jobs:
            job_ids.append(jobs[0]["id"])
    return DocumentImportOut(documents=imported, jobIds=job_ids)


def _pl_import_error(code: str, fallback: str) -> str:
    return {
        "NOT_A_PDF": "plik nie jest dokumentem PDF",
        "FILE_TOO_LARGE": "plik przekracza dozwolony rozmiar",
        "EMPTY_FILE": "plik jest pusty",
        "TOO_MANY_PAGES": "dokument ma zbyt wiele stron",
    }.get(code, fallback)


# ------------------------------------------------------------------ documents


def _document_out(ctx, document_id: str) -> DocumentOut:  # type: ignore[no-untyped-def]
    row = ctx.documents.get(document_id)
    if row is None:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "dokument nie istnieje")
    return DocumentOut(
        id=row["id"],
        batchId=row["batch_id"],
        originalName=row["original_name"],
        sizeBytes=row["size_bytes"],
        pageCount=row["page_count"],
        status=row["status"],
        detectedProfileId=row["detected_profile_id"],
        periodFrom=row["declared_period_from"],
        periodTo=row["declared_period_to"],
        createdAt=row["created_at"],
        recordCount=ctx.records.count([document_id]),
        issueCounts=ctx.issues.counts_by_severity([document_id]),
        errorCode=row["error_code"],
    )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(request: Request, batch_id: str | None = Query(None, alias="batchId")):
    ctx = get_context(request)
    rows = ctx.documents.list_for_batch(batch_id) if batch_id else ctx.documents.list_all()
    return [_document_out(ctx, row["id"]) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(request: Request, document_id: str) -> DocumentOut:
    return _document_out(get_context(request), document_id)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    request: Request, document_id: str, _: Annotated[None, Depends(verify_csrf)]
) -> Response:
    ctx = get_context(request)
    try:
        ImportService(ctx).delete_document(document_id)
    except KeyError as exc:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "dokument nie istnieje") from exc
    return Response(status_code=204)


@router.get("/documents/{document_id}/pages/{page_number}/image")
def page_image(request: Request, document_id: str, page_number: int) -> FileResponse:
    from ..application.services import ExtractionService

    ctx = get_context(request)
    if ctx.documents.get(document_id) is None:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "dokument nie istnieje")
    if page_number < 1:
        raise api_error(400, "INVALID_PAGE", "numer strony musi być dodatni")
    try:
        path = ExtractionService(ctx).render_page(document_id, page_number)
    except Exception as exc:  # noqa: BLE001
        raise api_error(404, "PAGE_NOT_FOUND", "nie można wyrenderować strony") from exc
    # The path is built from internal UUIDs only; never from user input.
    resolved = Path(path).resolve()
    if not str(resolved).startswith(str(ctx.settings.pages_dir.resolve())):
        raise api_error(400, "INVALID_PATH", "niedozwolona ścieżka pliku")
    media_type = "image/webp" if resolved.suffix == ".webp" else "image/png"
    return FileResponse(
        resolved,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/documents/{document_id}/records", response_model=RecordPageOut)
def list_records(
    request: Request,
    document_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    only_issues: bool = Query(False, alias="onlyIssues"),
    status: str | None = Query(None),
) -> RecordPageOut:
    ctx = get_context(request)
    if ctx.documents.get(document_id) is None:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "dokument nie istnieje")
    try:
        page = ctx.records.page_records(
            [document_id], cursor=cursor, limit=limit, only_issues=only_issues, status=status
        )
    except ValueError as exc:
        raise api_error(400, "INVALID_CURSOR", "niepoprawny kursor paginacji") from exc
    return RecordPageOut(
        items=[record_to_out(r, document_id) for r in page.records],
        nextCursor=page.next_cursor,
        total=page.total,
    )


@router.get("/records/{record_id}", response_model=RecordOut)
def get_record(request: Request, record_id: str) -> RecordOut:
    ctx = get_context(request)
    record = ctx.records.get_record(record_id)
    if record is None:
        raise api_error(404, "RECORD_NOT_FOUND", "rekord nie istnieje")
    row = ctx.db.query_one("SELECT document_id FROM records WHERE id = ?", (record_id,))
    return record_to_out(record, row["document_id"] if row else "")


# ------------------------------------------------------------------ cells


@router.patch("/cells/{cell_id}", response_model=CellPatchOut)
def patch_cell(
    request: Request, cell_id: str, body: CellPatch, _: Annotated[None, Depends(verify_csrf)]
):
    ctx = get_context(request)
    owner = ctx.records.cell_document(cell_id)
    if owner is None:
        raise api_error(404, "CELL_NOT_FOUND", "komórka nie istnieje")
    existing = ctx.records.get_cell(cell_id)
    assert existing is not None

    document = ctx.documents.get(owner["document_id"])
    profile = ctx.default_profile
    if document and document["detected_profile_id"]:
        with contextlib.suppress(Exception):
            profile = ctx.profiles.get(document["detected_profile_id"])

    review = ReviewService(ctx)
    try:
        value_text, value_type = review.parse_value(profile, existing["column_key"], body.value)
    except ParseError as exc:
        raise api_error(422, exc.code, _pl_parse_error(exc.code), field="value") from exc

    try:
        applied, revision = ctx.records.apply_correction(
            cell_id,
            value_text=value_text,
            value_type=value_type,
            base_revision=body.base_revision,
            reason=body.reason,
        )
    except KeyError as exc:
        raise api_error(404, "CELL_NOT_FOUND", "komórka nie istnieje") from exc

    if not applied:
        current = ctx.records.get_cell(cell_id)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "REVISION_CONFLICT",
                    "message": "komórka została zmieniona w międzyczasie",
                    "currentRevision": revision,
                    "currentValue": current["effective_value"] if current else None,
                    "correlationId": "",
                }
            },
        )

    review.revalidate_record(owner["record_id"], profile)
    updated = ctx.records.get_record(owner["record_id"])
    cell = updated.cell(existing["column_key"]) if updated else None
    assert cell is not None
    return CellPatchOut(
        cell=cell_to_out(cell), recordStatus=updated.status.value if updated else "ok"
    )


def _pl_parse_error(code: str) -> str:
    return {
        "INVALID_MONEY": "niepoprawny format kwoty",
        "MONEY_SCALE_EXCEEDED": "kwota ma więcej niż dwa miejsca po przecinku",
        "INVALID_DATE": "niepoprawny format daty",
        "INVALID_INTEGER": "wartość musi być liczbą całkowitą",
    }.get(code, "niepoprawna wartość")


@router.delete("/cells/{cell_id}/correction", response_model=CellPatchOut)
def revert_cell(
    request: Request, cell_id: str, body: CellRevert, _: Annotated[None, Depends(verify_csrf)]
):
    ctx = get_context(request)
    owner = ctx.records.cell_document(cell_id)
    if owner is None:
        raise api_error(404, "CELL_NOT_FOUND", "komórka nie istnieje")
    existing = ctx.records.get_cell(cell_id)
    assert existing is not None
    try:
        applied, revision = ctx.records.revert_correction(cell_id, body.base_revision)
    except KeyError as exc:
        raise api_error(404, "CELL_NOT_FOUND", "komórka nie istnieje") from exc
    if not applied:
        current = ctx.records.get_cell(cell_id)
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "REVISION_CONFLICT",
                    "message": "komórka została zmieniona w międzyczasie",
                    "currentRevision": revision,
                    "currentValue": current["effective_value"] if current else None,
                    "correlationId": "",
                }
            },
        )
    document = ctx.documents.get(owner["document_id"])
    profile = ctx.default_profile
    if document and document["detected_profile_id"]:
        with contextlib.suppress(Exception):
            profile = ctx.profiles.get(document["detected_profile_id"])
    ReviewService(ctx).revalidate_record(owner["record_id"], profile)
    updated = ctx.records.get_record(owner["record_id"])
    cell = updated.cell(existing["column_key"]) if updated else None
    assert cell is not None
    return CellPatchOut(
        cell=cell_to_out(cell), recordStatus=updated.status.value if updated else "ok"
    )


@router.get("/cells/{cell_id}/history", response_model=list[CorrectionHistoryOut])
def cell_history(request: Request, cell_id: str) -> list[CorrectionHistoryOut]:
    ctx = get_context(request)
    rows = ctx.records.cell_history(cell_id)
    return [
        CorrectionHistoryOut(
            id=row["id"],
            action=row["action"],
            oldValue=row["old_value_text"],
            newValue=row["new_value_text"],
            reason=row["reason"],
            actor=row["actor"],
            createdAt=row["created_at"],
        )
        for row in rows
    ]


# ------------------------------------------------------------------ issues


@router.get("/documents/{document_id}/issues", response_model=IssuePageOut)
def list_issues(
    request: Request,
    document_id: str,
    severity: str | None = Query(None),
    status: str | None = Query("open"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> IssuePageOut:
    ctx = get_context(request)
    if ctx.documents.get(document_id) is None:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "dokument nie istnieje")
    rows, total = ctx.issues.list(
        [document_id], severity=severity, status=status, limit=limit, offset=offset
    )
    return IssuePageOut(items=[issue_to_out(row) for row in rows], total=total)


@router.post("/issues/{issue_id}/acknowledge", status_code=204)
def acknowledge_issue(
    request: Request, issue_id: str, _: Annotated[None, Depends(verify_csrf)]
) -> Response:
    ctx = get_context(request)
    if not ctx.issues.acknowledge(issue_id):
        raise api_error(404, "ISSUE_NOT_FOUND", "problem nie istnieje lub jest już zamknięty")
    return Response(status_code=204)
