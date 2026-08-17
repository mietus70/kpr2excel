/**
 * Export configurator: scope, columns (selection + order), rows and filters,
 * plus a live summary. The preview is marked stale whenever the configuration
 * or the underlying data changes, and must be refreshed before exporting.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, api } from '@/api/client';
import type {
  ExportDefinition,
  ExportPolicy,
  ExportPreview,
  KpirDocument,
  Profile,
} from '@/types/api';

interface Props {
  profile: Profile;
  documents: KpirDocument[];
  initialDocumentIds: string[];
  excludedRecordIds: string[];
  explicitRecordIds: string[];
  onClearRowSelection: () => void;
}

export function ExportConfigurator({
  profile,
  documents,
  initialDocumentIds,
  excludedRecordIds,
  explicitRecordIds,
  onClearRowSelection,
}: Props) {
  const moneyColumns = useMemo(
    () => profile.columns.filter((c) => c.type === 'money'),
    [profile],
  );

  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>(() => {
    const ready = new Set(documents.map((d) => d.id));
    const initial = initialDocumentIds.filter((id) => ready.has(id));
    return initial.length > 0 ? initial : documents.map((d) => d.id);
  });
  const [columnKeys, setColumnKeys] = useState<string[]>(profile.defaultExportColumns);
  const [filterColumn, setFilterColumn] = useState(profile.defaultFilterColumn);
  const [minAmount, setMinAmount] = useState('');
  const [maxAmount, setMaxAmount] = useState('');
  const [policy, setPolicy] = useState<ExportPolicy>('strict');
  const [includeIssues, setIncludeIssues] = useState(false);
  const [useExplicitRows, setUseExplicitRows] = useState(false);

  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [stale, setStale] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [download, setDownload] = useState<{ url: string; rows: number } | null>(null);

  const definition: ExportDefinition = useMemo(() => {
    const filters =
      minAmount.trim() || maxAmount.trim()
        ? [
            {
              type: 'money_range' as const,
              columnKey: filterColumn,
              min: minAmount.trim() || null,
              max: maxAmount.trim() || null,
              bounds: 'inclusive' as const,
            },
          ]
        : [];
    return {
      scope: { type: 'documents', documentIds: selectedDocumentIds },
      columnKeys,
      rowSelection: useExplicitRows
        ? { mode: 'EXPLICIT', includedRecordIds: explicitRecordIds, excludedRecordIds: [] }
        : { mode: 'ALL_MATCHING', includedRecordIds: [], excludedRecordIds },
      filters,
      policy,
      includeIssuesSheet: includeIssues,
    };
  }, [
    columnKeys,
    filterColumn,
    minAmount,
    maxAmount,
    policy,
    includeIssues,
    selectedDocumentIds,
    useExplicitRows,
    explicitRecordIds,
    excludedRecordIds,
  ]);

  // Keep selection in sync when documents appear or disappear after extraction.
  useEffect(() => {
    const readyIds = documents.map((d) => d.id);
    const ready = new Set(readyIds);
    setSelectedDocumentIds((current) => {
      const kept = current.filter((id) => ready.has(id));
      const added = readyIds.filter((id) => !current.includes(id));
      if (current.length === 0) return readyIds;
      if (added.length === 0 && kept.length === current.length) return current;
      return [...kept, ...added];
    });
  }, [documents]);

  // Any configuration change invalidates the preview.
  useEffect(() => {
    setStale(true);
    setDownload(null);
  }, [definition]);

  const toggleDocument = (id: string) => {
    setSelectedDocumentIds((ids) =>
      ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id],
    );
  };

  const selectAllDocuments = () => setSelectedDocumentIds(documents.map((d) => d.id));
  const selectNoDocuments = () => setSelectedDocumentIds([]);

  const selectedRowTotal = documents
    .filter((d) => selectedDocumentIds.includes(d.id))
    .reduce((sum, d) => sum + d.recordCount, 0);

  const refreshPreview = useCallback(async () => {
    if (selectedDocumentIds.length === 0) {
      setError('Wybierz co najmniej jeden dokument.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.previewExport(definition);
      setPreview(result);
      setStale(false);
    } catch (err) {
      setPreview(null);
      setError(err instanceof ApiError ? err.message : 'Nie udało się pobrać podglądu');
    } finally {
      setBusy(false);
    }
  }, [definition, selectedDocumentIds]);

  const runExport = useCallback(async () => {
    if (!preview || stale) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createExport({
        ...definition,
        sourceRevision: preview.sourceRevision,
      });
      // The worker builds the file; poll until it is ready.
      let current = created;
      for (let attempt = 0; attempt < 120 && current.status !== 'ready'; attempt += 1) {
        if (current.status === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 500));
        current = await api.getExport(created.id);
      }
      if (current.status === 'ready' && current.downloadUrl) {
        setDownload({ url: current.downloadUrl, rows: current.rowCount ?? 0 });
      } else {
        setError(`Eksport nie powiódł się (${current.errorCode ?? 'timeout'}).`);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Nie udało się utworzyć eksportu');
    } finally {
      setBusy(false);
    }
  }, [definition, preview, stale]);

  const toggleColumn = (key: string) => {
    setColumnKeys((keys) =>
      keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key],
    );
  };

  const move = (key: string, delta: number) => {
    setColumnKeys((keys) => {
      const index = keys.indexOf(key);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= keys.length) return keys;
      const next = [...keys];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const unselected = profile.columns.filter((c) => !columnKeys.includes(c.key));

  return (
    <div className="stack">
      <div className="export-grid">
        {/* ---------------------------------------------------- 1. scope */}
        <section className="panel">
          <h3>1. Zakres źródłowy</h3>
          {documents.length === 0 ? (
            <p className="muted small">Brak zaimportowanych dokumentów gotowych do eksportu.</p>
          ) : (
            <>
              <div className="row" style={{ marginBottom: 8 }}>
                <button type="button" onClick={selectAllDocuments}>
                  Zaznacz wszystkie
                </button>
                <button type="button" onClick={selectNoDocuments}>
                  Odznacz wszystkie
                </button>
                {documents.some((d) => (d.issueCounts.critical ?? 0) > 0) && (
                  <button
                    type="button"
                    onClick={() =>
                      setSelectedDocumentIds(
                        documents
                          .filter((d) => (d.issueCounts.critical ?? 0) === 0)
                          .map((d) => d.id),
                      )
                    }
                  >
                    Pomiń pliki z błędami krytycznymi
                  </button>
                )}
              </div>
              <ul className="column-list">
                {documents.map((doc) => {
                  const checked = selectedDocumentIds.includes(doc.id);
                  const critical = doc.issueCounts.critical ?? 0;
                  return (
                    <li key={doc.id}>
                      <input
                        type="checkbox"
                        id={`doc-${doc.id}`}
                        checked={checked}
                        onChange={() => toggleDocument(doc.id)}
                      />
                      <label htmlFor={`doc-${doc.id}`}>{doc.originalName}</label>
                      <span className="spacer" />
                      <span className="small muted mono">{doc.recordCount} wierszy</span>
                      {critical > 0 && (
                        <span className="status-chip critical" title="Blokuje eksport w trybie strict">
                          {critical} krytyczne
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          <p className="small muted" style={{ marginBottom: 0 }}>
            {selectedDocumentIds.length === 0
              ? 'Zaznacz co najmniej jeden plik PDF.'
              : `Jeden plik Excel z ${selectedDocumentIds.length} PDF (${selectedRowTotal} wierszy przed filtrami).`}
          </p>
          {documents.some(
            (d) => selectedDocumentIds.includes(d.id) && (d.issueCounts.critical ?? 0) > 0,
          ) && (
            <p className="notice warn" style={{ marginTop: 8 }} role="status">
              Zaznaczony plik ma błędy krytyczne. W trybie „strict” zablokuje cały wspólny
              Excel. Odznacz go albo użyj „Pomiń pliki z błędami krytycznymi”.
            </p>
          )}
        </section>

        {/* -------------------------------------------------- 2. columns */}
        <section className="panel">
          <h3>2. Kolumny i kolejność</h3>
          <ul className="column-list">
            {columnKeys.map((key, index) => {
              const column = profile.columns.find((c) => c.key === key);
              return (
                <li key={key}>
                  <input
                    type="checkbox"
                    id={`col-${key}`}
                    checked
                    onChange={() => toggleColumn(key)}
                    aria-label={`Usuń kolumnę ${column?.label ?? key} z eksportu`}
                  />
                  <label htmlFor={`col-${key}`}>{column?.label ?? key}</label>
                  <span className="order-buttons">
                    <button
                      type="button"
                      onClick={() => move(key, -1)}
                      disabled={index === 0}
                      aria-label={`Przesuń ${column?.label ?? key} w górę`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => move(key, 1)}
                      disabled={index === columnKeys.length - 1}
                      aria-label={`Przesuń ${column?.label ?? key} w dół`}
                    >
                      ↓
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
          {unselected.length > 0 && (
            <>
              <h3 style={{ marginTop: 12 }}>Pominięte</h3>
              <ul className="column-list">
                {unselected.map((column) => (
                  <li key={column.key}>
                    <input
                      type="checkbox"
                      id={`col-off-${column.key}`}
                      checked={false}
                      onChange={() => toggleColumn(column.key)}
                    />
                    <label htmlFor={`col-off-${column.key}`} className="muted">
                      {column.label}
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
          {columnKeys.length === 0 && (
            <p className="notice error" role="alert">
              Wybierz co najmniej jedną kolumnę.
            </p>
          )}
        </section>

        {/* --------------------------------------------- 3. rows/filters */}
        <section className="panel">
          <h3>3. Wiersze i filtry</h3>
          <div className="stack">
            <label className="row">
              <input
                type="radio"
                name="rowmode"
                checked={!useExplicitRows}
                onChange={() => setUseExplicitRows(false)}
              />
              <span>
                Wszystkie pasujące
                {excludedRecordIds.length > 0 && (
                  <span className="muted small"> (bez {excludedRecordIds.length} wykluczonych)</span>
                )}
              </span>
            </label>
            <label className="row">
              <input
                type="radio"
                name="rowmode"
                checked={useExplicitRows}
                onChange={() => setUseExplicitRows(true)}
                disabled={explicitRecordIds.length === 0}
              />
              <span>
                Tylko zaznaczone
                <span className="muted small"> ({explicitRecordIds.length} wierszy)</span>
              </span>
            </label>
            {(excludedRecordIds.length > 0 || explicitRecordIds.length > 0) && (
              <button type="button" onClick={onClearRowSelection}>
                Wyczyść ręczny wybór wierszy
              </button>
            )}

            <hr style={{ width: '100%', border: 0, borderTop: '1px solid var(--border)' }} />

            <label htmlFor="filter-column">Filtr kwotowy — kolumna</label>
            <select
              id="filter-column"
              value={filterColumn}
              onChange={(e) => setFilterColumn(e.target.value)}
            >
              {moneyColumns.map((column) => (
                <option key={column.key} value={column.key}>
                  {column.label}
                </option>
              ))}
            </select>
            <div className="row">
              <label htmlFor="filter-min" style={{ minWidth: 28 }}>
                od
              </label>
              <input
                id="filter-min"
                className="mono"
                inputMode="decimal"
                placeholder="np. 100.00"
                value={minAmount}
                onChange={(e) => setMinAmount(e.target.value)}
                style={{ width: 120 }}
              />
              <label htmlFor="filter-max" style={{ minWidth: 28 }}>
                do
              </label>
              <input
                id="filter-max"
                className="mono"
                inputMode="decimal"
                placeholder="np. 1000.00"
                value={maxAmount}
                onChange={(e) => setMaxAmount(e.target.value)}
                style={{ width: 120 }}
              />
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              Granice są włączne. Kolumna filtra nie musi być eksportowana. Wiersze bez kwoty nie
              spełniają aktywnego filtra.
            </p>

            <hr style={{ width: '100%', border: 0, borderTop: '1px solid var(--border)' }} />

            <label htmlFor="policy">Polityka eksportu</label>
            <select
              id="policy"
              value={policy}
              onChange={(e) => setPolicy(e.target.value as ExportPolicy)}
            >
              <option value="strict">strict — blokuj przy problemach krytycznych</option>
              <option value="reviewed">reviewed — po akceptacji problemów</option>
              <option value="draft">draft — eksport roboczy</option>
            </select>
            <label className="row">
              <input
                type="checkbox"
                checked={includeIssues}
                onChange={(e) => setIncludeIssues(e.target.checked)}
              />
              <span>Dodaj arkusz „Problemy”</span>
            </label>
          </div>
        </section>

        {/* ------------------------------------------------- 4. summary */}
        <section className="panel">
          <h3>4. Podsumowanie</h3>
          {stale && (
            <p className="notice warn" role="status">
              Podgląd jest nieaktualny. Odśwież go przed eksportem.
            </p>
          )}
          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}
          {preview && !stale && (
            <ul className="summary-list">
              <li>
                <span>Wiersze w pliku</span>
                <span className="summary-value">{preview.matchingRowCount}</span>
              </li>
              <li>
                <span>Kolumny w pliku</span>
                <span className="summary-value">{preview.columnCount}</span>
              </li>
              <li>
                <span>Wszystkie wiersze w zakresie</span>
                <span className="summary-value">{preview.totalRowCount}</span>
              </li>
              <li>
                <span>Odrzucone przez zakres kwot</span>
                <span className="summary-value">{preview.excludedByRangeCount}</span>
              </li>
              <li>
                <span>Odrzucone: pusta/niepoprawna kwota</span>
                <span className="summary-value">{preview.excludedForNullOrInvalidCount}</span>
              </li>
              <li>
                <span>Odrzucone ręcznym wyborem</span>
                <span className="summary-value">{preview.excludedBySelectionCount}</span>
              </li>
              <li>
                <span>Problemy krytyczne w wybranych</span>
                <span className="summary-value">{preview.blockingIssueCount}</span>
              </li>
            </ul>
          )}
          {preview && !stale && preview.activeFilters.length > 0 && (
            <p className="small muted">
              Aktywny filtr:{' '}
              {preview.activeFilters
                .map(
                  (f) =>
                    `${f.label} ${f.min ? `od ${f.min}` : ''} ${f.max ? `do ${f.max}` : ''}`.trim(),
                )
                .join('; ')}
            </p>
          )}
          {preview && !stale && preview.blockingIssueCount > 0 && policy === 'strict' && (
            <p className="notice warn">
              Polityka „strict” zablokuje eksport. Popraw problemy lub zmień politykę.
            </p>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <button type="button" onClick={() => void refreshPreview()} disabled={busy}>
              {busy ? 'Liczę…' : 'Odśwież podgląd'}
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => void runExport()}
              disabled={busy || stale || !preview || columnKeys.length === 0}
            >
              Utwórz XLSX
            </button>
          </div>

          {download && (
            <p className="notice ok" style={{ marginTop: 10 }}>
              Gotowe: {download.rows} wierszy.{' '}
              <a href={download.url} download>
                Pobierz plik XLSX
              </a>
            </p>
          )}
        </section>
      </div>

      {preview && !stale && preview.sampleRows.length > 0 && (
        <section className="panel">
          <h3>Przykładowe pierwsze wiersze</h3>
          <div className="table-scroll" style={{ maxHeight: 240 }}>
            <table className="data-table">
              <thead>
                <tr>
                  {preview.columnLabels.map((column) => (
                    <th key={column.key} scope="col">
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.sampleRows.map((row) => (
                  <tr key={row.recordId}>
                    {preview.columnLabels.map((column) => (
                      <td key={column.key}>
                        {row.values[column.key] ?? <span className="cell-empty">(puste)</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
