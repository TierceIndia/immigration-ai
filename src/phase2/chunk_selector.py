"""Deterministic candidate-chunk selection.

This stage is intentionally recall-oriented: it is fine (expected) for it to over-select. Its job
is only to cut down "every chunk in the document" to "chunks worth spending an LLM call on" - the
LLM extractor and the deterministic validators downstream decide whether a real requirement
exists. Never used to filter final requirements - only to control extraction cost (see
requirements_extraction task spec, "COST CONTROL").

Heuristics (any one match makes a chunk a candidate):
- heading path contains an eligibility-related term
- body text contains modal/requirement language ("must", "must not", "eligible", "required",
  "cannot", "unless", "if", "provided that", "subject to", etc.)
- chunk_type is TABLE (thresholds/points tables are almost always relevant)
- chunk_type is LIST (document/eligibility checklists)

HEADING chunks are never themselves selected as extraction candidates (a heading alone carries no
evidence) but they are always included as neighboring context for a selected sibling, so the LLM
sees "Eligibility > Age requirement" alongside the paragraph under it.
"""
from __future__ import annotations

from dataclasses import dataclass

_HEADING_TERMS = (
    "eligib", "requirement", "criteria", "age", "experience", "language", "english",
    "points", "invitation", "nomination", "skills assessment", "occupation", "health",
    "character", "document", "financial", "sponsorship", "regional", "validity", "fee",
    "family", "identity", "visa status",
)

_MODAL_TERMS = (
    "must", "must not", "cannot", "eligible", "required", "requires", "unless",
    "provided that", "subject to", "at least", "no more than", "not exceed",
    "you need to", "you will need", "condition", "exempt", "exception",
)


@dataclass
class ChunkCandidate:
    chunk_id: int
    chunk_type: str
    heading_path: str
    order_index: int
    raw_text: str
    page_number: int | None
    reasons: list[str]


def _heading_matches(heading_path: str) -> bool:
    hp = (heading_path or "").lower()
    return any(term in hp for term in _HEADING_TERMS)


def _text_matches(text: str) -> list[str]:
    t = (text or "").lower()
    return [term for term in _MODAL_TERMS if term in t]


def select_candidates(chunks: list[dict]) -> list[ChunkCandidate]:
    """chunks: list of dicts with keys chunk_id, chunk_type, heading_path, order_index,
    raw_text, page_number (as stored in document_chunks / extracted.json).
    """
    candidates: list[ChunkCandidate] = []
    for c in chunks:
        if c["chunk_type"] == "HEADING":
            continue  # headings are context only, never extraction candidates themselves

        reasons: list[str] = []
        if c["chunk_type"] == "TABLE":
            reasons.append("chunk_type=TABLE")
        if c["chunk_type"] == "LIST":
            reasons.append("chunk_type=LIST")
        if _heading_matches(c.get("heading_path", "")):
            reasons.append("heading_match")
        modal_hits = _text_matches(c["raw_text"])
        if modal_hits:
            reasons.append(f"modal_terms={','.join(modal_hits[:5])}")

        if reasons:
            candidates.append(
                ChunkCandidate(
                    chunk_id=c["chunk_id"],
                    chunk_type=c["chunk_type"],
                    heading_path=c.get("heading_path", ""),
                    order_index=c["order_index"],
                    raw_text=c["raw_text"],
                    page_number=c.get("page_number"),
                    reasons=reasons,
                )
            )
    return candidates


def build_context_window(all_chunks: list[dict], candidate: ChunkCandidate, window: int = 1) -> list[dict]:
    """Return up to `window` neighboring chunks on each side of the candidate (by order_index),
    always including the nearest preceding HEADING chunk regardless of window size, so the LLM
    has the section title even if it's more than `window` away.
    """
    by_order = sorted(all_chunks, key=lambda c: c["order_index"])
    idx = next((i for i, c in enumerate(by_order) if c["chunk_id"] == candidate.chunk_id), None)
    if idx is None:
        return []

    neighbors = []
    lo = max(0, idx - window)
    hi = min(len(by_order), idx + window + 1)
    for i in range(lo, hi):
        if by_order[i]["chunk_id"] != candidate.chunk_id:
            neighbors.append(by_order[i])

    # Ensure nearest preceding heading is present
    nearest_heading = None
    for i in range(idx, -1, -1):
        if by_order[i]["chunk_type"] == "HEADING":
            nearest_heading = by_order[i]
            break
    if nearest_heading and nearest_heading["chunk_id"] not in {n["chunk_id"] for n in neighbors}:
        neighbors.insert(0, nearest_heading)

    return neighbors
