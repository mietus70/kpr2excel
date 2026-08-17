/**
 * Types mirroring the FastAPI contract (`/api/v1/openapi.json`).
 *
 * Money values are ALWAYS decimal strings, never numbers: JSON numbers would
 * silently lose precision. Dates are ISO `YYYY-MM-DD` strings.
 */

export type ValueType = 'text' | 'money' | 'date' | 'integer' | 'empty';
export type ColumnType = 'text' | 'money' | 'date' | 'integer';
export type RecordStatus = 'ok' | 'review' | 'critical';
export type IssueSeverity = 'info' | 'warning' | 'critical';
export type ExportPolicy = 'strict' | 'reviewed' | 'draft';
export type RowSelectionMode = 'ALL_MATCHING' | 'EXPLICIT';

export interface ApiErrorBody {
  code: string;
  message: string;
  field?: string | null;
  correlationId: string;
  currentRevision?: number;
  currentValue?: string | null;
}

export interface ProfileColumn {
  key: string;
  label: string;
  type: ColumnType;
  formNumber: string | null;
  required: boolean;
}

export interface Profile {
  id: string;
  version: number;
  qualifiedId: string;
  displayName: string;
  columns: ProfileColumn[];
  defaultExportColumns: string[];
  defaultFilterColumn: string;
}

export interface Batch {
  id: string;
  displayName: string;
  createdAt: string;
  documentCount: number;
}

export interface KpirDocument {
  id: string;
  batchId: string;
  originalName: string;
  sizeBytes: number;
  pageCount: number;
  status: string;
  detectedProfileId: string | null;
  periodFrom: string | null;
  periodTo: string | null;
  createdAt: string;
  recordCount: number;
  issueCounts: Record<string, number>;
  errorCode: string | null;
}

export interface SourceSpan {
  page: number;
  /** Normalised [x0, y0, x1, y1] in 0..1 of the rotated page. */
  bbox: [number, number, number, number];
}

export interface Cell {
  id: string;
  columnKey: string;
  value: string | null;
  valueType: ValueType;
  rawText: string;
  extractedValue: string | null;
  isManual: boolean;
  confidence: number;
  confidenceParts: Record<string, number>;
  revision: number;
  source: SourceSpan | null;
}

export interface KpirRecord {
  id: string;
  documentId: string;
  logicalIndex: number;
  pageFrom: number;
  pageTo: number;
  status: RecordStatus;
  confidence: number;
  revision: number;
  cells: Record<string, Cell>;
}

export interface RecordPage {
  items: KpirRecord[];
  nextCursor: string | null;
  total: number;
}

export interface Issue {
  id: string;
  code: string;
  severity: IssueSeverity;
  messageKey: string;
  documentId: string | null;
  pageNumber: number | null;
  recordId: string | null;
  cellId: string | null;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
}

export interface IssuePage {
  items: Issue[];
  total: number;
}

export interface Job {
  id: string;
  type: string;
  status:
    | 'QUEUED'
    | 'RUNNING'
    | 'SUCCEEDED'
    | 'FAILED'
    | 'CANCELLING'
    | 'CANCELLED'
    | 'INTERRUPTED';
  documentId: string | null;
  batchId: string | null;
  exportId: string | null;
  progressCurrent: number;
  progressTotal: number;
  progressPhase: string | null;
  attempt: number;
  errorCode: string | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface MoneyRangeFilter {
  type: 'money_range';
  columnKey: string;
  /** Decimal string, e.g. "100.00". */
  min?: string | null;
  max?: string | null;
  bounds: 'inclusive' | 'exclusive';
}

export interface RowSelection {
  mode: RowSelectionMode;
  includedRecordIds: string[];
  excludedRecordIds: string[];
}

export interface ExportDefinition {
  scope: { type: 'documents' | 'batch'; documentIds?: string[]; batchId?: string | null };
  columnKeys: string[];
  rowSelection: RowSelection;
  filters: MoneyRangeFilter[];
  policy: ExportPolicy;
  includeIssuesSheet: boolean;
  sourceRevision?: string | null;
}

export interface ExportPreview {
  matchingRowCount: number;
  columnCount: number;
  excludedForNullOrInvalidCount: number;
  excludedByRangeCount: number;
  excludedBySelectionCount: number;
  totalRowCount: number;
  issueCounts: Record<string, number>;
  blockingIssueCount: number;
  sampleRows: { recordId: string; values: Record<string, string | null> }[];
  sourceRevision: string;
  columnLabels: { key: string; label: string }[];
  activeFilters: {
    columnKey: string;
    label: string;
    min: string | null;
    max: string | null;
    bounds: string;
  }[];
  policy: string;
}

export interface ExportJob {
  id: string;
  status: string;
  policy: string;
  jobId: string | null;
  rowCount: number | null;
  columnCount: number | null;
  sizeBytes: number | null;
  sha256: string | null;
  sourceRevision: string | null;
  createdAt: string;
  errorCode: string | null;
  downloadUrl: string | null;
}

export interface CorrectionHistoryEntry {
  id: string;
  action: string;
  oldValue: string | null;
  newValue: string | null;
  reason: string | null;
  actor: string;
  createdAt: string;
}
