"""Durable worker loop.

Claims jobs atomically from SQLite, renews a lease with a heartbeat, checks the
cancellation flag between pages and isolates per-document failures.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from typing import Any

from ..application.services import AppContext, ExportService, ExtractionService
from ..config import Settings, get_settings
from ..domain.models import JobStatus, JobType
from ..infrastructure.db.database import Database
from ..infrastructure.storage.profiles_loader import load_registry
from ..logging_setup import configure_logging

logger = logging.getLogger(__name__)

LEASE_SECONDS = 60
HEARTBEAT_INTERVAL = 15
IDLE_SLEEP = 0.5
PROGRESS_MIN_INTERVAL = 0.75


class Worker:
    def __init__(self, ctx: AppContext, *, owner: str | None = None) -> None:
        self.ctx = ctx
        self.owner = owner or f"{socket.gethostname()}-{os.getpid()}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ loop
    def run_forever(self) -> None:
        logger.info("worker started", extra={"owner": self.owner})
        last_reclaim = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_reclaim > 10:
                reclaimed = self.ctx.jobs.reclaim_expired()
                if reclaimed:
                    logger.warning("reclaimed interrupted jobs", extra={"count": reclaimed})
                last_reclaim = now
            if not self.run_once():
                self._stop.wait(IDLE_SLEEP)
        logger.info("worker stopped", extra={"owner": self.owner})

    def run_once(self) -> bool:
        """Claim and execute a single job. Returns False when the queue is idle."""
        job = self.ctx.jobs.claim_next(self.owner, lease_seconds=LEASE_SECONDS)
        if job is None:
            return False
        job_id = job["id"]
        job_type = job["type"]
        logger.info("job started", extra={"job_id": job_id, "job_type": job_type})

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(job_id, heartbeat_stop), daemon=True
        )
        heartbeat.start()
        try:
            self._dispatch(job)
            if self.ctx.jobs.is_cancel_requested(job_id):
                self.ctx.jobs.finish(job_id, JobStatus.CANCELLED)
            else:
                self.ctx.jobs.finish(job_id, JobStatus.SUCCEEDED)
        except Exception as exc:  # noqa: BLE001 - a failed job must not kill the worker
            code = getattr(exc, "code", type(exc).__name__)
            logger.error(
                "job failed",
                extra={"job_id": job_id, "job_type": job_type, "error_code": code},
            )
            attempt = int(job["attempt"] or 0)
            max_attempts = int(job["max_attempts"] or 3)
            if attempt < max_attempts and _is_retryable(code):
                self.ctx.jobs.retry(job_id)
            else:
                self.ctx.jobs.finish(
                    job_id, JobStatus.FAILED, error_code=str(code), error_summary=type(exc).__name__
                )
        finally:
            heartbeat_stop.set()
        return True

    def _heartbeat_loop(self, job_id: str, stop: threading.Event) -> None:
        # A separate DB connection is created per thread by Database.
        while not stop.wait(HEARTBEAT_INTERVAL):
            try:
                self.ctx.jobs.heartbeat(job_id, self.owner, lease_seconds=LEASE_SECONDS)
            except Exception:  # noqa: BLE001 - heartbeat must never crash the job
                logger.debug("heartbeat failed", extra={"job_id": job_id})

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, job: Any) -> None:
        job_id = job["id"]
        job_type = job["type"]

        def should_cancel() -> bool:
            return self._stop.is_set() or self.ctx.jobs.is_cancel_requested(job_id)

        last_report = 0.0

        def progress(current: int, total: int, phase: str) -> None:
            nonlocal last_report
            now = time.monotonic()
            # Rate limited so a thousand pages do not mean a thousand writes.
            if now - last_report >= PROGRESS_MIN_INTERVAL or current >= total:
                self.ctx.jobs.update_progress(job_id, current, total, phase)
                last_report = now

        if job_type in (JobType.EXTRACT_DOCUMENT.value, JobType.REPROCESS_DOCUMENT.value):
            service = ExtractionService(self.ctx)
            service.run(
                job["document_id"],
                job_id=job_id,
                should_cancel=should_cancel,
                progress=progress,
            )
        elif job_type == JobType.EXPORT_XLSX.value:
            self.ctx.jobs.update_progress(job_id, 0, 1, "export")
            service = ExportService(self.ctx)
            try:
                service.build(job["export_id"], should_cancel=should_cancel)
            except Exception as exc:  # noqa: BLE001
                self.ctx.exports.mark_failed(
                    job["export_id"], getattr(exc, "code", "EXPORT_FAILED"), type(exc).__name__
                )
                raise
            self.ctx.jobs.update_progress(job_id, 1, 1, "export")
        elif job_type == JobType.RENDER_PAGE.value:
            import json

            payload = json.loads(job["payload_json"] or "{}")
            ExtractionService(self.ctx).render_page(
                job["document_id"], int(payload.get("pageNumber", 1))
            )
        else:
            raise RuntimeError(f"unsupported job type: {job_type}")


def _is_retryable(code: str) -> bool:
    return code not in {
        "NOT_A_PDF",
        "PDF_ENCRYPTED",
        "OCR_REQUIRED",
        "TOO_MANY_PAGES",
        "FILE_TOO_LARGE",
        "BLOCKING_ISSUES",
    }


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.migrate()
    profiles = load_registry(settings.resolved_profiles_dir())
    return AppContext(settings=settings, db=db, profiles=profiles)


def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings)
    ctx = build_context(settings)
    worker = Worker(ctx)

    def handle_signal(_signum: int, _frame: object) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    worker.run_forever()


if __name__ == "__main__":  # pragma: no cover
    run_worker()
