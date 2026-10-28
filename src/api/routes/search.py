"""Search over the stored government text.

One endpoint, two ways of finding a chunk. Semantic search compares
embeddings and finds passages that mean the same thing as the query even
when they share no words with it. Keyword search matches the query's
words directly and always works.

The endpoint uses semantic search when it can and keyword search when it
cannot, and always reports which one it used. It never pretends.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user_id
from db.connection import get_db_engine, get_session
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    program_id: str | None = None
    document_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)
    # "auto" uses semantic search when it is available and keyword search
    # otherwise. "semantic" and "keyword" ask for one specifically; asking
    # for semantic when it is unavailable is an error, not a silent
    # downgrade.
    mode: str = "auto"


class SearchResponse(BaseModel):
    query: str
    mode: str
    semantic_available: bool
    embedded_chunks: int
    total: int
    results: list[dict]


@router.post("", response_model=SearchResponse)
def search(
    body: SearchRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """Search the stored government text."""
    from extraction.embeddings import EmbeddingError, get_embedding_client, embeddings_enabled
    from services.search_service import (
        attach_citations, embedded_chunk_count, keyword_search, semantic_search,
        vector_support_available,
    )

    mode = (body.mode or "auto").strip().lower()
    if mode not in ("auto", "semantic", "keyword"):
        raise HTTPException(status_code=400, detail="mode must be auto, semantic or keyword")

    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")

    engine = get_db_engine(request)
    with get_session(engine) as session:
        vectors_available = vector_support_available(session)
        embedded = embedded_chunk_count(session) if vectors_available else 0
        semantic_available = vectors_available and embedded > 0 and embeddings_enabled()

        if mode == "semantic" and not semantic_available:
            if not embeddings_enabled():
                detail = (
                    "Embeddings are off. Set EMBEDDINGS_ENABLED=true (and embed "
                    "the chunks) to use semantic search, or search with "
                    "mode=keyword."
                )
            elif not vectors_available:
                detail = (
                    "This database has no vector support. Semantic search needs "
                    "PostgreSQL with the pgvector extension."
                )
            else:
                detail = (
                    "No chunks have been embedded yet, so there is nothing to "
                    "compare against. Run the embedding backfill first."
                )
            raise HTTPException(status_code=409, detail=detail)

        used_mode = "keyword"
        hits = []
        if mode in ("auto", "semantic") and semantic_available:
            try:
                client = get_embedding_client()
                vector = client.embed_query(query)
                hits = semantic_search(
                    session, vector,
                    program_id=body.program_id,
                    document_id=body.document_id,
                    limit=body.limit,
                )
                used_mode = "semantic"
            except EmbeddingError as e:
                if mode == "semantic":
                    raise HTTPException(status_code=409, detail=str(e)) from e
                logger.warning("Semantic search unavailable, falling back to keyword",
                               extra={"error": str(e)})

        if used_mode == "keyword":
            hits = keyword_search(
                session, query,
                program_id=body.program_id,
                document_id=body.document_id,
                limit=body.limit,
            )

        attach_citations(session, hits)

        return {
            "query": query,
            "mode": used_mode,
            "semantic_available": semantic_available,
            "embedded_chunks": embedded,
            "total": len(hits),
            "results": [hit.to_dict() for hit in hits],
        }
