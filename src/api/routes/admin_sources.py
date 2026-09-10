"""Admin source management routes — create, update, list, disable government sources,
plus government source AUTO-DISCOVERY (onboarding/suggestion tool).

All mutations require admin role.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.models import (
    DiscoveredSource, DocumentChunk, DocumentVersion, GovernmentDocument, GovernmentSource,
)
from db.repositories import DiscoveredSourceRepo, SourceRepo
from extractor_html import extract_html
from extractor_pdf import extract_pdf
from hashing import sha256_bytes
from logging_config import get_logger
from source_discovery import discover_sources

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class SourceCreateRequest(BaseModel):
    source_id: str = Field(..., description="Unique source identifier (e.g. SRC-AU-189-MAIN)")
    country_id: str = Field(..., description="Country code (e.g. AU)")
    program_id: str = Field(..., description="Program ID (e.g. SKILLED_189)")
    visa_category: str = Field(..., description="Visa category (e.g. SKILLED)")
    authority: str = Field(..., description="Issuing authority")
    url: str = Field(..., description="Source URL")
    source_type: str = Field("HTML", description="Source type (HTML/PDF/API)")
    source_subtype: str | None = Field(None, description="Source subtype")
    priority: str = Field("PRIMARY", description="PRIMARY or SECONDARY")
    monitoring_method: str = Field("WEB", description="Monitoring method")
    crawl_frequency: str = Field("DAILY", description="Crawl frequency (DAILY/WEEKLY/MONTHLY)")
    notes: str | None = None


class SourceUpdateRequest(BaseModel):
    status: str | None = Field(None, description="ACTIVE or DISABLED")
    priority: str | None = Field(None, description="PRIMARY or SECONDARY")
    crawl_frequency: str | None = None
    notes: str | None = None
    url: str | None = None


class SourceResponse(BaseModel):
    source_id: str
    country_id: str
    program_id: str
    visa_category: str
    authority: str
    url: str
    source_type: str
    source_subtype: str | None = None
    priority: str
    monitoring_method: str
    crawl_frequency: str
    status: str
    last_checked_at: str | None = None
    last_successful_fetch_at: str | None = None
    last_hash: str | None = None
    notes: str | None = None
    fallback_source_id: str | None = None
    fetch_status: str | None = None
    http_status: int | None = None
    last_error: str | None = None
    consecutive_failures: int | None = None
    created_at: str | None = None


def _source_to_response(obj: GovernmentSource) -> SourceResponse:
    return SourceResponse(
        source_id=obj.source_id,
        country_id=obj.country_id,
        program_id=obj.program_id,
        visa_category=obj.visa_category,
        authority=obj.authority,
        url=obj.url,
        source_type=obj.source_type,
        source_subtype=obj.source_subtype,
        priority=obj.priority,
        monitoring_method=obj.monitoring_method,
        crawl_frequency=obj.crawl_frequency,
        status=obj.status,
        last_checked_at=obj.last_checked_at.isoformat() if obj.last_checked_at else None,
        last_successful_fetch_at=obj.last_successful_fetch_at.isoformat() if obj.last_successful_fetch_at else None,
        last_hash=obj.last_hash,
        notes=obj.notes,
        fallback_source_id=obj.fallback_source_id,
        fetch_status=obj.fetch_status,
        http_status=obj.http_status,
        last_error=obj.last_error,
        consecutive_failures=obj.consecutive_failures,
        created_at=obj.created_at.isoformat() if obj.created_at else None,
    )


# ===========================================================================
# AUTO-DISCOVERY endpoints. IMPORTANT: these MUST be registered before any
# `/{source_id}`-style route below, otherwise `GET /discovered` would be captured
# by `GET /{source_id}` and return 404. FastAPI matches routes in registration
# order.
# ===========================================================================

class DiscoveryRequest(BaseModel):
    seed_url: str = Field(..., description="Seed URL to discover same-domain sub-pages from")
    max_depth: int = Field(2, ge=0, le=5, description="Max crawl depth (default 2)")
    suggested_category: str | None = Field(
        None, description="Optional category bias: SKILLED/WORK/STUDENT/VISITOR"
    )


class DiscoveredSourceResponse(BaseModel):
    discovered_source_id: str
    seed_url: str
    candidate_url: str
    page_title: str | None = None
    matched_keywords: list[str] = []
    confidence_score: int
    suggested_visa_category: str | None = None
    suggested_country_id: str
    status: str
    discovered_at: str | None = None
    discovery_run_id: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    rejection_reason: str | None = None


def _discovered_to_response(obj: DiscoveredSource) -> DiscoveredSourceResponse:
    return DiscoveredSourceResponse(
        discovered_source_id=obj.discovered_source_id,
        seed_url=obj.seed_url,
        candidate_url=obj.candidate_url,
        page_title=obj.page_title,
        matched_keywords=obj.matched_keywords or [],
        confidence_score=obj.confidence_score,
        suggested_visa_category=obj.suggested_visa_category,
        suggested_country_id=obj.suggested_country_id,
        status=obj.status,
        discovered_at=obj.discovered_at.isoformat() if obj.discovered_at else None,
        discovery_run_id=obj.discovery_run_id,
        reviewed_by=obj.reviewed_by,
        reviewed_at=obj.reviewed_at.isoformat() if obj.reviewed_at else None,
        rejection_reason=obj.rejection_reason,
    )


class DiscoveryApproveRequest(BaseModel):
    """Fields an admin must supply to promote a candidate into a real source. The
    candidate_url (and country) come from the candidate; URL cannot be changed here."""
    source_id: str = Field(..., description="New government_sources source_id, e.g. SRC-AU-WORK-482")
    program_id: str = Field(..., description="Program ID, e.g. WORK_482")
    visa_category: str = Field(..., description="SKILLED/WORK/STUDENT/VISITOR")
    authority: str = Field(..., description="Issuing authority")
    priority: str = Field("PRIMARY", description="PRIMARY or SECONDARY")
    source_type: str = Field("HTML", description="HTML/PDF/API")
    source_subtype: str | None = None
    monitoring_method: str = Field("WEB")
    crawl_frequency: str = Field("DAILY")
    notes: str | None = None


class DiscoveryRejectRequest(BaseModel):
    reason: str = Field(..., description="Why the candidate was rejected")


class ManualCaptureRequest(BaseModel):
    """Raw content captured manually (e.g. from a browser the admin used by hand
    after automatic retrieval was ACCESS_BLOCKED). Feeds the pipeline exactly like
    an automated fetch's output, tagged with retrieval_method MANUAL_CAPTURE."""
    raw_html: str = Field(..., description="Raw HTML captured manually")
    content_type: str | None = Field("text/html", description="Content type of raw_html")
    source_type: str | None = Field(None, description="Override source type (HTML/PDF)")


@router.post("/discover")
def discover(
    body: DiscoveryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Run source auto-discovery from a seed URL and return newly created candidates
    (status PENDING_REVIEW). Never activates anything — approval is a separate action."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        _, report = discover_sources(
            seed_url=body.seed_url,
            max_depth=body.max_depth,
            suggested_category=body.suggested_category,
            session=session,
        )
        if report.get("robots_blocked_seed"):
            raise HTTPException(status_code=400, detail=report["errors"][0])
        repo = DiscoveredSourceRepo(session)
        created = repo.list_by_status("PENDING_REVIEW")
        return [_discovered_to_response(c) for c in created]


@router.get("/discovered", response_model=list[DiscoveredSourceResponse])
def list_discovered(
    request: Request,
    status: str | None = None,
    suggested_visa_category: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List discovered candidates, filterable by status / suggested category."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = DiscoveredSourceRepo(session)
        rows = repo.list_by_status(status, suggested_visa_category)
        return [_discovered_to_response(r) for r in rows]


@router.post("/discovered/{discovered_source_id}/approve", response_model=SourceResponse)
def approve_discovered(
    discovered_source_id: str,
    body: DiscoveryApproveRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Promote a candidate into a real government_sources row (admin action). The
    candidate_url is fixed to the discovered page; the admin fills the remaining fields."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = DiscoveredSourceRepo(session)
        cand = repo.get(discovered_source_id)
        if not cand:
            raise HTTPException(status_code=404, detail="Discovered source not found")
        if cand.status != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail=f"Candidate already {cand.status}")
        source_repo = SourceRepo(session)
        if source_repo.get(body.source_id):
            raise HTTPException(status_code=409, detail=f"Source {body.source_id} already exists")
        source_fields = {
            "source_id": body.source_id,
            "country_id": cand.suggested_country_id or "AU",
            "program_id": body.program_id,
            "visa_category": body.visa_category,
            "authority": body.authority,
            "url": cand.candidate_url,
            "source_type": body.source_type,
            "source_subtype": body.source_subtype,
            "priority": body.priority,
            "monitoring_method": body.monitoring_method,
            "crawl_frequency": body.crawl_frequency,
            "status": "ACTIVE",
            "notes": body.notes,
        }
        cand, source = repo.approve(
            discovered_source_id,
            reviewed_by=current_user["user_id"],
            source_fields=source_fields,
        )
        session.commit()
        return _source_to_response(source)


@router.post("/discovered/{discovered_source_id}/reject")
def reject_discovered(
    discovered_source_id: str,
    body: DiscoveryRejectRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Reject a discovered candidate with a reason."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = DiscoveredSourceRepo(session)
        cand = repo.get(discovered_source_id)
        if not cand:
            raise HTTPException(status_code=404, detail="Discovered source not found")
        if cand.status != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail=f"Candidate already {cand.status}")
        obj = repo.reject(discovered_source_id, reviewed_by=current_user["user_id"],
                          rejection_reason=body.reason)
        session.commit()
        return _discovered_to_response(obj)


# ===========================================================================
# CRUD on real government_sources.
# ===========================================================================

@router.get("", response_model=list[SourceResponse])
def list_sources(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """List all government sources."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        from sqlalchemy import select
        stmt = select(GovernmentSource)
        sources = list(session.execute(stmt).scalars())
        return [_source_to_response(s) for s in sources]


@router.get("/{source_id}", response_model=SourceResponse)
def get_source(
    source_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get a single government source by ID."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = SourceRepo(session)
        obj = repo.get(source_id)
        if not obj:
            raise HTTPException(status_code=404, detail="Source not found")
        return _source_to_response(obj)


@router.post("/{source_id}/manual-capture", response_model=SourceResponse)
def manual_capture(
    source_id: str,
    body: ManualCaptureRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Resolve a source stuck in NEEDS_MANUAL_CAPTURE by ingesting raw content an
    admin captured by hand. Computes the content hash exactly like the automated
    path (hashing.sha256_bytes), creates a document_versions row tagged with
    retrieval_method=MANUAL_CAPTURE, and resets the source to ACTIVE so scheduled
    runs resume normally. Admin-gated."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = SourceRepo(session)
        obj = repo.get(source_id)
        if not obj:
            raise HTTPException(status_code=404, detail="Source not found")

        raw_bytes = body.raw_html.encode("utf-8")
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="raw_html must not be empty")

        source_type = (body.source_type or obj.source_type).upper()
        content_hash = sha256_bytes(raw_bytes)
        now = datetime.now(timezone.utc)
        document_id = f"DOC-{obj.source_id.replace('SRC-', '')}"

        doc = session.get(GovernmentDocument, document_id)

        # Idempotency: re-capturing content we already hold is UNCHANGED — it must never open
        # a second document_versions row for the same (document_id, content_hash).
        existing_snapshot = None
        if doc is not None:
            existing_snapshot = (
                session.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.content_hash == content_hash,
                )
                .order_by(DocumentVersion.version_id.desc())
                .first()
            )

        is_new_content = doc is None or existing_snapshot is None

        if doc is None:
            doc = GovernmentDocument(
                document_id=document_id,
                source_id=obj.source_id,
                country_id=obj.country_id,
                program_id=obj.program_id,
                visa_category=obj.visa_category,
                document_type=obj.source_subtype or "GENERAL",
                url=obj.url,
                retrieved_at=now,
                content_hash=content_hash,
                current_version=1,
                language="en",
                raw_path=f"manual:{document_id}",
                status="CURRENT",
            )
            session.add(doc)
        elif existing_snapshot is None:
            doc.content_hash = content_hash
            doc.retrieved_at = now
            doc.current_version = (doc.current_version or 0) + 1
            doc.status = "CURRENT"

        version_number = (
            existing_snapshot.version_number if existing_snapshot is not None
            else (doc.current_version or 1)
        )

        # Reset the source so scheduled runs resume. Clear failure diagnostics.
        for field, value in (
            ("status", "ACTIVE"),
            ("fetch_status", "OK"),
            ("http_status", 200),
            ("last_error", None),
            ("consecutive_failures", 0),
            ("last_hash", content_hash),
            ("last_checked_at", now),
            ("last_successful_fetch_at", now),
        ):
            setattr(obj, field, value)

        if existing_snapshot is None:
            dv = DocumentVersion(
                document_id=document_id,
                version_number=version_number,
                content_hash=content_hash,
                retrieved_at=now,
                raw_path=f"manual:{document_id}",
                change_type="MANUAL_CAPTURE",
                # Provenance tag on the version (no schema change — this column is Text)
                diff_summary=f"retrieval_method=MANUAL_CAPTURE; captured_by={current_user.get('user_id')}; source_status_before={obj.status}",
            )
            session.add(dv)

            # Chunk the captured content and run Phase-2 requirement extraction so the manual
            # capture feeds the review queue exactly like an automated fetch would.
            session.flush()
            try:
                if source_type == "PDF":
                    parsed = extract_pdf(raw_bytes)
                else:
                    parsed = extract_html(raw_bytes, obj.url)
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not parse captured content: {exc}",
                )
            if doc is not None and doc.title is None and parsed.title:
                doc.title = parsed.title
            for c in parsed.chunks:
                session.add(DocumentChunk(
                    document_id=document_id,
                    version_id=dv.version_id,
                    chunk_type=c.chunk_type,
                    heading_path=c.heading_path,
                    order_index=c.order_index,
                    raw_text=c.raw_text,
                    page_number=getattr(c, "page_number", None),
                ))
            session.flush()
            if parsed.chunks:
                try:
                    from utils.fetch_document import run_requirement_extraction
                    extraction_report = run_requirement_extraction(
                        session, obj, document_id, version_number,
                    )
                    logger.info(
                        "Manual-capture extraction complete",
                        extra={
                            "source_id": source_id,
                            "requirements_extracted": extraction_report.get(
                                "requirements_extracted", 0
                            ),
                        },
                    )
                except Exception as exc:
                    # Content is already stored; extraction failure must not reject the capture.
                    logger.warning(
                        "Manual-capture extraction failed; content stored",
                        extra={"source_id": source_id, "error": str(exc)},
                    )

        session.commit()
        return _source_to_response(obj)


@router.post("", response_model=SourceResponse, status_code=201)
def create_source(
    body: SourceCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Create a new government source."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = SourceRepo(session)
        existing = repo.get(body.source_id)
        if existing:
            raise HTTPException(status_code=409, detail=f"Source {body.source_id} already exists")
        obj = repo.create(
            source_id=body.source_id,
            country_id=body.country_id,
            program_id=body.program_id,
            visa_category=body.visa_category,
            authority=body.authority,
            url=body.url,
            source_type=body.source_type,
            source_subtype=body.source_subtype,
            priority=body.priority,
            monitoring_method=body.monitoring_method,
            crawl_frequency=body.crawl_frequency,
            status="ACTIVE",
            notes=body.notes,
        )
        session.commit()
        return _source_to_response(obj)


@router.patch("/{source_id}", response_model=SourceResponse)
def update_source(
    source_id: str,
    body: SourceUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Update a government source (status, priority, crawl_frequency, notes, url)."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = SourceRepo(session)
        obj = repo.get(source_id)
        if not obj:
            raise HTTPException(status_code=404, detail="Source not found")
        update_fields = body.model_dump(exclude_unset=True)
        for k, v in update_fields.items():
            setattr(obj, k, v)
        session.commit()
        return _source_to_response(obj)
