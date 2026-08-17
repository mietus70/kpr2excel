/**
 * Wybór unikalnych ksiąg spośród zaimportowanych PDF-ów.
 *
 * Ten sam rok często leży w bazie dwa razy: oryginał + kopia z biura
 * („…_biuro_optima…”, skan SKM). Eksport wszystkich daje zdublowane wiersze.
 */

import type { KpirDocument } from '@/types/api';

export interface DuplicateHint {
  id: string;
  reason: 'kopia' | 'zawarty';
  ofName: string;
}

function periodDays(from: string | null, to: string | null): number {
  if (!from || !to) return 0;
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return 0;
  return (end - start) / 86_400_000;
}

function namePenalty(name: string): number {
  const lower = name.toLowerCase();
  let penalty = name.length / 200;
  if (/biuro|optima/.test(lower)) penalty += 3;
  if (/\bskm\b|skan|scan/.test(lower)) penalty += 3;
  return penalty;
}

function score(doc: KpirDocument): number {
  return doc.recordCount * 10 + periodDays(doc.periodFrom, doc.periodTo) - namePenalty(doc.originalName);
}

export function isSameBook(a: KpirDocument, b: KpirDocument): boolean {
  if (a.id === b.id) return false;
  if (a.sizeBytes > 0 && a.sizeBytes === b.sizeBytes && a.pageCount === b.pageCount) {
    return a.recordCount === b.recordCount;
  }
  return Boolean(
    a.periodFrom &&
      a.periodTo &&
      a.periodFrom === b.periodFrom &&
      a.periodTo === b.periodTo &&
      a.recordCount === b.recordCount &&
      a.pageCount === b.pageCount,
  );
}

export function isPeriodSubset(inner: KpirDocument, outer: KpirDocument): boolean {
  if (inner.id === outer.id) return false;
  if (!inner.periodFrom || !inner.periodTo || !outer.periodFrom || !outer.periodTo) return false;
  const contained =
    inner.periodFrom >= outer.periodFrom &&
    inner.periodTo <= outer.periodTo &&
    (inner.periodFrom > outer.periodFrom || inner.periodTo < outer.periodTo);
  return contained && inner.recordCount < outer.recordCount;
}

/** Zostawia jedną księgę z pary kopii i pomija krótszy wycinek tego samego roku. */
export function uniqueDocumentIds(documents: KpirDocument[]): {
  keep: string[];
  dropped: DuplicateHint[];
} {
  const dropped: DuplicateHint[] = [];
  const skip = new Set<string>();
  const ordered = [...documents].sort((a, b) => score(b) - score(a));

  for (const candidate of ordered) {
    if (skip.has(candidate.id)) continue;
    for (const other of documents) {
      if (other.id === candidate.id || skip.has(other.id)) continue;
      if (isSameBook(candidate, other)) {
        skip.add(other.id);
        dropped.push({ id: other.id, reason: 'kopia', ofName: candidate.originalName });
      } else if (isPeriodSubset(other, candidate)) {
        skip.add(other.id);
        dropped.push({ id: other.id, reason: 'zawarty', ofName: candidate.originalName });
      }
    }
  }

  return {
    keep: documents.filter((d) => !skip.has(d.id)).map((d) => d.id),
    dropped,
  };
}
