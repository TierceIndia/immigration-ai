"""PDF -> text + page numbers.

Detects whether a PDF has a text layer:
- If yes: extract text per page via pypdf, chunked as one PARAGRAPH chunk per page
  (page-level granularity is enough for Phase-1 provenance; Phase-2 can re-chunk
  by paragraph within a page if needed).
- If no (scanned / image-only PDF): does NOT run OCR. Returns needs_ocr=True so the
  pipeline marks the document status='NEEDS_OCR' rather than silently storing empty text.

This matches the brief: "If scanned -> OCR" is a real pipeline branch, but OCR itself
is explicitly out of scope for Phase-1 (see DESIGN.md section 8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from pypdf import PdfReader


@dataclass
class PdfChunk:
    chunk_type: str  # always PARAGRAPH for page-level text in Phase-1
    heading_path: str
    order_index: int
    raw_text: str
    page_number: int


@dataclass
class PdfExtractionResult:
    title: str | None
    chunks: list[PdfChunk] = field(default_factory=list)
    clean_text: str = ""
    needs_ocr: bool = False
    page_count: int = 0


MIN_CHARS_PER_PAGE_TO_COUNT_AS_TEXT = 20


def extract_pdf(raw_bytes: bytes) -> PdfExtractionResult:
    reader = PdfReader(BytesIO(raw_bytes))
    title = None
    try:
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title)
    except Exception:
        title = None

    chunks: list[PdfChunk] = []
    text_parts: list[str] = []
    pages_with_text = 0

    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if len(text) >= MIN_CHARS_PER_PAGE_TO_COUNT_AS_TEXT:
            pages_with_text += 1
            chunks.append(
                PdfChunk(
                    chunk_type="PARAGRAPH",
                    heading_path=f"Page {page_index}",
                    order_index=page_index,
                    raw_text=text,
                    page_number=page_index,
                )
            )
            text_parts.append(text)

    page_count = len(reader.pages)
    needs_ocr = page_count > 0 and pages_with_text == 0

    return PdfExtractionResult(
        title=title,
        chunks=chunks,
        clean_text="\n\n".join(text_parts),
        needs_ocr=needs_ocr,
        page_count=page_count,
    )
