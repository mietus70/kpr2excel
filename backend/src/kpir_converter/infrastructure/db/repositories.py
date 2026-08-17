"""SQLite repositories.

The *effective* value of a cell is the manual correction when one exists,
otherwise the extraction result. That resolution happens here, once, in SQL, so
that reads, filters and exports can never disagree.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ...domain.models import (
    Cell,
    Document,
    Issue,
    JobStatus,
    KpirRecord,
    Page,
    RecordStatus,
    SourceSpan,
    ValueType,
    new_id,
    utc_now,
)
from ...domain.values import BBox, Confidence
from .database import Database

__all__ = [
    "BatchRepository",
    "DocumentRepository",
    "PageRepository",
    "RunRepository",
    "RecordRepository",
    "IssueRepository",
    "JobRepository",
    "ExportRepository",
    "PresetRepository",
    "iso",
]


def iso(value: _dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------------------


class BatchRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, display_name: str) -> str:
        batch_id = new_id()
        now = iso()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO batches(id, display_name, created_at, updated_at) VALUES (?,?,?,?)",
                (batch_id, display_name, now, now),
            )
        return batch_id

    def get(self, batch_id: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM batches WHERE id = ?", (batch_id,))

    def list(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.query_all("SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,))

    def touch(self, batch_id: str) -> None:
        self.db.execute("UPDATE batches SET updated_at = ? WHERE id = ?", (iso(), batch_id))


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, document: Document) -> str:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO documents(id, batch_id, original_name, stored_path, sha256,"
                " size_bytes, page_count, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    document.id,
                    document.batch_id,
                    document.original_name,
                    document.stored_path,
                    document.sha256,
                    document.size_bytes,
                    document.page_count,
                    document.status.value,
                    iso(document.created_at),
                ),
            )
        return document.id

    def get(self, document_id: str) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", (document_id,)
        )

    def list_for_batch(self, batch_id: str) -> list[sqlite3.Row]:
        return self.db.query_all(
            "SELECT * FROM documents WHERE batch_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (batch_id,),
        )

    def list_all(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.db.query_all(
            "SELECT * FROM documents WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def set_status(
        self,
        document_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE documents SET status = ?, error_code = ?, error_summary = ? WHERE id = ?",
            (status, error_code, error_summary, document_id),
        )

    def set_analysis(
        self,
        document_id: str,
        *,
        page_count: int,
        profile_id: str | None,
        period_from: str | None,
        period_to: str | None,
    ) -> None:
        self.db.execute(
            "UPDATE documents SET page_count = ?, detected_profile_id = ?,"
            " declared_period_from = ?, declared_period_to = ? WHERE id = ?",
            (page_count, profile_id, period_from, period_to, document_id),
        )

    def find_by_sha(self, sha256: str) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT * FROM documents WHERE sha256 = ? AND deleted_at IS NULL LIMIT 1", (sha256,)
        )

    def hard_delete(self, document_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            conn.execute("DELETE FROM issues WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM corrections_history WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM jobs WHERE document_id = ?", (document_id,))


class PageRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_many(self, pages: Sequence[Page]) -> None:
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO pages(id, document_id, page_number, width, height, rotation,"
                " text_available, status) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(document_id, page_number) DO UPDATE SET"
                " width=excluded.width, height=excluded.height, rotation=excluded.rotation,"
                " text_available=excluded.text_available, status=excluded.status",
                [
                    (
                        p.id,
                        p.document_id,
                        p.page_number,
                        p.width,
                        p.height,
                        p.rotation,
                        int(p.text_available),
                        p.status.value,
                    )
                    for p in pages
                ],
            )

    def set_status(
        self,
        document_id: str,
        page_number: int,
        status: str,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE pages SET status = ?, error_code = ?, error_summary = ?"
            " WHERE document_id = ? AND page_number = ?",
            (status, error_code, error_summary, document_id, page_number),
        )

    def set_render(self, document_id: str, page_number: int, path: str) -> None:
        self.db.execute(
            "UPDATE pages SET render_path = ? WHERE document_id = ? AND page_number = ?",
            (path, document_id, page_number),
        )

    def get(self, document_id: str, page_number: int) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT * FROM pages WHERE document_id = ? AND page_number = ?",
            (document_id, page_number),
        )

    def list(self, document_id: str) -> list[sqlite3.Row]:
        return self.db.query_all(
            "SELECT * FROM pages WHERE document_id = ? ORDER BY page_number", (document_id,)
        )


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        document_id: str,
        *,
        extractor_version: str,
        profile_id: str,
        profile_version: int,
        config_hash: str,
    ) -> str:
        run_id = new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE extraction_runs SET is_active = 0 WHERE document_id = ?", (document_id,)
            )
            conn.execute(
                "INSERT INTO extraction_runs(id, document_id, extractor_version, profile_id,"
                " profile_version, config_hash, status, is_active, started_at)"
                " VALUES (?,?,?,?,?,?,'running',1,?)",
                (
                    run_id,
                    document_id,
                    extractor_version,
                    profile_id,
                    profile_version,
                    config_hash,
                    iso(),
                ),
            )
        return run_id

    def active_for_document(self, document_id: str) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT * FROM extraction_runs WHERE document_id = ? AND is_active = 1"
            " ORDER BY started_at DESC LIMIT 1",
            (document_id,),
        )

    def get(self, run_id: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM extraction_runs WHERE id = ?", (run_id,))

    def checkpoint(self, run_id: str, last_page_done: int) -> None:
        self.db.execute(
            "UPDATE extraction_runs SET last_page_done = ? WHERE id = ?", (last_page_done, run_id)
        )

    def finish(self, run_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE extraction_runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, iso(), run_id),
        )


# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RecordPage:
    """A page of records plus the opaque cursor for the next page."""

    records: list[KpirRecord]
    next_cursor: str | None
    total: int


_EFFECTIVE_SELECT = """
SELECT
    c.id                AS cell_id,
    c.record_id         AS record_id,
    c.column_key        AS column_key,
    c.raw_text          AS raw_text,
    c.normalized_text   AS normalized_text,
    c.value_type        AS extracted_type,
    c.parsed_value_text AS extracted_value,
    c.confidence        AS confidence,
    c.confidence_parts  AS confidence_parts,
    c.source_page       AS source_page,
    c.bbox_x0 AS bbox_x0, c.bbox_y0 AS bbox_y0, c.bbox_x1 AS bbox_x1, c.bbox_y1 AS bbox_y1,
    c.extraction_meta_json AS extraction_meta_json,
    c.revision          AS revision,
    cc.value_text       AS correction_value,
    cc.value_type       AS correction_type,
    cc.reason           AS correction_reason,
    cc.updated_at       AS correction_updated_at,
    (cc.cell_id IS NOT NULL) AS is_manual,
    COALESCE(cc.value_text, c.parsed_value_text) AS effective_value,
    COALESCE(cc.value_type, c.value_type)        AS effective_type
FROM cells c
LEFT JOIN cell_corrections cc ON cc.cell_id = c.id
"""


class RecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- writes
    def insert_records(self, run_id: str, document_id: str, records: Sequence[KpirRecord]) -> None:
        """Persist one page worth of records in a single short transaction."""
        if not records:
            return
        now = iso()
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO records(id, extraction_run_id, document_id, logical_index,"
                " source_page_from, source_page_to, status, confidence, revision,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        r.id,
                        run_id,
                        document_id,
                        r.logical_index,
                        r.source_page_from,
                        r.source_page_to,
                        r.status.value,
                        r.confidence,
                        r.revision,
                        now,
                        now,
                    )
                    for r in records
                ],
            )
            cell_rows = []
            for record in records:
                for cell in record.cells.values():
                    bbox = cell.source.bbox.as_tuple() if cell.source else (None, None, None, None)
                    cell_rows.append(
                        (
                            cell.id,
                            record.id,
                            cell.column_key,
                            cell.raw_text,
                            cell.normalized_text,
                            cell.parsed_value_text,
                            cell.value_type.value,
                            cell.confidence.score,
                            _json([list(p) for p in cell.confidence.components]),
                            cell.source.page_number if cell.source else None,
                            bbox[0],
                            bbox[1],
                            bbox[2],
                            bbox[3],
                            _json(cell.extraction_meta or None),
                            cell.revision,
                        )
                    )
            conn.executemany(
                "INSERT INTO cells(id, record_id, column_key, raw_text, normalized_text,"
                " parsed_value_text, value_type, confidence, confidence_parts, source_page,"
                " bbox_x0, bbox_y0, bbox_x1, bbox_y1, extraction_meta_json, revision)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                cell_rows,
            )

    def update_record_status(self, record_id: str, status: RecordStatus) -> None:
        self.db.execute(
            "UPDATE records SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, iso(), record_id),
        )

    # ---------------------------------------------------------------- reads
    def count(self, document_ids: Sequence[str]) -> int:
        if not document_ids:
            return 0
        placeholders = ",".join("?" * len(document_ids))
        value = self.db.query_scalar(
            f"SELECT COUNT(*) FROM records WHERE document_id IN ({placeholders})",
            tuple(document_ids),
        )
        return int(value or 0)

    def get_record(self, record_id: str) -> KpirRecord | None:
        row = self.db.query_one("SELECT * FROM records WHERE id = ?", (record_id,))
        if row is None:
            return None
        cells = self._cells_for_records([record_id])
        return _record_from_row(row, cells.get(record_id, {}))

    def get_cell(self, cell_id: str) -> sqlite3.Row | None:
        return self.db.query_one(
            _EFFECTIVE_SELECT + " WHERE c.id = ?",
            (cell_id,),
        )

    def cell_document(self, cell_id: str) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT r.id AS record_id, r.document_id AS document_id, r.revision AS record_revision"
            " FROM cells c JOIN records r ON r.id = c.record_id WHERE c.id = ?",
            (cell_id,),
        )

    def page_records(
        self,
        document_ids: Sequence[str],
        *,
        cursor: str | None = None,
        limit: int = 100,
        only_issues: bool = False,
        status: str | None = None,
    ) -> RecordPage:
        """Keyset pagination over ``(document_id, logical_index)``."""
        if not document_ids:
            return RecordPage([], None, 0)
        placeholders = ",".join("?" * len(document_ids))
        params: list[Any] = list(document_ids)
        where = [f"r.document_id IN ({placeholders})"]
        if status:
            where.append("r.status = ?")
            params.append(status)
        if only_issues:
            where.append(
                "EXISTS (SELECT 1 FROM issues i WHERE i.record_id = r.id AND i.status = 'open')"
            )
        if cursor:
            doc, index = _decode_cursor(cursor)
            where.append("(r.document_id > ? OR (r.document_id = ? AND r.logical_index > ?))")
            params.extend([doc, doc, index])

        total = int(
            self.db.query_scalar(
                f"SELECT COUNT(*) FROM records r WHERE {' AND '.join(where)}", tuple(params)
            )
            or 0
        )
        rows = self.db.query_all(
            f"SELECT r.* FROM records r WHERE {' AND '.join(where)}"
            " ORDER BY r.document_id, r.logical_index LIMIT ?",
            (*params, limit + 1),
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        cells = self._cells_for_records([r["id"] for r in rows])
        records = [_record_from_row(r, cells.get(r["id"], {})) for r in rows]
        next_cursor = (
            _encode_cursor(rows[-1]["document_id"], rows[-1]["logical_index"]) if has_more else None
        )
        return RecordPage(records, next_cursor, total)

    def iter_records(
        self, document_ids: Sequence[str], *, chunk_size: int = 500
    ) -> Iterator[KpirRecord]:
        """Stream every record in source order without materialising the batch."""
        cursor: str | None = None
        while True:
            page = self.page_records(document_ids, cursor=cursor, limit=chunk_size)
            yield from page.records
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def _cells_for_records(self, record_ids: Sequence[str]) -> dict[str, dict[str, Cell]]:
        if not record_ids:
            return {}
        result: dict[str, dict[str, Cell]] = {}
        chunk = 400
        for start in range(0, len(record_ids), chunk):
            subset = record_ids[start : start + chunk]
            placeholders = ",".join("?" * len(subset))
            rows = self.db.query_all(
                _EFFECTIVE_SELECT + f" WHERE c.record_id IN ({placeholders})", tuple(subset)
            )
            for row in rows:
                cell = _cell_from_row(row)
                result.setdefault(row["record_id"], {})[cell.column_key] = cell
        return result

    # ---------------------------------------------------------------- corrections
    def apply_correction(
        self,
        cell_id: str,
        *,
        value_text: str | None,
        value_type: ValueType,
        base_revision: int,
        reason: str | None,
    ) -> tuple[bool, int]:
        """Optimistic concurrency: returns ``(applied, current_revision)``."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT c.revision, c.parsed_value_text, c.value_type, c.record_id,"
                " r.document_id, cc.value_text AS corr_value, cc.value_type AS corr_type"
                " FROM cells c JOIN records r ON r.id = c.record_id"
                " LEFT JOIN cell_corrections cc ON cc.cell_id = c.id WHERE c.id = ?",
                (cell_id,),
            ).fetchone()
            if row is None:
                raise KeyError(cell_id)
            current_revision = int(row["revision"])
            if current_revision != base_revision:
                return False, current_revision

            old_value = (
                row["corr_value"] if row["corr_value"] is not None else row["parsed_value_text"]
            )
            old_type = row["corr_type"] or row["value_type"]
            now = iso()
            conn.execute(
                "INSERT INTO cell_corrections(cell_id, value_text, value_type, reason,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(cell_id) DO UPDATE SET value_text=excluded.value_text,"
                " value_type=excluded.value_type, reason=excluded.reason,"
                " updated_at=excluded.updated_at",
                (cell_id, value_text, value_type.value, reason, now, now),
            )
            conn.execute(
                "INSERT INTO corrections_history(id, cell_id, record_id, document_id,"
                " old_value_text, new_value_text, old_value_type, new_value_type, action,"
                " reason, actor, base_revision, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,'set',?,'local-user',?,?)",
                (
                    new_id(),
                    cell_id,
                    row["record_id"],
                    row["document_id"],
                    old_value,
                    value_text,
                    old_type,
                    value_type.value,
                    reason,
                    base_revision,
                    now,
                ),
            )
            new_revision = current_revision + 1
            conn.execute("UPDATE cells SET revision = ? WHERE id = ?", (new_revision, cell_id))
            conn.execute(
                "UPDATE records SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (now, row["record_id"]),
            )
        return True, new_revision

    def revert_correction(self, cell_id: str, base_revision: int) -> tuple[bool, int]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT c.revision, c.parsed_value_text, c.value_type, c.record_id,"
                " r.document_id, cc.value_text AS corr_value, cc.value_type AS corr_type"
                " FROM cells c JOIN records r ON r.id = c.record_id"
                " LEFT JOIN cell_corrections cc ON cc.cell_id = c.id WHERE c.id = ?",
                (cell_id,),
            ).fetchone()
            if row is None:
                raise KeyError(cell_id)
            current_revision = int(row["revision"])
            if current_revision != base_revision:
                return False, current_revision
            if row["corr_value"] is None and row["corr_type"] is None:
                return True, current_revision
            now = iso()
            conn.execute("DELETE FROM cell_corrections WHERE cell_id = ?", (cell_id,))
            conn.execute(
                "INSERT INTO corrections_history(id, cell_id, record_id, document_id,"
                " old_value_text, new_value_text, old_value_type, new_value_type, action,"
                " reason, actor, base_revision, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,'revert',NULL,'local-user',?,?)",
                (
                    new_id(),
                    cell_id,
                    row["record_id"],
                    row["document_id"],
                    row["corr_value"],
                    row["parsed_value_text"],
                    row["corr_type"] or row["value_type"],
                    row["value_type"],
                    base_revision,
                    now,
                ),
            )
            new_revision = current_revision + 1
            conn.execute("UPDATE cells SET revision = ? WHERE id = ?", (new_revision, cell_id))
            conn.execute(
                "UPDATE records SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (now, row["record_id"]),
            )
        return True, new_revision

    def cell_history(self, cell_id: str) -> list[sqlite3.Row]:
        return self.db.query_all(
            "SELECT * FROM corrections_history WHERE cell_id = ? ORDER BY created_at DESC",
            (cell_id,),
        )

    def source_revision(self, document_ids: Sequence[str]) -> str:
        """Opaque token binding an export to the data state it previewed."""
        if not document_ids:
            return "empty"
        placeholders = ",".join("?" * len(document_ids))
        row = self.db.query_one(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(revision),0) AS rev,"
            f" COALESCE(MAX(updated_at),'') AS ts FROM records"
            f" WHERE document_id IN ({placeholders})",
            tuple(document_ids),
        )
        corr = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(MAX(created_at),'') AS ts FROM corrections_history"
            f" WHERE document_id IN ({placeholders})",
            tuple(document_ids),
        )
        import hashlib

        payload = (
            f"{sorted(document_ids)}|{row['n']}|{row['rev']}|{row['ts']}|{corr['n']}|{corr['ts']}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _encode_cursor(document_id: str, logical_index: int) -> str:
    import base64

    return base64.urlsafe_b64encode(f"{document_id}:{logical_index}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    import base64

    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        document_id, _, index = raw.rpartition(":")
        return document_id, int(index)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid cursor") from exc


def _cell_from_row(row: sqlite3.Row) -> Cell:
    parts = _loads(row["confidence_parts"]) or []
    source = None
    if row["source_page"] is not None and row["bbox_x0"] is not None:
        source = SourceSpan(
            page_number=int(row["source_page"]),
            bbox=BBox(row["bbox_x0"], row["bbox_y0"], row["bbox_x1"], row["bbox_y1"]),
        )
    cell = Cell(
        column_key=row["column_key"],
        raw_text=row["raw_text"] or "",
        normalized_text=row["normalized_text"] or "",
        parsed_value_text=row["effective_value"],
        value_type=ValueType(row["effective_type"]),
        confidence=Confidence(
            score=float(row["confidence"]),
            components=tuple((p[0], p[1]) for p in parts if isinstance(p, list) and len(p) == 2),
        ),
        source=source,
        id=row["cell_id"],
        record_id=row["record_id"],
        is_manual=bool(row["is_manual"]),
        revision=int(row["revision"]),
        extraction_meta=_loads(row["extraction_meta_json"]) or {},
    )
    # Keep the extraction value reachable for the UI even when overridden.
    cell.extraction_meta.setdefault("extracted_value", row["extracted_value"])
    cell.extraction_meta.setdefault("extracted_type", row["extracted_type"])
    return cell


def _record_from_row(row: sqlite3.Row, cells: dict[str, Cell]) -> KpirRecord:
    return KpirRecord(
        logical_index=int(row["logical_index"]),
        cells=cells,
        source_page_from=int(row["source_page_from"]),
        source_page_to=int(row["source_page_to"]),
        id=row["id"],
        extraction_run_id=row["extraction_run_id"],
        status=RecordStatus(row["status"]),
        confidence=float(row["confidence"]),
        revision=int(row["revision"]),
    )


# --------------------------------------------------------------------------------------


class IssueRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_many(self, issues: Sequence[Issue]) -> None:
        if not issues:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO issues(id, document_id, page_number, record_id, cell_id, code,"
                " severity, message_key, details_json, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        i.id,
                        i.document_id,
                        i.page_number,
                        i.record_id,
                        i.cell_id,
                        i.code,
                        i.severity.value,
                        i.message_key,
                        _json(i.details or None),
                        i.status.value,
                        iso(i.created_at),
                    )
                    for i in issues
                ],
            )

    def list(
        self,
        document_ids: Sequence[str],
        *,
        severity: str | None = None,
        status: str | None = "open",
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        if not document_ids:
            return [], 0
        placeholders = ",".join("?" * len(document_ids))
        where = [f"document_id IN ({placeholders})"]
        params: list[Any] = list(document_ids)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)
        total = int(
            self.db.query_scalar(f"SELECT COUNT(*) FROM issues WHERE {clause}", tuple(params)) or 0
        )
        rows = self.db.query_all(
            f"SELECT * FROM issues WHERE {clause}"
            " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,"
            " created_at LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def counts_by_severity(self, document_ids: Sequence[str]) -> dict[str, int]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" * len(document_ids))
        rows = self.db.query_all(
            f"SELECT severity, COUNT(*) AS n FROM issues"
            f" WHERE document_id IN ({placeholders}) AND status = 'open' GROUP BY severity",
            tuple(document_ids),
        )
        return {row["severity"]: int(row["n"]) for row in rows}

    def critical_record_ids(self, document_ids: Sequence[str]) -> set[str]:
        if not document_ids:
            return set()
        placeholders = ",".join("?" * len(document_ids))
        rows = self.db.query_all(
            f"SELECT DISTINCT record_id FROM issues WHERE document_id IN ({placeholders})"
            " AND severity = 'critical' AND status = 'open' AND record_id IS NOT NULL",
            tuple(document_ids),
        )
        return {row["record_id"] for row in rows}

    def acknowledge(self, issue_id: str) -> bool:
        cursor = self.db.execute(
            "UPDATE issues SET status = 'acknowledged' WHERE id = ? AND status = 'open'",
            (issue_id,),
        )
        return cursor.rowcount > 0

    def resolve_for_record(self, record_id: str, codes: Sequence[str] | None = None) -> None:
        """Re-validation closes stale issues; history stays auditable."""
        if codes:
            placeholders = ",".join("?" * len(codes))
            self.db.execute(
                f"UPDATE issues SET status='resolved', resolved_at=? WHERE record_id = ?"
                f" AND status='open' AND code IN ({placeholders})",
                (iso(), record_id, *codes),
            )
        else:
            self.db.execute(
                "UPDATE issues SET status='resolved', resolved_at=? WHERE record_id = ?"
                " AND status='open'",
                (iso(), record_id),
            )

    def delete_for_record(self, record_id: str) -> None:
        self.db.execute("DELETE FROM issues WHERE record_id = ?", (record_id,))


class JobRepository:
    """Durable queue backed by SQLite (no external broker for a single host)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(
        self,
        job_type: str,
        *,
        document_id: str | None = None,
        batch_id: str | None = None,
        export_id: str | None = None,
        payload: dict | None = None,
        priority: int = 100,
        progress_total: int = 0,
    ) -> str:
        job_id = new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO jobs(id, type, priority, status, batch_id, document_id, export_id,"
                " payload_json, progress_total, created_at) VALUES (?,?,?,'QUEUED',?,?,?,?,?,?)",
                (
                    job_id,
                    job_type,
                    priority,
                    batch_id,
                    document_id,
                    export_id,
                    _json(payload),
                    progress_total,
                    iso(),
                ),
            )
        return job_id

    def claim_next(self, owner: str, *, lease_seconds: int = 60) -> sqlite3.Row | None:
        """Atomic conditional UPDATE; only one worker can win a job."""
        expires = iso(utc_now() + _dt.timedelta(seconds=lease_seconds))
        now = iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = 'QUEUED' AND cancel_requested = 0"
                " ORDER BY priority, created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                "UPDATE jobs SET status='RUNNING', lease_owner=?, lease_expires_at=?,"
                " heartbeat_at=?, started_at=COALESCE(started_at, ?), attempt=attempt+1"
                " WHERE id = ? AND status='QUEUED'",
                (owner, expires, now, now, row["id"]),
            )
            if cursor.rowcount == 0:
                return None
            return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()

    def heartbeat(self, job_id: str, owner: str, *, lease_seconds: int = 60) -> bool:
        expires = iso(utc_now() + _dt.timedelta(seconds=lease_seconds))
        cursor = self.db.execute(
            "UPDATE jobs SET heartbeat_at=?, lease_expires_at=? WHERE id=? AND lease_owner=?",
            (iso(), expires, job_id, owner),
        )
        return cursor.rowcount > 0

    def update_progress(
        self, job_id: str, current: int, total: int | None = None, phase: str | None = None
    ) -> None:
        if total is None:
            self.db.execute(
                "UPDATE jobs SET progress_current=?, progress_phase=COALESCE(?, progress_phase)"
                " WHERE id=?",
                (current, phase, job_id),
            )
        else:
            self.db.execute(
                "UPDATE jobs SET progress_current=?, progress_total=?,"
                " progress_phase=COALESCE(?, progress_phase) WHERE id=?",
                (current, total, phase, job_id),
            )

    def finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE jobs SET status=?, finished_at=?, error_code=?, error_summary=?,"
            " lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
            (status.value, iso(), error_code, error_summary, job_id),
        )

    def request_cancel(self, job_id: str) -> bool:
        cursor = self.db.execute(
            "UPDATE jobs SET cancel_requested=1,"
            " status = CASE WHEN status='QUEUED' THEN 'CANCELLED'"
            "               WHEN status='RUNNING' THEN 'CANCELLING' ELSE status END,"
            " finished_at = CASE WHEN status='QUEUED' THEN ? ELSE finished_at END"
            " WHERE id=? AND status IN ('QUEUED','RUNNING')",
            (iso(), job_id),
        )
        return cursor.rowcount > 0

    def is_cancel_requested(self, job_id: str) -> bool:
        row = self.db.query_one("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
        return bool(row and row["cancel_requested"])

    def retry(self, job_id: str) -> bool:
        cursor = self.db.execute(
            "UPDATE jobs SET status='QUEUED', cancel_requested=0, error_code=NULL,"
            " error_summary=NULL, finished_at=NULL, lease_owner=NULL, lease_expires_at=NULL"
            " WHERE id=? AND status IN ('FAILED','CANCELLED','INTERRUPTED')",
            (job_id,),
        )
        return cursor.rowcount > 0

    def reclaim_expired(self) -> int:
        """Recover jobs whose worker died (lease expired)."""
        now = iso()
        cursor = self.db.execute(
            "UPDATE jobs SET status = CASE WHEN attempt < max_attempts THEN 'QUEUED'"
            " ELSE 'FAILED' END, lease_owner=NULL, lease_expires_at=NULL,"
            " error_code = CASE WHEN attempt < max_attempts THEN error_code"
            "                    ELSE 'LEASE_EXPIRED' END"
            " WHERE status IN ('RUNNING','CANCELLING') AND lease_expires_at IS NOT NULL"
            " AND lease_expires_at < ?",
            (now,),
        )
        return cursor.rowcount

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list(
        self,
        *,
        batch_id: str | None = None,
        document_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        where: list[str] = []
        params: list[Any] = []
        if batch_id:
            where.append("batch_id = ?")
            params.append(batch_id)
        if document_id:
            where.append("document_id = ?")
            params.append(document_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return self.db.query_all(
            f"SELECT * FROM jobs {clause} ORDER BY created_at DESC LIMIT ?", (*params, limit)
        )

    def active_count(self) -> int:
        return int(
            self.db.query_scalar(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')"
            )
            or 0
        )


class ExportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        definition_json: str,
        policy: str,
        document_ids: Sequence[str],
        source_revision: str,
        batch_id: str | None = None,
    ) -> str:
        export_id = new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO exports(id, batch_id, format, policy, status, definition_json,"
                " source_revision, created_at) VALUES (?,?,'xlsx',?,'pending',?,?,?)",
                (export_id, batch_id, policy, definition_json, source_revision, iso()),
            )
            conn.executemany(
                "INSERT INTO export_documents(export_id, document_id) VALUES (?,?)",
                [(export_id, d) for d in document_ids],
            )
        return export_id

    def attach_job(self, export_id: str, job_id: str) -> None:
        self.db.execute("UPDATE exports SET job_id = ? WHERE id = ?", (job_id, export_id))

    def mark_ready(
        self,
        export_id: str,
        *,
        path: str,
        sha256: str,
        size_bytes: int,
        row_count: int,
        column_count: int,
        extractor_version: str,
        profile_id: str,
        profile_version: int,
    ) -> None:
        self.db.execute(
            "UPDATE exports SET status='ready', path=?, sha256=?, size_bytes=?,"
            " result_row_count=?, result_column_count=?, extractor_version=?, profile_id=?,"
            " profile_version=?, finished_at=? WHERE id=?",
            (
                path,
                sha256,
                size_bytes,
                row_count,
                column_count,
                extractor_version,
                profile_id,
                profile_version,
                iso(),
                export_id,
            ),
        )

    def mark_failed(self, export_id: str, code: str, summary: str) -> None:
        self.db.execute(
            "UPDATE exports SET status='failed', error_code=?, error_summary=?, finished_at=?"
            " WHERE id=?",
            (code, summary, iso(), export_id),
        )

    def get(self, export_id: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM exports WHERE id = ?", (export_id,))

    def documents(self, export_id: str) -> list[str]:
        rows = self.db.query_all(
            "SELECT document_id FROM export_documents WHERE export_id = ?", (export_id,)
        )
        return [r["document_id"] for r in rows]

    def list(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.query_all("SELECT * FROM exports ORDER BY created_at DESC LIMIT ?", (limit,))

    def delete(self, export_id: str) -> None:
        self.db.execute("DELETE FROM exports WHERE id = ?", (export_id,))


class PresetRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, name: str, profile_id: str, definition_json: str) -> str:
        preset_id = new_id()
        now = iso()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO export_presets(id, name, profile_id, definition_json, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (preset_id, name, profile_id, definition_json, now, now),
            )
        return preset_id

    def list(self) -> list[sqlite3.Row]:
        return self.db.query_all("SELECT * FROM export_presets ORDER BY name")

    def delete(self, preset_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM export_presets WHERE id = ?", (preset_id,))
        return cursor.rowcount > 0
