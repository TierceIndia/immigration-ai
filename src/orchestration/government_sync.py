"""Government sync orchestrator — end-to-end pipeline from source to DRAFT rules.

Pipeline:
    1. Load source + document metadata
    2. Detect change (hash comparison)
    3. If changed: extract requirements via LLM
    4. Validate + normalize requirements
    5. Compile approved requirements → DRAFT rules
    6. Return review queue summary

This module is deliberately on-demand. No scheduler — that comes later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from db.models import (
    GovernmentSource, GovernmentDocument, DocumentVersion,
    DocumentChunk, Requirement,
)
from phase3.requirement_to_rule import compile_approved_requirements
from phase3.rule_repository import EnhancedRuleRepo
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """Result of syncing a single government source."""
    source_id: str
    status: str  # "unchanged" | "changed" | "error" | "no_document"
    document_id: str | None = None
    document_version: int | None = None
    old_hash: str | None = None
    new_hash: str | None = None
    requirements_extracted: int = 0
    requirements_approved: int = 0
    rules_created: int = 0
    rules_drafted: int = 0
    errors: list[str] = field(default_factory=list)


def detect_change(session: Session, source_id: str) -> tuple[bool, str | None, str | None]:
    """Check if the government source has changed since last check.

    Returns: (changed, old_hash, new_hash)
    """
    source = session.get(GovernmentSource, source_id)
    if not source:
        raise ValueError(f"Source {source_id} not found")

    doc = (
        session.query(GovernmentDocument)
        .filter(GovernmentDocument.source_id == source_id)
        .order_by(GovernmentDocument.retrieved_at.desc())
        .first()
    )
    if not doc:
        return False, None, None

    old_hash = source.last_hash
    new_hash = doc.content_hash

    if old_hash is None:
        # First time — treat as changed
        return True, None, new_hash

    return old_hash != new_hash, old_hash, new_hash


def record_change(session: Session, source_id: str, new_hash: str) -> None:
    """Update the source's last_hash after a successful sync."""
    source = session.get(GovernmentSource, source_id)
    if source:
        source.last_hash = new_hash
        source.last_checked_at = datetime.now(timezone.utc)
        session.flush()


def get_source_documents(session: Session, source_id: str) -> list[dict]:
    """Get all document versions for a source, newest first."""
    docs = (
        session.query(GovernmentDocument)
        .filter(GovernmentDocument.source_id == source_id)
        .order_by(GovernmentDocument.retrieved_at.desc())
        .all()
    )
    return [
        {
            "document_id": d.document_id,
            "title": d.title,
            "content_hash": d.content_hash,
            "current_version": d.current_version,
            "retrieved_at": d.retrieved_at.isoformat() if d.retrieved_at else None,
        }
        for d in docs
    ]


def get_document_chunks(session: Session, document_id: str) -> list[dict]:
    """Get all chunks for a document."""
    chunks = (
        session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.order_index)
        .all()
    )
    return [
        {
            "chunk_id": c.chunk_id,
            "chunk_type": c.chunk_type,
            "heading_path": c.heading_path,
            "order_index": c.order_index,
            "raw_text": c.raw_text,
            "page_number": c.page_number,
        }
        for c in chunks
    ]


def sync_source(session: Session, source_id: str,
                dry_run: bool = False) -> SyncResult:
    """Run the full sync pipeline for one government source.

    Steps:
        1. Detect change
        2. If unchanged → return early
        3. Compile any approved requirements → DRAFT rules
        4. Record the change
    """
    result = SyncResult(source_id=source_id, status="error")

    try:
        # 1. Detect change
        changed, old_hash, new_hash = detect_change(session, source_id)
        result.old_hash = old_hash
        result.new_hash = new_hash

        if not changed:
            result.status = "unchanged"
            logger.info("Source unchanged", extra={"source_id": source_id})
            return result

        result.status = "changed"

        # 2. Get the latest document
        doc = (
            session.query(GovernmentDocument)
            .filter(GovernmentDocument.source_id == source_id)
            .order_by(GovernmentDocument.retrieved_at.desc())
            .first()
        )
        if not doc:
            result.status = "no_document"
            return result

        result.document_id = doc.document_id
        result.document_version = doc.current_version

        # 3. Count existing approved requirements
        approved_count = (
            session.query(Requirement)
            .filter(Requirement.program_id == doc.program_id)
            .filter(Requirement.status == "APPROVED")
            .count()
        )
        result.requirements_approved = approved_count

        # 4. Compile approved requirements → DRAFT rules
        if approved_count > 0 and not dry_run:
            compilation_results = compile_approved_requirements(
                session, program_id=doc.program_id, dry_run=dry_run
            )
            result.rules_created = sum(
                1 for r in compilation_results if r["status"] == "created"
            )
            result.rules_drafted = result.rules_created  # all start as DRAFT
            result.requirements_extracted = len(compilation_results)

        # 5. Record the change
        if not dry_run and new_hash:
            record_change(session, source_id, new_hash)

        logger.info("Source sync completed",
                     extra={"source_id": source_id, "status": result.status,
                            "rules_created": result.rules_created})

    except Exception as e:
        result.status = "error"
        result.errors.append(str(e))
        logger.error("Source sync failed",
                      extra={"source_id": source_id, "error": str(e)})

    return result


def sync_all_sources(session: Session, dry_run: bool = False) -> list[SyncResult]:
    """Sync all active government sources."""
    sources = (
        session.query(GovernmentSource)
        .filter(GovernmentSource.status == "ACTIVE")
        .all()
    )
    results = []
    for src in sources:
        result = sync_source(session, src.source_id, dry_run=dry_run)
        results.append(result)
    return results


def get_review_queue(session: Session, program_id: str | None = None) -> dict[str, Any]:
    """Get a summary of rules awaiting human review/activation."""
    rule_repo = EnhancedRuleRepo(session)
    drafts = rule_repo.list_drafts(program_id)
    validated = rule_repo.list_validated(program_id)
    approved = rule_repo.list_approved_pending_activation(program_id)
    active = rule_repo.list_active(program_id)

    return {
        "drafts": [
            {"rule_id": r.rule_id, "program_id": r.program_id,
             "field": r.field, "category": r.category,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in drafts
        ],
        "validated": [
            {"rule_id": r.rule_id, "program_id": r.program_id,
             "field": r.field, "category": r.category}
            for r in validated
        ],
        "approved_pending": [
            {"rule_id": r.rule_id, "program_id": r.program_id,
             "field": r.field, "category": r.category}
            for r in approved
        ],
        "active": [
            {"rule_id": r.rule_id, "program_id": r.program_id,
             "field": r.field, "category": r.category,
             "version": r.current_version}
            for r in active
        ],
        "counts": {
            "drafts": len(drafts),
            "validated": len(validated),
            "approved_pending": len(approved),
            "active": len(active),
        },
    }
