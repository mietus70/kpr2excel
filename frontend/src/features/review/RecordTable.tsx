/**
 * Records table with inline editing.
 *
 * The UI distinguishes four cell states, never by colour alone:
 *   extracted (plain), manually corrected (M), needs review (!),
 *   critical (!!) and empty (italic placeholder).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import type { Cell, KpirRecord, Profile } from '@/types/api';

interface Props {
  profile: Profile;
  records: KpirRecord[];
  columns: string[];
  selectedCellId: string | null;
  onSelectCell: (cell: Cell, record: KpirRecord) => void;
  onCellUpdated: (recordId: string, cell: Cell, recordStatus: string) => void;
  reviewBelow?: number;
  criticalBelow?: number;
}

interface EditState {
  cellId: string;
  value: string;
  saving: boolean;
  error: string | null;
}

export function RecordTable({
  profile,
  records,
  columns,
  selectedCellId,
  onSelectCell,
  onCellUpdated,
  reviewBelow = 0.85,
  criticalBelow = 0.6,
}: Props) {
  const [edit, setEdit] = useState<EditState | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (edit && inputRef.current) inputRef.current.focus();
  }, [edit?.cellId]);

  const startEdit = useCallback((cell: Cell) => {
    setEdit({ cellId: cell.id, value: cell.value ?? '', saving: false, error: null });
  }, []);

  const commit = useCallback(
    async (cell: Cell, record: KpirRecord) => {
      if (!edit || edit.cellId !== cell.id) return;
      const next = edit.value.trim();
      if (next === (cell.value ?? '')) {
        setEdit(null);
        return;
      }
      setEdit({ ...edit, saving: true, error: null });
      try {
        const result = await api.patchCell(cell.id, next === '' ? null : next, cell.revision);
        onCellUpdated(record.id, result.cell, result.recordStatus);
        setEdit(null);
      } catch (error) {
        const message =
          error instanceof ApiError
            ? error.isConflict
              ? `Konflikt wersji. Aktualna wartość: ${error.currentValue ?? '(pusta)'}`
              : error.message
            : 'Nie udało się zapisać zmiany';
        setEdit((current) => (current ? { ...current, saving: false, error: message } : null));
      }
    },
    [edit, onCellUpdated],
  );

  const revert = useCallback(
    async (cell: Cell, record: KpirRecord) => {
      try {
        const result = await api.revertCell(cell.id, cell.revision);
        onCellUpdated(record.id, result.cell, result.recordStatus);
      } catch {
        /* surfaced by the row status on the next refresh */
      }
    },
    [onCellUpdated],
  );

  return (
    <div className="table-scroll">
      <table className="data-table">
        <caption className="sr-only">
          Rekordy KPiR. Enter rozpoczyna edycję zaznaczonej komórki, Escape anuluje.
        </caption>
        <thead>
          <tr>
            <th scope="col" style={{ width: 42 }}>
              Stan
            </th>
            {columns.map((key) => {
              const column = profile.columns.find((c) => c.key === key);
              return (
                <th key={key} scope="col">
                  {column?.label ?? key}
                  {column?.formNumber && <span className="muted small"> ({column.formNumber})</span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.id} aria-selected={record.cells[columns[0]]?.id === selectedCellId}>
              <td>
                <span className={`status-chip ${record.status}`}>
                  {record.status === 'ok' ? 'OK' : record.status === 'review' ? 'Sprawdź' : 'Błąd'}
                </span>
              </td>
              {columns.map((key) => {
                const cell = record.cells[key];
                const column = profile.columns.find((c) => c.key === key);
                const numeric = column?.type === 'money' || column?.type === 'integer';
                if (!cell) {
                  return <td key={key} className={numeric ? 'numeric' : undefined} />;
                }
                const editing = edit?.cellId === cell.id;
                return (
                  <td
                    key={key}
                    className={numeric ? 'numeric' : undefined}
                    onClick={() => onSelectCell(cell, record)}
                  >
                    {editing ? (
                      <>
                        <input
                          ref={inputRef}
                          className="cell-editor"
                          value={edit.value}
                          disabled={edit.saving}
                          aria-label={`Edycja: ${column?.label ?? key}`}
                          onChange={(e) =>
                            setEdit((c) => (c ? { ...c, value: e.target.value } : null))
                          }
                          onBlur={() => void commit(cell, record)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              void commit(cell, record);
                            } else if (e.key === 'Escape') {
                              e.preventDefault();
                              setEdit(null);
                            }
                          }}
                        />
                        {edit.error && (
                          <div className="small" style={{ color: 'var(--crit)' }} role="alert">
                            {edit.error}
                          </div>
                        )}
                      </>
                    ) : (
                      <button
                        type="button"
                        className="cell-value"
                        style={{
                          border: 'none',
                          background: 'none',
                          padding: 0,
                          width: '100%',
                          textAlign: numeric ? 'right' : 'left',
                        }}
                        onClick={() => onSelectCell(cell, record)}
                        onDoubleClick={() => startEdit(cell)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            startEdit(cell);
                          }
                        }}
                        aria-label={describeCell(cell, column?.label ?? key, reviewBelow, criticalBelow)}
                      >
                        <CellContent
                          cell={cell}
                          reviewBelow={reviewBelow}
                          criticalBelow={criticalBelow}
                        />
                      </button>
                    )}
                    {cell.isManual && !editing && (
                      <button
                        type="button"
                        className="small"
                        style={{ border: 'none', background: 'none', padding: 0, color: 'var(--accent)' }}
                        onClick={(e) => {
                          e.stopPropagation();
                          void revert(cell, record);
                        }}
                      >
                        cofnij
                      </button>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CellContent({
  cell,
  reviewBelow,
  criticalBelow,
}: {
  cell: Cell;
  reviewBelow: number;
  criticalBelow: number;
}) {
  if (cell.value === null || cell.value === '') {
    return <span className="cell-empty">(puste)</span>;
  }
  const marker = cell.isManual
    ? { cls: 'manual', text: 'M', title: 'Wartość poprawiona ręcznie' }
    : cell.confidence < criticalBelow
      ? { cls: 'critical', text: '!!', title: 'Bardzo niska pewność' }
      : cell.confidence < reviewBelow
        ? { cls: 'review', text: '!', title: 'Do sprawdzenia' }
        : null;
  return (
    <span className="cell-value">
      <span>{cell.value}</span>
      {marker && (
        <span className={`cell-state ${marker.cls}`} title={marker.title}>
          {marker.text}
        </span>
      )}
    </span>
  );
}

function describeCell(
  cell: Cell,
  label: string,
  reviewBelow: number,
  criticalBelow: number,
): string {
  const value = cell.value ?? 'pusta';
  if (cell.isManual) return `${label}: ${value}, poprawiona ręcznie`;
  if (cell.confidence < criticalBelow) return `${label}: ${value}, bardzo niska pewność`;
  if (cell.confidence < reviewBelow) return `${label}: ${value}, do sprawdzenia`;
  return `${label}: ${value}`;
}
