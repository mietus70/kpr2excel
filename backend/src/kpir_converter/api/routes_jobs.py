"""Jobs, progress SSE and cancellation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from .deps import api_error, get_context, job_to_out, verify_csrf
from .schemas import JobListOut, JobOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["jobs"])

SSE_INTERVAL = 1.0


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    request: Request,
    batch_id: str | None = Query(None, alias="batchId"),
    document_id: str | None = Query(None, alias="documentId"),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> JobListOut:
    ctx = get_context(request)
    rows = ctx.jobs.list(batch_id=batch_id, document_id=document_id, status=status, limit=limit)
    return JobListOut(items=[job_to_out(row) for row in rows])


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(request: Request, job_id: str) -> JobOut:
    ctx = get_context(request)
    row = ctx.jobs.get(job_id)
    if row is None:
        raise api_error(404, "JOB_NOT_FOUND", "zadanie nie istnieje")
    return job_to_out(row)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(request: Request, job_id: str, _: Annotated[None, Depends(verify_csrf)]) -> JobOut:
    ctx = get_context(request)
    if ctx.jobs.get(job_id) is None:
        raise api_error(404, "JOB_NOT_FOUND", "zadanie nie istnieje")
    if not ctx.jobs.request_cancel(job_id):
        raise api_error(409, "JOB_NOT_CANCELLABLE", "zadanie nie jest już aktywne")
    row = ctx.jobs.get(job_id)
    assert row is not None
    return job_to_out(row)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(request: Request, job_id: str, _: Annotated[None, Depends(verify_csrf)]) -> JobOut:
    ctx = get_context(request)
    if ctx.jobs.get(job_id) is None:
        raise api_error(404, "JOB_NOT_FOUND", "zadanie nie istnieje")
    if not ctx.jobs.retry(job_id):
        raise api_error(
            409, "JOB_NOT_RETRYABLE", "zadanie nie jest w stanie pozwalającym na ponowienie"
        )
    row = ctx.jobs.get(job_id)
    assert row is not None
    return job_to_out(row)


@router.get("/events")
async def events(request: Request, batch_id: str | None = Query(None, alias="batchId")):
    """SSE progress stream. Polling /jobs is the documented fallback."""
    ctx = get_context(request)

    async def generator():
        previous: dict[str, str] = {}
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            rows = ctx.jobs.list(batch_id=batch_id, limit=100)
            payloads = []
            for row in rows:
                job = job_to_out(row)
                signature = f"{job.status}:{job.progress_current}:{job.progress_total}"
                if previous.get(job.id) != signature:
                    previous[job.id] = signature
                    payloads.append(job.model_dump(by_alias=True))
            if payloads:
                data = json.dumps({"jobs": payloads}, ensure_ascii=False)
                yield f"event: jobs\ndata: {data}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(SSE_INTERVAL)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
