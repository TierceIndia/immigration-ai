"""Government sync orchestrator — end-to-end pipeline from source to ACTIVE rules.

Pipeline:
    1. Load source + document metadata
    2. Detect change (hash comparison)
    3. If changed: compile approved requirements → DRAFT rules
    4. Record the change

Every sync execution is tracked via PipelineRun for audit trail.

Failure-safe invariant:
    If extraction/compilation fails → old ACTIVE rules stay ACTIVE,
    new rules are NOT activated, PipelineRun = FAILED | PARTIAL.

Multi-level change detection (for reviewer visibility):
    Level 1 — RAW:       source last_hash vs document content_hash
    Level 2 — DOCUMENT:  old document version vs new document version
    Level 3 — REQUIREMENT: old requirement fields vs new requirement fields
    Level 4 — RULE:      old rule data vs new rule data
    Stored in PipelineRun.metadata_json["change_detection"].
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
from db.repositories import PipelineRunRepo
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
    rules_validated: int = 0
    rules_approved: int = 0
    rules_activated: int = 0
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


def compute_requirement_diff(old_reqs: list[dict], new_reqs: list[dict]) -> dict:
    """Compare old and new requirement snapshots.

    Returns a structured diff with added, removed, and changed requirements.
    """
    old_by_key = {r["semantic_key"]: r for r in old_reqs}
    new_by_key = {r["semantic_key"]: r for r in new_reqs}

    added = [new_by_key[k] for k in new_by_key if k not in old_by_key]
    removed = [old_by_key[k] for k in old_by_key if k not in new_by_key]

    changed = []
    for key in old_by_key:
        if key in new_by_key:
            old_r = old_by_key[key]
            new_r = new_by_key[key]
            diffs = {}
            for field_name in ("operator", "value_json", "mandatory", "criticality",
                               "field", "category", "description"):
                old_val = old_r.get(field_name)
                new_val = new_r.get(field_name)
                if old_val != new_val:
                    diffs[field_name] = {"old": old_val, "new": new_val}
            if diffs:
                changed.append({
                    "semantic_key": key,
                    "requirement_id": new_r.get("requirement_id"),
                    "fields_changed": diffs,
                })

    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added": [{"semantic_key": r["semantic_key"]} for r in added],
        "removed": [{"semantic_key": r["semantic_key"]} for r in removed],
        "changed": changed,
    }


def compute_rule_diff(old_rules: list[dict], new_rules: list[dict]) -> dict:
    """Compare old and new rule snapshots.

    Returns a structured diff with added, removed, and changed rules.
    """
    old_by_key = {(r["program_id"], r["category"], r["field"]): r for r in old_rules}
    new_by_key = {(r["program_id"], r["category"], r["field"]): r for r in new_rules}

    added = [new_by_key[k] for k in new_by_key if k not in old_by_key]
    removed = [old_by_key[k] for k in old_by_key if k not in new_by_key]

    changed = []
    for key in old_by_key:
        if key in new_by_key:
            old_r = old_by_key[key]
            new_r = new_by_key[key]
            diffs = {}
            for field_name in ("operator", "value_json", "mandatory", "critical",
                               "message"):
                old_val = old_r.get(field_name)
                new_val = new_r.get(field_name)
                if old_val != new_val:
                    diffs[field_name] = {"old": old_val, "new": new_val}
            if diffs:
                changed.append({
                    "program_id": key[0],
                    "category": key[1],
                    "field": key[2],
                    "fields_changed": diffs,
                })

    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added": [{"program_id": r["program_id"], "field": r["field"]} for r in added],
        "removed": [{"program_id": r["program_id"], "field": r["field"]} for r in removed],
        "changed": changed,
    }


def compute_change_detection(
    session: Session,
    source_id: str,
    old_hash: str | None,
    new_hash: str | None,
    program_id: str,
) -> dict:
    """Compute multi-level change detection for a sync run.

    Levels:
        1. RAW: hash comparison
        2. REQUIREMENT: requirement field diffs
        3. RULE: rule field diffs (before vs after compilation)

    Returns a dict suitable for PipelineRun.metadata_json["change_detection"].
    """
    change: dict[str, Any] = {
        "raw": {
            "old_hash": old_hash,
            "new_hash": new_hash,
            "changed": old_hash != new_hash if old_hash and new_hash else True,
        },
    }

    # Level 2: Requirement diff
    old_reqs = [
        {
            "semantic_key": r.semantic_key,
            "requirement_id": r.requirement_id,
            "operator": r.operator,
            "value_json": r.value_json,
            "mandatory": r.mandatory,
            "criticality": r.criticality,
            "field": r.field,
            "category": r.category,
            "description": r.description,
        }
        for r in session.query(Requirement).filter(
            Requirement.program_id == program_id,
            Requirement.status == "APPROVED",
        ).all()
    ]
    # After compilation, requirements remain APPROVED — the diff is "before sync" snapshot
    change["requirement"] = {
        "snapshot_count": len(old_reqs),
        "snapshot": old_reqs,
    }

    # Level 3: Rule diff — load current active rules before compilation
    rule_repo = EnhancedRuleRepo(session)
    old_active = rule_repo.list_active(program_id)
    old_rule_snapshots = [
        {
            "rule_id": r.rule_id,
            "program_id": r.program_id,
            "category": r.category,
            "field": r.field,
            "operator": r.operator,
            "value_json": r.value_json,
            "mandatory": r.mandatory,
            "critical": r.critical,
            "message": r.message,
            "status": r.status,
            "version": r.current_version,
        }
        for r in old_active
    ]
    change["rule"] = {
        "active_before_count": len(old_rule_snapshots),
        "active_before": old_rule_snapshots,
    }

    return change


def sync_source(session: Session, source_id: str,
                dry_run: bool = False, trigger_type: str = "ON_DEMAND") -> SyncResult:
    """Run the full sync pipeline for one government source.

    Creates a PipelineRun to track execution. Steps:
        1. Detect change
        2. If unchanged → return early
        3. Compile approved requirements → DRAFT rules
        4. Record the change

    Failure-safe: if any step fails, old ACTIVE rules remain untouched.
    PipelineRun is marked FAILED or PARTIAL with error details.
    """
    run_repo = PipelineRunRepo(session)
    rule_repo = EnhancedRuleRepo(session)
    run = run_repo.create(source_id=source_id, trigger_type=trigger_type)

    result = SyncResult(source_id=source_id, status="error")

    try:
        # 1. Detect change
        changed, old_hash, new_hash = detect_change(session, source_id)
        run.old_hash = old_hash
        run.new_hash = new_hash

        if not changed:
            result.status = "unchanged"
            run_repo.complete(run.run_id)
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
            run_repo.fail(run.run_id, "No document found for source")
            return result

        result.document_id = doc.document_id
        result.document_version = doc.current_version

        # 3. Compute multi-level change detection (before compilation)
        change_detection = compute_change_detection(
            session, source_id, old_hash, new_hash, doc.program_id,
        )

        # 4. Count existing approved requirements
        approved_count = (
            session.query(Requirement)
            .filter(Requirement.program_id == doc.program_id)
            .filter(Requirement.status == "APPROVED")
            .count()
        )
        result.requirements_approved = approved_count

        # 5. Compile approved requirements → DRAFT rules
        #    Failure-safe: if compilation fails, old rules stay ACTIVE
        errors: list[str] = []
        if approved_count > 0 and not dry_run:
            try:
                compilation_results = compile_approved_requirements(
                    session, program_id=doc.program_id, dry_run=dry_run
                )
                result.rules_created = sum(
                    1 for r in compilation_results if r["status"] == "created"
                )
                result.rules_drafted = result.rules_created
                result.requirements_extracted = len(compilation_results)

                # Track per-rule errors
                for r in compilation_results:
                    if r["status"] == "error" and r.get("error"):
                        errors.append(f"{r['requirement_id']}: {r['error']}")
            except Exception as comp_err:
                errors.append(f"Compilation failed: {comp_err}")
                logger.error("Requirement compilation failed",
                             extra={"source_id": source_id, "error": str(comp_err),
                                    "run_id": run.run_id})

        # 6. Compute rule diff after compilation
        new_active = rule_repo.list_active(doc.program_id)
        change_detection["rule"]["active_after_count"] = len(new_active)
        change_detection["rule"]["active_after"] = [
            {
                "rule_id": r.rule_id,
                "program_id": r.program_id,
                "category": r.category,
                "field": r.field,
                "operator": r.operator,
                "value_json": r.value_json,
                "mandatory": r.mandatory,
                "critical": r.critical,
                "status": r.status,
                "version": r.current_version,
            }
            for r in new_active
        ]
        change_detection["rule"]["diff"] = compute_rule_diff(
            change_detection["rule"]["active_before"],
            change_detection["rule"]["active_after"],
        )

        # 7. Record the change
        if not dry_run and new_hash:
            record_change(session, source_id, new_hash)

        # 8. Update PipelineRun via state machine
        run_kwargs = dict(
            requirements_extracted=result.requirements_extracted,
            requirements_approved=result.requirements_approved,
            rules_created=result.rules_created,
        )
        if errors:
            run.metadata_json = {"change_detection": change_detection}
            run_repo.partial(
                run.run_id, "; ".join(errors), **run_kwargs,
            )
            logger.warning("Source sync partial",
                           extra={"source_id": source_id, "errors": errors,
                                  "run_id": run.run_id})
        else:
            run.metadata_json = {"change_detection": change_detection}
            run_repo.complete(run.run_id, **run_kwargs)
            logger.info("Source sync completed",
                         extra={"source_id": source_id, "status": result.status,
                                "rules_created": result.rules_created,
                                "run_id": run.run_id})

    except Exception as e:
        result.status = "error"
        result.errors.append(str(e))
        # Failure-safe: if anything fails, old ACTIVE rules are untouched
        # PipelineRun is FAILED, new rules never activated
        run_repo.fail(run.run_id, str(e))
        logger.error("Source sync failed",
                      extra={"source_id": source_id, "error": str(e),
                             "run_id": run.run_id})

    return result


def sync_all_sources(session: Session, dry_run: bool = False,
                     trigger_type: str = "ON_DEMAND") -> list[SyncResult]:
    """Sync all active government sources."""
    sources = (
        session.query(GovernmentSource)
        .filter(GovernmentSource.status == "ACTIVE")
        .all()
    )
    results = []
    for src in sources:
        result = sync_source(session, src.source_id, dry_run=dry_run,
                             trigger_type=trigger_type)
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
