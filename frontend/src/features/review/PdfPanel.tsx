/**
 * Page preview: a locally rendered image plus an SVG overlay for the source
 * rectangle of the active cell. No external PDF viewer and no CDN.
 */

import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import type { SourceSpan } from '@/types/api';

interface Props {
  documentId: string;
  pageCount: number;
  activeSpan: SourceSpan | null;
  activeLabel?: string;
}

export function PdfPanel({ documentId, pageCount, activeSpan, activeLabel }: Props) {
  const [page, setPage] = useState(1);
  const [loaded, setLoaded] = useState(false);

  // Selecting a cell pulls the panel to the matching page.
  useEffect(() => {
    if (activeSpan && activeSpan.page !== page) {
      setPage(activeSpan.page);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSpan]);

  useEffect(() => setLoaded(false), [page, documentId]);

  const highlight = activeSpan && activeSpan.page === page ? activeSpan.bbox : null;

  return (
    <section className="panel pdf-panel" aria-label="Podgląd strony PDF">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>Strona źródłowa</h2>
        <div className="spacer" />
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          aria-label="Poprzednia strona"
        >
          ‹
        </button>
        <span className="mono small" aria-live="polite">
          {page} / {pageCount}
        </span>
        <button
          type="button"
          onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
          disabled={page >= pageCount}
          aria-label="Następna strona"
        >
          ›
        </button>
      </div>

      <div className="pdf-stage">
        <img
          src={api.pageImageUrl(documentId, page)}
          alt={`Strona ${page} dokumentu`}
          onLoad={() => setLoaded(true)}
        />
        {highlight && loaded && (
          <svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
            <rect
              className="highlight-rect"
              x={highlight[0]}
              y={highlight[1]}
              width={Math.max(0.002, highlight[2] - highlight[0])}
              height={Math.max(0.002, highlight[3] - highlight[1])}
            />
          </svg>
        )}
      </div>

      <p className="small muted" style={{ marginBottom: 0 }}>
        {activeSpan
          ? `Podświetlono źródło: ${activeLabel ?? 'wybrana komórka'} (strona ${activeSpan.page}).`
          : 'Wybierz komórkę w tabeli, aby zobaczyć jej źródło w dokumencie.'}
      </p>
    </section>
  );
}
