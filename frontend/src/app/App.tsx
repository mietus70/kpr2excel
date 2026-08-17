import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, bootstrap, subscribeToJobs } from '@/api/client';
import { ExportConfigurator } from '@/features/export/ExportConfigurator';
import { PdfPanel } from '@/features/review/PdfPanel';
import { RecordTable } from '@/features/review/RecordTable';
import type {
  Cell,
  Issue,
  Job,
  KpirDocument,
  KpirRecord,
  Profile,
  SourceSpan,
} from '@/types/api';

type Tab = 'import' | 'review' | 'export';

const EMPTY_RECORD_IDS: string[] = [];

export function App() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [documents, setDocuments] = useState<KpirDocument[]>([]);
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);
  const [records, setRecords] = useState<KpirRecord[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tab, setTab] = useState<Tab>('import');
  const [activeSpan, setActiveSpan] = useState<SourceSpan | null>(null);
  const [activeCell, setActiveCell] = useState<{ cell: Cell; record: KpirRecord } | null>(null);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [excludedRecordIds, setExcludedRecordIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [booted, setBooted] = useState(false);

  // ------------------------------------------------------------- bootstrap
  useEffect(() => {
    (async () => {
      try {
        await bootstrap();
        const [profiles, docs] = await Promise.all([api.profiles(), api.listDocuments()]);
        setProfile(profiles[0] ?? null);
        setDocuments(docs);
        if (docs.length > 0) {
          setActiveDocumentId(docs[0].id);
          setTab(docs[0].status === 'ready' ? 'review' : 'import');
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Nie można połączyć się z API');
      } finally {
        setBooted(true);
      }
    })();
  }, []);

  // ------------------------------------------------------------- job feed
  useEffect(() => {
    const unsubscribe = subscribeToJobs((incoming) => {
      setJobs((current) => {
        const map = new Map(current.map((job) => [job.id, job]));
        incoming.forEach((job) => map.set(job.id, job));
        return [...map.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      });
    });
    return unsubscribe;
  }, []);

  // Refresh documents whenever a job finishes.
  const finishedSignature = jobs
    .filter((job) => ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(job.status))
    .map((job) => job.id)
    .join(',');

  useEffect(() => {
    if (!booted) return;
    void (async () => {
      try {
        const docs = await api.listDocuments();
        setDocuments(docs);
        if (!activeDocumentId && docs.length > 0) setActiveDocumentId(docs[0].id);
      } catch {
        /* keep the previous list */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedSignature, booted]);

  // ------------------------------------------------------------- records
  const loadRecords = useCallback(async (documentId: string, filterIssues: boolean) => {
    try {
      const page = await api.listRecords(documentId, { limit: 200, onlyIssues: filterIssues });
      setRecords(page.items);
      const issuePage = await api.listIssues(documentId);
      setIssues(issuePage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Nie udało się pobrać rekordów');
    }
  }, []);

  useEffect(() => {
    if (activeDocumentId) void loadRecords(activeDocumentId, onlyIssues);
  }, [activeDocumentId, onlyIssues, loadRecords, finishedSignature]);

  const activeDocument = documents.find((doc) => doc.id === activeDocumentId) ?? null;

  const handleCellUpdated = useCallback(
    (recordId: string, cell: Cell, recordStatus: string) => {
      setRecords((current) =>
        current.map((record) =>
          record.id === recordId
            ? {
                ...record,
                status: recordStatus as KpirRecord['status'],
                cells: { ...record.cells, [cell.columnKey]: cell },
              }
            : record,
        ),
      );
      if (activeDocumentId) {
        void api.listIssues(activeDocumentId).then((page) => setIssues(page.items));
      }
    },
    [activeDocumentId],
  );

  const selectCell = useCallback((cell: Cell, record: KpirRecord) => {
    setActiveCell({ cell, record });
    setActiveSpan(cell.source);
  }, []);

  const columns = useMemo(() => profile?.columns.map((c) => c.key) ?? [], [profile]);

  const running = jobs.filter((job) => ['QUEUED', 'RUNNING'].includes(job.status));

  if (!booted) {
    return (
      <div className="app">
        <main className="app-main">
          <p className="muted">Uruchamianie…</p>
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Konwerter KPiR → Excel</h1>
        <span className="offline-badge" title="Aplikacja nie łączy się z internetem">
          offline
        </span>
        <nav className="tabs" role="tablist" aria-label="Sekcje aplikacji">
          {(
            [
              ['import', 'Import'],
              ['review', 'Kontrola'],
              ['export', 'Eksport'],
            ] as [Tab, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="app-main">
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}

        {running.length > 0 && (
          <section className="panel" aria-live="polite">
            <h3>Zadania w toku</h3>
            {running.map((job) => (
              <div key={job.id} className="row" style={{ marginBottom: 6 }}>
                <span className="small">{describeJob(job)}</span>
                <div className="progress-track" style={{ flex: 1, maxWidth: 260 }}>
                  <div
                    className="progress-bar"
                    style={{
                      width: `${
                        job.progressTotal > 0
                          ? Math.round((job.progressCurrent / job.progressTotal) * 100)
                          : 5
                      }%`,
                    }}
                  />
                </div>
                <span className="small mono">
                  {job.progressCurrent}/{job.progressTotal || '?'}
                </span>
                <button type="button" onClick={() => void api.cancelJob(job.id)}>
                  Anuluj
                </button>
              </div>
            ))}
          </section>
        )}

        {tab === 'import' && (
          <ImportView
            documents={documents}
            onImported={async () => {
              const docs = await api.listDocuments();
              setDocuments(docs);
              if (docs.length > 0) setActiveDocumentId(docs[0].id);
            }}
            onDeleted={async (id) => {
              await api.deleteDocument(id);
              const docs = await api.listDocuments();
              setDocuments(docs);
              setActiveDocumentId(docs[0]?.id ?? null);
              setRecords([]);
            }}
            onOpen={(id) => {
              setActiveDocumentId(id);
              setTab('review');
            }}
          />
        )}

        {tab === 'review' && profile && activeDocument && (
          <>
            <section className="panel">
              <div className="row">
                <label htmlFor="doc-select">Dokument</label>
                <select
                  id="doc-select"
                  value={activeDocumentId ?? ''}
                  onChange={(e) => setActiveDocumentId(e.target.value)}
                >
                  {documents.map((doc) => (
                    <option key={doc.id} value={doc.id}>
                      {doc.originalName} ({doc.recordCount})
                    </option>
                  ))}
                </select>
                <label className="row">
                  <input
                    type="checkbox"
                    checked={onlyIssues}
                    onChange={(e) => setOnlyIssues(e.target.checked)}
                  />
                  <span>Tylko wiersze z problemami</span>
                </label>
                <div className="spacer" />
                <span className="small muted">
                  {issues.filter((i) => i.severity === 'critical').length} krytycznych,{' '}
                  {issues.filter((i) => i.severity === 'warning').length} ostrzeżeń
                </span>
              </div>
            </section>

            <div className="review-layout">
              <PdfPanel
                documentId={activeDocument.id}
                pageCount={activeDocument.pageCount}
                activeSpan={activeSpan}
                activeLabel={
                  activeCell
                    ? profile.columns.find((c) => c.key === activeCell.cell.columnKey)?.label
                    : undefined
                }
              />

              <div className="stack">
                <section className="panel">
                  <h2>Rekordy ({records.length})</h2>
                  <p className="small muted">
                    Kliknij komórkę, aby zobaczyć jej źródło. <span className="kbd">Enter</span>{' '}
                    rozpoczyna edycję, <span className="kbd">Esc</span> anuluje. Oznaczenia:{' '}
                    <span className="cell-state manual">M</span> ręczna,{' '}
                    <span className="cell-state review">!</span> do sprawdzenia,{' '}
                    <span className="cell-state critical">!!</span> krytyczna.
                  </p>
                  <RecordTable
                    profile={profile}
                    records={records}
                    columns={columns}
                    selectedCellId={activeCell?.cell.id ?? null}
                    onSelectCell={selectCell}
                    onCellUpdated={handleCellUpdated}
                  />
                </section>

                {activeCell && (
                  <section className="panel">
                    <h3>Szczegóły komórki</h3>
                    <dl className="detail-grid">
                      <dt>Kolumna</dt>
                      <dd>
                        {profile.columns.find((c) => c.key === activeCell.cell.columnKey)?.label}
                      </dd>
                      <dt>Tekst surowy</dt>
                      <dd className="mono">{activeCell.cell.rawText || '—'}</dd>
                      <dt>Wartość ekstrakcji</dt>
                      <dd className="mono">{activeCell.cell.extractedValue ?? '(puste)'}</dd>
                      <dt>Wartość efektywna</dt>
                      <dd className="mono">
                        {activeCell.cell.value ?? '(puste)'}
                        {activeCell.cell.isManual && ' — poprawiona ręcznie'}
                      </dd>
                      <dt>Pewność</dt>
                      <dd className="mono">{(activeCell.cell.confidence * 100).toFixed(1)}%</dd>
                      <dt>Strona</dt>
                      <dd>{activeCell.cell.source?.page ?? '—'}</dd>
                    </dl>
                  </section>
                )}

                {issues.length > 0 && (
                  <section className="panel">
                    <h3>Problemy ({issues.length})</h3>
                    <div className="table-scroll" style={{ maxHeight: 220 }}>
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th scope="col">Waga</th>
                            <th scope="col">Kod</th>
                            <th scope="col">Strona</th>
                            <th scope="col">Szczegóły</th>
                          </tr>
                        </thead>
                        <tbody>
                          {issues.slice(0, 100).map((issue) => (
                            <tr key={issue.id}>
                              <td>
                                <span
                                  className={`status-chip ${
                                    issue.severity === 'critical' ? 'critical' : 'review'
                                  }`}
                                >
                                  {issue.severity === 'critical' ? 'Krytyczny' : 'Ostrzeżenie'}
                                </span>
                              </td>
                              <td className="mono small">{issue.code}</td>
                              <td>{issue.pageNumber ?? '—'}</td>
                              <td className="small">{JSON.stringify(issue.details)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}
              </div>
            </div>
          </>
        )}

        {tab === 'review' && !activeDocument && (
          <p className="notice info">Zaimportuj dokument, aby rozpocząć kontrolę.</p>
        )}

        {tab === 'export' && profile && (
          <ExportConfigurator
            profile={profile}
            documents={documents.filter((d) => d.status === 'ready')}
            initialDocumentIds={documents.filter((d) => d.status === 'ready').map((d) => d.id)}
            excludedRecordIds={excludedRecordIds}
            explicitRecordIds={EMPTY_RECORD_IDS}
            onClearRowSelection={() => setExcludedRecordIds([])}
          />
        )}
      </main>
    </div>
  );
}

function describeJob(job: Job): string {
  const label =
    job.type === 'EXTRACT_DOCUMENT'
      ? 'Ekstrakcja dokumentu'
      : job.type === 'EXPORT_XLSX'
        ? 'Tworzenie pliku XLSX'
        : job.type;
  return `${label} — ${job.progressPhase ?? job.status}`;
}

// --------------------------------------------------------------------- import

function ImportView({
  documents,
  onImported,
  onDeleted,
  onOpen,
}: {
  documents: KpirDocument[];
  onImported: () => Promise<void>;
  onDeleted: (id: string) => Promise<void>;
  onOpen: (id: string) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.pdf'));
      if (list.length === 0) {
        setMessage('Wybierz pliki PDF.');
        return;
      }
      setBusy(true);
      setMessage(null);
      try {
        const batch = await api.createBatch(`Import ${new Date().toLocaleString('pl-PL')}`);
        await api.uploadDocuments(batch.id, list);
        await onImported();
        setMessage(`Zaimportowano ${list.length} plik(ów). Trwa ekstrakcja.`);
      } catch (err) {
        setMessage(err instanceof ApiError ? err.message : 'Import nie powiódł się');
      } finally {
        setBusy(false);
      }
    },
    [onImported],
  );

  return (
    <div className="stack">
      <section className="panel">
        <h2>Import plików PDF</h2>
        <div
          className={`dropzone ${dragging ? 'dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void upload(e.dataTransfer.files);
          }}
        >
          <p>Przeciągnij pliki KPiR w formacie PDF lub</p>
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            {busy ? 'Wysyłanie…' : 'Wybierz pliki'}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            hidden
            onChange={(e) => e.target.files && void upload(e.target.files)}
          />
          <p className="small muted" style={{ marginBottom: 0 }}>
            Pliki pozostają na tym komputerze. Nic nie jest wysyłane do internetu.
          </p>
        </div>
        {message && (
          <p className="notice info" style={{ marginTop: 12 }} role="status">
            {message}
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Dokumenty ({documents.length})</h2>
        {documents.length === 0 ? (
          <p className="muted">Brak zaimportowanych dokumentów.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Nazwa</th>
                <th scope="col">Status</th>
                <th scope="col">Strony</th>
                <th scope="col">Rekordy</th>
                <th scope="col">Problemy</th>
                <th scope="col">Akcje</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>{doc.originalName}</td>
                  <td>
                    <span
                      className={`status-chip ${
                        doc.status === 'ready'
                          ? 'ok'
                          : doc.status === 'failed'
                            ? 'critical'
                            : 'review'
                      }`}
                    >
                      {translateStatus(doc.status)}
                      {doc.errorCode ? ` (${doc.errorCode})` : ''}
                    </span>
                  </td>
                  <td className="numeric">{doc.pageCount}</td>
                  <td className="numeric">{doc.recordCount}</td>
                  <td className="numeric">
                    {(doc.issueCounts.critical ?? 0) + (doc.issueCounts.warning ?? 0)}
                  </td>
                  <td>
                    <div className="row">
                      <button
                        type="button"
                        onClick={() => onOpen(doc.id)}
                        disabled={doc.status !== 'ready'}
                      >
                        Otwórz
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void onDeleted(doc.id)}
                      >
                        Usuń
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function translateStatus(status: string): string {
  return (
    {
      uploaded: 'Wczytany',
      analyzing: 'Analiza',
      extracting: 'Ekstrakcja',
      ready: 'Gotowy',
      failed: 'Błąd',
      deleting: 'Usuwanie',
    }[status] ?? status
  );
}
