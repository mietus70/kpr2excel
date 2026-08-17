/**
 * Same-origin API client.
 *
 * All URLs are relative so the browser never has to know where the backend
 * lives; in development Vite proxies `/api` to the loopback API, and in the
 * packaged build FastAPI serves the SPA itself. This keeps the CSP directive
 * `connect-src 'self'` valid in both modes.
 */

import type {
  ApiErrorBody,
  Batch,
  Cell,
  CorrectionHistoryEntry,
  ExportDefinition,
  ExportJob,
  ExportPreview,
  IssuePage,
  Job,
  KpirDocument,
  KpirRecord,
  Profile,
  RecordPage,
} from '@/types/api';

const BASE = '/api/v1';
const CSRF_COOKIE = 'kpir_csrf';
const CSRF_HEADER = 'X-CSRF-Token';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly field?: string | null;
  readonly correlationId?: string;
  readonly currentRevision?: number;
  readonly currentValue?: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || 'Wystąpił błąd');
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.field = body.field;
    this.correlationId = body.correlationId;
    this.currentRevision = body.currentRevision;
    this.currentValue = body.currentValue;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

function readCsrfToken(): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set(CSRF_HEADER, readCsrfToken());
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const body: ApiErrorBody = payload?.error ?? {
      code: `HTTP_${response.status}`,
      message: 'Nieznany błąd serwera',
      correlationId: '',
    };
    throw new ApiError(response.status, body);
  }
  return payload as T;
}

/** Ensures the CSRF cookie exists before the first mutation. */
export async function bootstrap(): Promise<void> {
  await request<{ status: string }>('/health');
}

export const api = {
  health: () => request<{ status: string; offline: boolean; profiles: string[] }>('/health'),

  profiles: () => request<Profile[]>('/profiles'),

  // -------------------------------------------------------------- batches
  listBatches: () => request<Batch[]>('/batches'),
  createBatch: (displayName: string) =>
    request<Batch>('/batches', { method: 'POST', body: JSON.stringify({ displayName }) }),

  uploadDocuments: (batchId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    return request<{ documents: KpirDocument[]; jobIds: string[] }>(
      `/batches/${batchId}/documents`,
      { method: 'POST', body: form },
    );
  },

  // ------------------------------------------------------------ documents
  listDocuments: (batchId?: string) =>
    request<KpirDocument[]>(`/documents${batchId ? `?batchId=${encodeURIComponent(batchId)}` : ''}`),
  getDocument: (documentId: string) => request<KpirDocument>(`/documents/${documentId}`),
  deleteDocument: (documentId: string) =>
    request<void>(`/documents/${documentId}`, { method: 'DELETE' }),

  pageImageUrl: (documentId: string, pageNumber: number) =>
    `${BASE}/documents/${documentId}/pages/${pageNumber}/image`,

  listRecords: (
    documentId: string,
    options: { cursor?: string | null; limit?: number; onlyIssues?: boolean } = {},
  ) => {
    const params = new URLSearchParams();
    if (options.cursor) params.set('cursor', options.cursor);
    params.set('limit', String(options.limit ?? 100));
    if (options.onlyIssues) params.set('onlyIssues', 'true');
    return request<RecordPage>(`/documents/${documentId}/records?${params}`);
  },

  getRecord: (recordId: string) => request<KpirRecord>(`/records/${recordId}`),

  listIssues: (documentId: string, severity?: string) => {
    const params = new URLSearchParams({ status: 'open', limit: '500' });
    if (severity) params.set('severity', severity);
    return request<IssuePage>(`/documents/${documentId}/issues?${params}`);
  },

  acknowledgeIssue: (issueId: string) =>
    request<void>(`/issues/${issueId}/acknowledge`, { method: 'POST' }),

  // ---------------------------------------------------------------- cells
  patchCell: (cellId: string, value: string | null, baseRevision: number, reason?: string) =>
    request<{ cell: Cell; recordStatus: string }>(`/cells/${cellId}`, {
      method: 'PATCH',
      body: JSON.stringify({ value, baseRevision, reason: reason ?? null }),
    }),

  revertCell: (cellId: string, baseRevision: number) =>
    request<{ cell: Cell; recordStatus: string }>(`/cells/${cellId}/correction`, {
      method: 'DELETE',
      body: JSON.stringify({ baseRevision }),
    }),

  cellHistory: (cellId: string) =>
    request<CorrectionHistoryEntry[]>(`/cells/${cellId}/history`),

  // ----------------------------------------------------------------- jobs
  listJobs: (batchId?: string) =>
    request<{ items: Job[] }>(`/jobs${batchId ? `?batchId=${encodeURIComponent(batchId)}` : ''}`),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: 'POST' }),
  retryJob: (jobId: string) => request<Job>(`/jobs/${jobId}/retry`, { method: 'POST' }),

  // --------------------------------------------------------------- export
  previewExport: (definition: ExportDefinition) =>
    request<ExportPreview>('/export-previews', {
      method: 'POST',
      body: JSON.stringify(definition),
    }),

  createExport: (definition: ExportDefinition) =>
    request<ExportJob>('/exports', { method: 'POST', body: JSON.stringify(definition) }),

  getExport: (exportId: string) => request<ExportJob>(`/exports/${exportId}`),
  listExports: () => request<{ items: ExportJob[] }>('/exports'),
};

/** Subscribes to job progress over SSE; falls back to polling on error. */
export function subscribeToJobs(
  onJobs: (jobs: Job[]) => void,
  batchId?: string,
): () => void {
  const url = `${BASE}/events${batchId ? `?batchId=${encodeURIComponent(batchId)}` : ''}`;
  let source: EventSource | null = null;
  let pollTimer: number | null = null;

  const startPolling = () => {
    if (pollTimer !== null) return;
    pollTimer = window.setInterval(async () => {
      try {
        const { items } = await api.listJobs(batchId);
        onJobs(items);
      } catch {
        /* stay silent: the UI keeps the last known state */
      }
    }, 2000);
  };

  try {
    source = new EventSource(url, { withCredentials: true });
    source.addEventListener('jobs', (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data) as { jobs: Job[] };
        onJobs(data.jobs);
      } catch {
        /* ignore malformed frame */
      }
    });
    source.onerror = () => {
      source?.close();
      source = null;
      startPolling();
    };
  } catch {
    startPolling();
  }

  return () => {
    source?.close();
    if (pollTimer !== null) window.clearInterval(pollTimer);
  };
}
