"""PyMuPDF adapter: positioned tokens, vector lines and page rendering.

This is the only module allowed to know about the PDF library. It converts every
coordinate into the canonical normalised ``0..1`` space of the *rotated* page, so
the domain never deals with library specific origins.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from ...domain.models import Token
from ...domain.values import BBox

__all__ = [
    "PdfError",
    "PageGeometry",
    "PageContent",
    "PdfDocumentAdapter",
]


class PdfError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PageGeometry:
    page_number: int  # 1-based, API convention
    width: float
    height: float
    rotation: int


@dataclass(slots=True)
class PageContent:
    geometry: PageGeometry
    tokens: list[Token]
    horizontal_lines: list[float]  # normalised y positions of rule lines
    vertical_lines: list[float]  # normalised x positions of rule lines

    @property
    def has_text(self) -> bool:
        return any(t.raw_text.strip() for t in self.tokens)


class PdfDocumentAdapter:
    """Read-only access to a PDF file. The original file is never modified."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            self._doc = fitz.open(self._path)
        except Exception as exc:  # noqa: BLE001 - library raises many types
            raise PdfError("PDF_OPEN_FAILED", "cannot open the PDF document") from exc
        if self._doc.needs_pass:
            self._doc.close()
            raise PdfError("PDF_ENCRYPTED", "the PDF document is password protected")

    def __enter__(self) -> PdfDocumentAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        # Closing must never raise: it runs in cleanup paths.
        with contextlib.suppress(Exception):
            self._doc.close()

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def metadata_period_hint(self) -> str | None:
        """Best-effort period hint from the first page text (never trusted blindly)."""
        return None

    def page_geometry(self, page_number: int) -> PageGeometry:
        page = self._load(page_number)
        # ``page.rect`` already reports the *rotated* (displayed) page box, while
        # text and drawing coordinates come back in unrotated space. The canonical
        # space is the displayed one, so widths are taken straight from the rect
        # and every rect is mapped through ``page.rotation_matrix``.
        rect = page.rect
        return PageGeometry(page_number, rect.width, rect.height, int(page.rotation) % 360)

    def iter_pages(self, page_numbers: list[int] | None = None) -> Iterator[PageContent]:
        numbers = page_numbers or list(range(1, self.page_count + 1))
        for number in numbers:
            yield self.page_content(number)

    def page_content(self, page_number: int) -> PageContent:
        page = self._load(page_number)
        geometry = self.page_geometry(page_number)
        tokens = self._extract_tokens(page, geometry)
        h_lines, v_lines = self._extract_rule_lines(page, geometry)
        return PageContent(
            geometry=geometry,
            tokens=tokens,
            horizontal_lines=h_lines,
            vertical_lines=v_lines,
        )

    def render_page(self, page_number: int, target: Path, *, dpi: int = 130) -> Path:
        """Render a page to WebP for the review panel (local file, no external viewer)."""
        page = self._load(page_number)
        target.parent.mkdir(parents=True, exist_ok=True)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        try:
            pixmap.save(str(target))
        except Exception:  # noqa: BLE001 - fall back to PNG when WebP is unavailable
            fallback = target.with_suffix(".png")
            pixmap.save(str(fallback))
            return fallback
        return target

    # ------------------------------------------------------------------ internals
    def _load(self, page_number: int) -> Any:
        if page_number < 1 or page_number > self.page_count:
            raise PdfError("PAGE_OUT_OF_RANGE", f"page {page_number} does not exist")
        return self._doc.load_page(page_number - 1)

    def _extract_tokens(self, page: Any, geometry: PageGeometry) -> list[Token]:
        """Words with coordinates, never the linear ``get_text()`` output."""
        tokens: list[Token] = []
        try:
            raw_dict = page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT)
        except Exception as exc:  # noqa: BLE001
            raise PdfError("PDF_TEXT_FAILED", "cannot read the text layer") from exc

        width = geometry.width or 1.0
        height = geometry.height or 1.0
        matrix = page.rotation_matrix

        for block_id, block in enumerate(raw_dict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line_id, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    font_name = span.get("font")
                    font_size = span.get("size")
                    for word_id, word in enumerate(_split_span_into_words(span)):
                        text, rect = word
                        x0, y0, x1, y1 = _to_canonical(rect, matrix)
                        bbox = BBox(
                            max(0.0, min(1.0, x0 / width)),
                            max(0.0, min(1.0, y0 / height)),
                            max(0.0, min(1.0, x1 / width)),
                            max(0.0, min(1.0, y1 / height)),
                        )
                        tokens.append(
                            Token(
                                raw_text=text,
                                bbox=bbox,
                                page_number=geometry.page_number,
                                baseline=bbox.y1,
                                font_name=font_name,
                                font_size=font_size,
                                block_id=block_id,
                                line_id=line_id,
                                word_id=word_id,
                            )
                        )
        tokens.sort(key=lambda t: (round(t.baseline, 4), t.bbox.x0))
        return tokens

    def _extract_rule_lines(
        self, page: Any, geometry: PageGeometry
    ) -> tuple[list[float], list[float]]:
        """Vector rule lines give the strongest signal for table boundaries."""
        h_lines: list[float] = []
        v_lines: list[float] = []
        width = geometry.width or 1.0
        height = geometry.height or 1.0
        matrix = page.rotation_matrix
        try:
            drawings = page.get_drawings()
        except Exception:  # noqa: BLE001 - drawings are optional
            return h_lines, v_lines

        for drawing in drawings:
            for item in drawing.get("items", []):
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    rect = (min(p1.x, p2.x), min(p1.y, p2.y), max(p1.x, p2.x), max(p1.y, p2.y))
                elif kind == "re":
                    r = item[1]
                    rect = (r.x0, r.y0, r.x1, r.y1)
                else:
                    continue
                x0, y0, x1, y1 = _to_canonical(rect, matrix)
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                if kind == "re" and dx > 0.02 * width and dy > 0.02 * height:
                    # A filled table frame: record all four edges.
                    h_lines.extend([y0 / height, y1 / height])
                    v_lines.extend([x0 / width, x1 / width])
                    continue
                if dy <= max(1.5, 0.002 * height) and dx > 0.05 * width:
                    h_lines.append(((y0 + y1) / 2) / height)
                elif dx <= max(1.5, 0.002 * width) and dy > 0.02 * height:
                    v_lines.append(((x0 + x1) / 2) / width)
        return sorted(h_lines), sorted(v_lines)


def _split_span_into_words(span: dict[str, Any]) -> list[tuple[str, tuple[float, ...]]]:
    """Rebuild words from character boxes so glyph geometry stays available."""
    chars = span.get("chars")
    if not chars:
        text = (span.get("text") or "").strip()
        if not text:
            return []
        return [(text, tuple(span["bbox"]))]

    words: list[tuple[str, tuple[float, ...]]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = "".join(c["c"] for c in current).strip()
        if text:
            x0 = min(c["bbox"][0] for c in current)
            y0 = min(c["bbox"][1] for c in current)
            x1 = max(c["bbox"][2] for c in current)
            y1 = max(c["bbox"][3] for c in current)
            words.append((text, (x0, y0, x1, y1)))
        current.clear()

    for char in chars:
        if char["c"].isspace():
            flush()
        else:
            current.append(char)
    flush()
    return words


def _to_canonical(rect: tuple[float, ...], matrix: Any) -> tuple[float, float, float, float]:
    """Map a rect from unrotated PDF space into the displayed (canonical) space.

    PyMuPDF hands back text and drawing coordinates in the unrotated page space
    while ``page.rect`` describes the rotated page. ``page.rotation_matrix`` is
    the library's own bridge between the two, so we use it rather than
    re-deriving the transform by hand.
    """
    mapped = fitz.Rect(rect[0], rect[1], rect[2], rect[3]) * matrix
    mapped.normalize()
    return mapped.x0, mapped.y0, mapped.x1, mapped.y1
