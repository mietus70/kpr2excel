"""Export preview, creation, download and presets."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ..application.services import ExportService, definition_from_dict
from ..domain.export import ExportValidationError
from .deps import api_error, get_context, verify_csrf
from .schemas import (
    ActiveFilterOut,
    ColumnLabelOut,
    ExportDefinitionIn,
    ExportListOut,
    ExportOut,
    ExportPreviewOut,
    PresetIn,
    PresetOut,
    SampleRowOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["export"])

_PL_ERRORS = {
    "NO_COLUMNS_SELECTED": "wybierz co najmniej jedną kolumnę",
    "UNKNOWN_COLUMN": "wybrana kolumna nie istnieje w profilu",
    "DUPLICATE_COLUMN": "kolumna została wybrana dwukrotnie",
    "UNKNOWN_FILTER_COLUMN": "kolumna filtra nie istnieje w profilu",
    "FILTER_COLUMN_TYPE_MISMATCH": "filtr kwotowy działa tylko na kolumnach kwotowych",
    "INVALID_FILTER_BOUND": "granica filtra nie jest poprawną kwotą",
    "MIN_GREATER_THAN_MAX": "kwota „od” jest większa niż kwota „do”",
    "EMPTY_FILTER": "podaj co najmniej jedną granicę filtra",
    "NO_ROWS_SELECTED": "zaznacz co najmniej jeden wiersz",
    "STALE_PREVIEW": "dane zmieniły się od czasu podglądu - odśwież podgląd",
    "BLOCKING_ISSUES": "wybrane wiersze mają otwarte problemy krytyczne",
    "MISSING_DOCUMENTS": "wskaż co najmniej jeden dokument",
    "UNKNOWN_DOCUMENT": "wskazany dokument nie istnieje",
}


def _map_validation_error(exc: ExportValidationError):
    status = 409 if exc.code in ("STALE_PREVIEW", "BLOCKING_ISSUES") else 422
    return api_error(status, exc.code, _PL_ERRORS.get(exc.code, exc.message), field=exc.field)


def _definition(request: Request, body: ExportDefinitionIn):
    ctx = get_context(request)
    payload = body.model_dump(by_alias=True)
    service = ExportService(ctx)
    try:
        document_ids = service.resolve_documents(definition_from_dict(payload, ctx.default_profile))
    except ExportValidationError as exc:
        raise _map_validation_error(exc) from exc
    profile = service.profile_for(document_ids)
    return service, definition_from_dict(payload, profile)


@router.post("/export-previews", response_model=ExportPreviewOut)
def create_preview(
    request: Request, body: ExportDefinitionIn, _: Annotated[None, Depends(verify_csrf)]
) -> ExportPreviewOut:
    service, definition = _definition(request, body)
    try:
        preview = service.preview(definition)
    except ExportValidationError as exc:
        raise _map_validation_error(exc) from exc
    return ExportPreviewOut(
        matchingRowCount=preview.matching_row_count,
        columnCount=preview.column_count,
        excludedForNullOrInvalidCount=preview.excluded_for_null_or_invalid_count,
        excludedByRangeCount=preview.excluded_by_range_count,
        excludedBySelectionCount=preview.excluded_by_selection_count,
        totalRowCount=preview.total_row_count,
        issueCounts=preview.issue_counts,
        blockingIssueCount=preview.blocking_issue_count,
        sampleRows=[
            SampleRowOut(recordId=s["recordId"], values=s["values"]) for s in preview.sample_rows
        ],
        sourceRevision=preview.source_revision,
        columnLabels=[ColumnLabelOut(**c) for c in preview.column_labels],
        activeFilters=[
            ActiveFilterOut(
                columnKey=f["columnKey"],
                label=f["label"],
                min=f["min"],
                max=f["max"],
                bounds=f["bounds"],
            )
            for f in preview.active_filters
        ],
        policy=preview.policy,
    )


def _export_out(row, ctx) -> ExportOut:  # type: ignore[no-untyped-def]
    return ExportOut(
        id=row["id"],
        status=row["status"],
        policy=row["policy"],
        jobId=row["job_id"],
        rowCount=row["result_row_count"],
        columnCount=row["result_column_count"],
        sizeBytes=row["size_bytes"],
        sha256=row["sha256"],
        sourceRevision=row["source_revision"],
        createdAt=row["created_at"],
        errorCode=row["error_code"],
        downloadUrl=(f"/api/v1/exports/{row['id']}/file" if row["status"] == "ready" else None),
    )


@router.post("/exports", response_model=ExportOut, status_code=202)
def create_export(
    request: Request, body: ExportDefinitionIn, _: Annotated[None, Depends(verify_csrf)]
) -> ExportOut:
    ctx = get_context(request)
    service, definition = _definition(request, body)
    try:
        export_id, _job_id = service.create(definition)
    except ExportValidationError as exc:
        raise _map_validation_error(exc) from exc
    row = ctx.exports.get(export_id)
    assert row is not None
    return _export_out(row, ctx)


@router.get("/exports", response_model=ExportListOut)
def list_exports(request: Request) -> ExportListOut:
    ctx = get_context(request)
    return ExportListOut(items=[_export_out(row, ctx) for row in ctx.exports.list()])


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(request: Request, export_id: str) -> ExportOut:
    ctx = get_context(request)
    row = ctx.exports.get(export_id)
    if row is None:
        raise api_error(404, "EXPORT_NOT_FOUND", "eksport nie istnieje")
    return _export_out(row, ctx)


@router.get("/exports/{export_id}/file")
def download_export(request: Request, export_id: str) -> FileResponse:
    ctx = get_context(request)
    row = ctx.exports.get(export_id)
    if row is None or row["status"] != "ready" or not row["path"]:
        raise api_error(404, "EXPORT_NOT_READY", "plik eksportu nie jest gotowy")
    path = Path(row["path"]).resolve()
    if not str(path).startswith(str(ctx.settings.exports_dir.resolve())) or not path.exists():
        raise api_error(404, "EXPORT_FILE_MISSING", "plik eksportu nie istnieje")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"kpir-{export_id[:8]}.xlsx",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="kpir-{export_id[:8]}.xlsx"',
        },
    )


@router.delete("/exports/{export_id}", status_code=204)
def delete_export(
    request: Request, export_id: str, _: Annotated[None, Depends(verify_csrf)]
) -> Response:
    import shutil

    ctx = get_context(request)
    row = ctx.exports.get(export_id)
    if row is None:
        raise api_error(404, "EXPORT_NOT_FOUND", "eksport nie istnieje")
    if row["path"]:
        shutil.rmtree(Path(row["path"]).parent, ignore_errors=True)
    ctx.exports.delete(export_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ presets


@router.get("/export-presets", response_model=list[PresetOut])
def list_presets(request: Request) -> list[PresetOut]:
    ctx = get_context(request)
    return [
        PresetOut(
            id=row["id"],
            name=row["name"],
            profileId=row["profile_id"],
            definition=json.loads(row["definition_json"]),
            createdAt=row["created_at"],
        )
        for row in ctx.presets.list()
    ]


@router.post("/export-presets", response_model=PresetOut, status_code=201)
def create_preset(
    request: Request, body: PresetIn, _: Annotated[None, Depends(verify_csrf)]
) -> PresetOut:
    ctx = get_context(request)
    try:
        preset_id = ctx.presets.create(
            body.name, body.profile_id, json.dumps(body.definition, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001 - unique name violation
        raise api_error(409, "PRESET_NAME_TAKEN", "preset o tej nazwie już istnieje") from exc
    row = next((r for r in ctx.presets.list() if r["id"] == preset_id), None)
    assert row is not None
    return PresetOut(
        id=row["id"],
        name=row["name"],
        profileId=row["profile_id"],
        definition=json.loads(row["definition_json"]),
        createdAt=row["created_at"],
    )


@router.delete("/export-presets/{preset_id}", status_code=204)
def delete_preset(
    request: Request, preset_id: str, _: Annotated[None, Depends(verify_csrf)]
) -> Response:
    ctx = get_context(request)
    if not ctx.presets.delete(preset_id):
        raise api_error(404, "PRESET_NOT_FOUND", "preset nie istnieje")
    return Response(status_code=204)
