"""Shared API dependencies, error mapping and CSRF protection."""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import uuid
from typing import Any

from fastapi import HTTPException, Request

from ..application.services import AppContext
from ..domain.models import Cell, KpirRecord
from .schemas import CellOut, IssueOut, JobOut, RecordOut, SourceSpanOut

logger = logging.getLogger(__name__)

CSRF_COOKIE = "kpir_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def api_error(
    status_code: int, code: str, message: str, *, field: str | None = None
) -> HTTPException:
    correlation_id = new_correlation_id()
    logger.info("api error", extra={"error_code": code, "correlation_id": correlation_id})
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "field": field,
                "correlationId": correlation_id,
            }
        },
    )


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(request: Request) -> None:
    """Double-submit cookie check for state changing requests."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise api_error(403, "CSRF_TOKEN_INVALID", "brak lub niepoprawny token CSRF")


# ------------------------------------------------------------------ serialisation


def cell_to_out(cell: Cell) -> CellOut:
    return CellOut(
        id=cell.id,
        columnKey=cell.column_key,
        value=cell.parsed_value_text,
        valueType=cell.value_type.value,
        rawText=cell.raw_text,
        extractedValue=cell.extraction_meta.get("extracted_value"),
        isManual=cell.is_manual,
        confidence=round(cell.confidence.score, 4),
        confidenceParts={k: v for k, v in cell.confidence.components},
        revision=cell.revision,
        source=(
            SourceSpanOut(page=cell.source.page_number, bbox=list(cell.source.bbox.as_tuple()))
            if cell.source
            else None
        ),
    )


def record_to_out(record: KpirRecord, document_id: str) -> RecordOut:
    return RecordOut(
        id=record.id,
        documentId=document_id,
        logicalIndex=record.logical_index,
        pageFrom=record.source_page_from,
        pageTo=record.source_page_to,
        status=record.status.value,
        confidence=round(record.confidence, 4),
        revision=record.revision,
        cells={key: cell_to_out(cell) for key, cell in record.cells.items()},
    )


def issue_to_out(row: sqlite3.Row) -> IssueOut:
    details: dict[str, Any] = {}
    if row["details_json"]:
        try:
            details = json.loads(row["details_json"])
        except json.JSONDecodeError:
            details = {}
    return IssueOut(
        id=row["id"],
        code=row["code"],
        severity=row["severity"],
        messageKey=row["message_key"],
        documentId=row["document_id"],
        pageNumber=row["page_number"],
        recordId=row["record_id"],
        cellId=row["cell_id"],
        status=row["status"],
        details=details,
        createdAt=row["created_at"],
    )


def job_to_out(row: sqlite3.Row) -> JobOut:
    return JobOut(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        documentId=row["document_id"],
        batchId=row["batch_id"],
        exportId=row["export_id"],
        progressCurrent=int(row["progress_current"] or 0),
        progressTotal=int(row["progress_total"] or 0),
        progressPhase=row["progress_phase"],
        attempt=int(row["attempt"] or 0),
        errorCode=row["error_code"],
        createdAt=row["created_at"],
        finishedAt=row["finished_at"],
    )
