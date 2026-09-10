"""Admin document routes — view government documents, versions, chunks, and diffs.

All endpoints require admin role. Provides visibility into the raw document
data that feeds the requirement extraction pipeline.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.models import GovernmentDocument, DocumentVersion, DocumentChunk
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class DocumentResponse(BaseModel):
    document_id: str
    source_id: str
    country_id: str
    program_id: str
    visa_category: str
    document_type: str
    url: str
    title: str | None = None
    published_date: str | None = None
    effective_date: str | None = None
    content_hash: str
    current_version: int
    language: str
    status: str
    retrieved_at: str | None = None
    created_at: str | None = None


class DocumentVersionResponse(BaseModel):
    version_id: int
    document_id: str
    version_number: int
    content_hash: str
    change_type: str
    diff_summary: str | None = None
    raw_path: str
    extracted_path: str | None = None
    retrieved_at: str | None = None


class DocumentChunkResponse(BaseModel):
    chunk_id: int
    document_id: str
    version_id: int
    chunk_type: str
    heading_path: str | None = None
    order_index: int
    raw_text: str
    page_number: int | None = None


def _doc_to_response(obj: GovernmentDocument) -> DocumentResponse:
    return DocumentResponse(
        document_id=obj.document_id,
        source_id=obj.source_id,
        country_id=obj.country_id,
        program_id=obj.program_id,
        visa_category=obj.visa_category,
        document_type=obj.document_type,
        url=obj.url,
        title=obj.title,
        published_date=obj.published_date,
        effective_date=obj.effective_date,
        content_hash=obj.content_hash,
        current_version=obj.current_version,
        language=obj.language,
        status=obj.status,
        retrieved_at=obj.retrieved_at.isoformat() if obj.retrieved_at else None,
        created_at=obj.created_at.isoformat() if obj.created_at else None,
    )


def _version_to_response(obj: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        version_id=obj.version_id,
        document_id=obj.document_id,
        version_number=obj.version_number,
        content_hash=obj.content_hash,
        change_type=obj.change_type,
        diff_summary=obj.diff_summary,
        raw_path=obj.raw_path,
        extracted_path=obj.extracted_path,
        retrieved_at=obj.retrieved_at.isoformat() if obj.retrieved_at else None,
    )


def _chunk_to_response(obj: DocumentChunk) -> DocumentChunkResponse:
    return DocumentChunkResponse(
        chunk_id=obj.chunk_id,
        document_id=obj.document_id,
        version_id=obj.version_id,
        chunk_type=obj.chunk_type,
        heading_path=obj.heading_path,
        order_index=obj.order_index,
        raw_text=obj.raw_text,
        page_number=obj.page_number,
    )


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    request: Request,
    source_id: str | None = None,
    program_id: str | None = None,
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List government documents, optionally filtered."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        from sqlalchemy import select
        stmt = select(GovernmentDocument)
        if source_id:
            stmt = stmt.where(GovernmentDocument.source_id == source_id)
        if program_id:
            stmt = stmt.where(GovernmentDocument.program_id == program_id)
        if status:
            stmt = stmt.where(GovernmentDocument.status == status)
        stmt = stmt.order_by(GovernmentDocument.retrieved_at.desc())
        docs = list(session.execute(stmt).scalars())
        return [_doc_to_response(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get a single document by ID."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        doc = session.get(GovernmentDocument, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return _doc_to_response(doc)


@router.get("/{document_id}/versions", response_model=list[DocumentVersionResponse])
def list_document_versions(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """List all versions for a document, newest first."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        doc = session.get(GovernmentDocument, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        from sqlalchemy import select
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
        )
        versions = list(session.execute(stmt).scalars())
        return [_version_to_response(v) for v in versions]


@router.get("/{document_id}/chunks", response_model=list[DocumentChunkResponse])
def list_document_chunks(
    document_id: str,
    request: Request,
    version_id: int | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List chunks for a document, ordered by position."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        doc = session.get(GovernmentDocument, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        from sqlalchemy import select
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
        )
        if version_id is not None:
            stmt = stmt.where(DocumentChunk.version_id == version_id)
        stmt = stmt.order_by(DocumentChunk.order_index)
        chunks = list(session.execute(stmt).scalars())
        return [_chunk_to_response(c) for c in chunks]


@router.get("/{document_id}/diff")
def get_document_diff(
    document_id: str,
    request: Request,
    from_version: int | None = None,
    to_version: int | None = None,
    current_user: dict = Depends(get_current_user),
):
    """Get a human-readable diff between two document versions.

    If versions not specified, compares the two most recent versions.
    Returns old vs new chunk text side by side.
    """
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        doc = session.get(GovernmentDocument, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        from sqlalchemy import select
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        )
        versions = list(session.execute(stmt).scalars())
        if len(versions) < 2:
            return {"document_id": document_id, "diff": None,
                    "message": "Only one version exists — no diff available."}

        if from_version is not None and to_version is not None:
            old_ver = next((v for v in versions if v.version_number == from_version), None)
            new_ver = next((v for v in versions if v.version_number == to_version), None)
        else:
            old_ver, new_ver = versions[-2], versions[-1]

        if not old_ver or not new_ver:
            raise HTTPException(status_code=400, detail="Requested version not found")

        old_chunks = list(session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id,
                   DocumentChunk.version_id == old_ver.version_id)
            .order_by(DocumentChunk.order_index)
        ).scalars())
        new_chunks = list(session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id,
                   DocumentChunk.version_id == new_ver.version_id)
            .order_by(DocumentChunk.order_index)
        ).scalars())

        return {
            "document_id": document_id,
            "from_version": old_ver.version_number,
            "to_version": new_ver.version_number,
            "from_hash": old_ver.content_hash,
            "to_hash": new_ver.content_hash,
            "from_retrieved_at": old_ver.retrieved_at.isoformat() if old_ver.retrieved_at else None,
            "to_retrieved_at": new_ver.retrieved_at.isoformat() if new_ver.retrieved_at else None,
            "old_chunks": [_chunk_to_response(c).model_dump() for c in old_chunks],
            "new_chunks": [_chunk_to_response(c).model_dump() for c in new_chunks],
            "change_type": new_ver.change_type,
            "diff_summary": new_ver.diff_summary,
        }
