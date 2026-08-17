-- Initial schema for the local KPiR converter.
-- Money is stored as canonical decimal TEXT, never REAL.

PRAGMA foreign_keys = ON;

CREATE TABLE batches (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE documents (
    id                   TEXT PRIMARY KEY,
    batch_id             TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    original_name        TEXT NOT NULL,
    stored_path          TEXT NOT NULL,
    sha256               TEXT NOT NULL,
    size_bytes           INTEGER NOT NULL,
    page_count           INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    detected_profile_id  TEXT,
    declared_period_from TEXT,
    declared_period_to   TEXT,
    error_code           TEXT,
    error_summary        TEXT,
    created_at           TEXT NOT NULL,
    deleted_at           TEXT
);
CREATE INDEX idx_documents_batch ON documents(batch_id);
CREATE INDEX idx_documents_status ON documents(status);

CREATE TABLE pages (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number    INTEGER NOT NULL,
    width          REAL NOT NULL,
    height         REAL NOT NULL,
    rotation       INTEGER NOT NULL DEFAULT 0,
    text_available INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,
    render_path    TEXT,
    error_code     TEXT,
    error_summary  TEXT,
    UNIQUE (document_id, page_number)
);
CREATE INDEX idx_pages_document ON pages(document_id, page_number);

CREATE TABLE extraction_runs (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    extractor_version TEXT NOT NULL,
    profile_id        TEXT NOT NULL,
    profile_version   INTEGER NOT NULL,
    config_hash       TEXT NOT NULL,
    status            TEXT NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    last_page_done    INTEGER NOT NULL DEFAULT 0,
    started_at        TEXT NOT NULL,
    finished_at       TEXT
);
CREATE INDEX idx_runs_document ON extraction_runs(document_id, is_active);

CREATE TABLE records (
    id                TEXT PRIMARY KEY,
    extraction_run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    logical_index     INTEGER NOT NULL,
    source_page_from  INTEGER NOT NULL,
    source_page_to    INTEGER NOT NULL,
    status            TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 1.0,
    revision          INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_records_run ON records(extraction_run_id, logical_index);
CREATE INDEX idx_records_document ON records(document_id, logical_index);
CREATE INDEX idx_records_status ON records(document_id, status);

-- Extraction output. Immutable within a run; user edits live in cell_corrections.
CREATE TABLE cells (
    id                  TEXT PRIMARY KEY,
    record_id           TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    column_key          TEXT NOT NULL,
    raw_text            TEXT NOT NULL DEFAULT '',
    normalized_text     TEXT NOT NULL DEFAULT '',
    parsed_value_text   TEXT,
    value_type          TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 1.0,
    confidence_parts    TEXT,
    source_page         INTEGER,
    bbox_x0             REAL,
    bbox_y0             REAL,
    bbox_x1             REAL,
    bbox_y1             REAL,
    extraction_meta_json TEXT,
    revision            INTEGER NOT NULL DEFAULT 1,
    UNIQUE (record_id, column_key)
);
CREATE INDEX idx_cells_record ON cells(record_id);

-- Current manual override of a cell (at most one active per cell).
CREATE TABLE cell_corrections (
    cell_id          TEXT PRIMARY KEY REFERENCES cells(id) ON DELETE CASCADE,
    value_text       TEXT,
    value_type       TEXT NOT NULL,
    actor            TEXT NOT NULL DEFAULT 'local-user',
    reason           TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- Full, append-only audit trail of every manual change.
CREATE TABLE corrections_history (
    id              TEXT PRIMARY KEY,
    cell_id         TEXT NOT NULL,
    record_id       TEXT NOT NULL,
    document_id     TEXT NOT NULL,
    old_value_text  TEXT,
    new_value_text  TEXT,
    old_value_type  TEXT NOT NULL,
    new_value_type  TEXT NOT NULL,
    action          TEXT NOT NULL,
    reason          TEXT,
    actor           TEXT NOT NULL DEFAULT 'local-user',
    base_revision   INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_corrections_cell ON corrections_history(cell_id, created_at);

CREATE TABLE issues (
    id           TEXT PRIMARY KEY,
    document_id  TEXT REFERENCES documents(id) ON DELETE CASCADE,
    page_number  INTEGER,
    record_id    TEXT,
    cell_id      TEXT,
    code         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    message_key  TEXT NOT NULL,
    details_json TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE INDEX idx_issues_document ON issues(document_id, status, severity);
CREATE INDEX idx_issues_record ON issues(record_id);

CREATE TABLE jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    priority         INTEGER NOT NULL DEFAULT 100,
    status           TEXT NOT NULL,
    batch_id         TEXT,
    document_id      TEXT,
    export_id        TEXT,
    payload_json     TEXT,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    progress_phase   TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    attempt          INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    lease_owner      TEXT,
    lease_expires_at TEXT,
    heartbeat_at     TEXT,
    error_code       TEXT,
    error_summary    TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX idx_jobs_claim ON jobs(status, priority, created_at);
CREATE INDEX idx_jobs_document ON jobs(document_id, status);
CREATE INDEX idx_jobs_batch ON jobs(batch_id, status);

CREATE TABLE exports (
    id                  TEXT PRIMARY KEY,
    batch_id            TEXT,
    job_id              TEXT,
    format              TEXT NOT NULL DEFAULT 'xlsx',
    policy              TEXT NOT NULL,
    status              TEXT NOT NULL,
    path                TEXT,
    sha256              TEXT,
    size_bytes          INTEGER,
    definition_json     TEXT NOT NULL,
    source_revision     TEXT,
    result_row_count    INTEGER,
    result_column_count INTEGER,
    extractor_version   TEXT,
    profile_id          TEXT,
    profile_version     INTEGER,
    error_code          TEXT,
    error_summary       TEXT,
    created_at          TEXT NOT NULL,
    finished_at         TEXT
);
CREATE INDEX idx_exports_created ON exports(created_at DESC);

CREATE TABLE export_documents (
    export_id   TEXT NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    PRIMARY KEY (export_id, document_id)
);

CREATE TABLE export_presets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    profile_id      TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
