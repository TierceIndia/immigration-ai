"""Search over the stored government text.

Two ways to find a chunk, in one endpoint:

* **Semantic** - compare the query's embedding with each chunk's embedding
  using pgvector's cosine distance. This finds text that means the same
  thing even when it shares no words with the query.
* **Keyword** - a plain substring match over the chunk text. Crude, but it
  always works, needs no model, no API key and no network.

The endpoint picks semantic when it can and falls back to keyword when it
cannot, and it always says which one it used. A search that quietly
returned keyword results while claiming to be semantic would be worse than
no search at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from extraction.citations import citation_for_chunk
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SearchHit:
    """One chunk that matched."""
    chunk_id: int
    document_id: str
    program_id: str
    title: str | None
    heading_path: str | None
    page_number: int | None
    snippet: str
    similarity: float
    citation: str | None
    mode: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "program_id": self.program_id,
            "title": self.title,
            "heading_path": self.heading_path,
            "page_number": self.page_number,
            "snippet": self.snippet,
            "similarity": self.similarity,
            "citation": self.citation,
            "mode": self.mode,
        }


def vector_support_available(session: Session) -> bool:
    """Whether this database can compare vectors at all."""
    dialect = session.get_bind().dialect.name
    if dialect != "postgresql":
        return False
    row = session.execute(text(
        "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
    )).first()
    return row is not None


def embedded_chunk_count(session: Session) -> int:
    """How many chunks actually carry an embedding."""
    try:
        row = session.execute(text(
            "SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"
        )).first()
    except Exception:
        return 0
    return int(row[0]) if row else 0


def _snippet(raw_text: str, query: str, width: int = 240) -> str:
    """A short excerpt around the first occurrence of the query, if any."""
    text_ = (raw_text or "").strip()
    if len(text_) <= width:
        return text_
    lowered = text_.lower()
    needle = (query or "").strip().lower()
    index = lowered.find(needle) if needle else -1
    if index < 0:
        return text_[:width].rstrip() + "..."
    start = max(0, index - width // 3)
    end = min(len(text_), start + width)
    excerpt = text_[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text_):
        excerpt = excerpt + "..."
    return excerpt


def _base_where(program_id: str | None, document_id: str | None, embedded_only: bool) -> tuple[str, dict]:
    clauses = ["d.status = 'CURRENT'"]
    params: dict[str, Any] = {}
    if embedded_only:
        clauses.append("c.embedding IS NOT NULL")
    if program_id:
        clauses.append("d.program_id = :program_id")
        params["program_id"] = program_id
    if document_id:
        clauses.append("c.document_id = :document_id")
        params["document_id"] = document_id
    return " AND ".join(clauses), params


_SELECT = """
SELECT c.chunk_id, c.document_id, d.program_id, d.title, c.heading_path,
       c.page_number, c.raw_text{extra}
FROM document_chunks c
JOIN government_documents d ON d.document_id = c.document_id
JOIN document_versions v ON v.version_id = c.version_id
                          AND v.version_number = d.current_version
WHERE {where}
"""


def keyword_search(
    session: Session,
    query: str,
    *,
    program_id: str | None = None,
    document_id: str | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Plain substring search over the current version of every chunk."""
    if not (query or "").strip():
        # An empty query would match every chunk, which is not an answer -
        # it is the absence of one.
        return []

    where, params = _base_where(program_id, document_id, embedded_only=False)
    params["query"] = f"%{query}%"
    params["limit"] = limit
    sql = (
        _SELECT.format(where=where, extra="")
        + " AND lower(c.raw_text) LIKE lower(:query)"
        + " ORDER BY c.document_id, c.order_index LIMIT :limit"
    )
    rows = session.execute(text(sql), params).mappings().all()
    return [
        SearchHit(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            program_id=row["program_id"],
            title=row["title"],
            heading_path=row["heading_path"],
            page_number=row["page_number"],
            snippet=_snippet(row["raw_text"], query),
            similarity=0.0,
            citation=None,
            mode="keyword",
        )
        for row in rows
    ]


def semantic_search(
    session: Session,
    query_vector: list[float],
    *,
    program_id: str | None = None,
    document_id: str | None = None,
    limit: int = 10,
) -> list[SearchHit]:
    """Nearest chunks by cosine distance.

    Only the current version of each document is searched: an older version
    holds the same text as the current one (or text that has since been
    replaced), so including it would return the same passage twice.
    """
    where, params = _base_where(program_id, document_id, embedded_only=True)
    params["vector"] = "[" + ",".join(repr(float(v)) for v in query_vector) + "]"
    params["limit"] = limit
    sql = (
        _SELECT.format(
            where=where,
            extra=", 1 - (c.embedding <=> CAST(:vector AS vector)) AS similarity",
        )
        + " ORDER BY c.embedding <=> CAST(:vector AS vector) LIMIT :limit"
    )
    rows = session.execute(text(sql), params).mappings().all()
    return [
        SearchHit(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            program_id=row["program_id"],
            title=row["title"],
            heading_path=row["heading_path"],
            page_number=row["page_number"],
            snippet=_snippet(row["raw_text"], ""),
            similarity=float(row["similarity"] or 0.0),
            citation=None,
            mode="semantic",
        )
        for row in rows
    ]


def attach_citations(session: Session, hits: list[SearchHit]) -> list[SearchHit]:
    """Fill in each hit's source anchor ("p. 3", heading path, ...)."""
    if not hits:
        return hits
    from db.models import DocumentChunk

    chunk_ids = [hit.chunk_id for hit in hits]
    rows = session.execute(
        select(DocumentChunk).where(DocumentChunk.chunk_id.in_(chunk_ids))
    ).scalars().all()
    by_id = {chunk.chunk_id: chunk for chunk in rows}
    for hit in hits:
        hit.citation = citation_for_chunk(by_id.get(hit.chunk_id))
    return hits
