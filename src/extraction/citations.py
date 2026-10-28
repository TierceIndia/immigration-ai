"""Human-readable provenance anchors for evidence.

A requirement's evidence always points at a chunk. This module turns that
chunk into a short, honest anchor a human can check against the source:

* a PDF chunk -> ``"p. 12"`` (the page the text was extracted from)
* an HTML chunk -> its heading path (``"Division 2 > Age"``)
* nothing usable -> ``None`` (never a made-up anchor)

The anchor is *derived* from the chunk on every read, never stored as free
text on the requirement, so it cannot drift away from the chunk it points
at. It is a locator for a human reader; it is deliberately NOT part of the
evidence-verification contract - the quote itself is still checked as an
exact substring of the chunk (see extraction/evidence_validator.py).
"""
from __future__ import annotations

from typing import Any


def format_citation(
    *,
    page_number: int | None = None,
    heading_path: str | None = None,
    chunk_id: int | None = None,
) -> str | None:
    """Return the anchor for one chunk, or None when there is nothing to anchor.

    ``page_number`` wins over ``heading_path``: a PDF page is a precise,
    stable locator, while a heading path is only as good as the headings the
    publisher chose that day.
    """
    if page_number is not None:
        try:
            return f"p. {int(page_number)}"
        except (TypeError, ValueError):
            pass
    heading = (heading_path or "").strip()
    if heading:
        return heading
    if chunk_id is not None:
        return f"chunk #{chunk_id}"
    return None


def citation_for_chunk(chunk: Any) -> str | None:
    """Return the anchor for a chunk ORM object or chunk dict, or None.

    Accepts either shape on purpose: the API layer has ORM rows, the
    extraction layer has plain dicts, and both must produce the same anchor.
    """
    if chunk is None:
        return None
    if isinstance(chunk, dict):
        return format_citation(
            page_number=chunk.get("page_number"),
            heading_path=chunk.get("heading_path"),
            chunk_id=chunk.get("chunk_id"),
        )
    return format_citation(
        page_number=getattr(chunk, "page_number", None),
        heading_path=getattr(chunk, "heading_path", None),
        chunk_id=getattr(chunk, "chunk_id", None),
    )
