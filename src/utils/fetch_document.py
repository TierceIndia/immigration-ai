"""Fetch → store → extract for the ORM sync path.

This is the "real network" path that bootstraps a government source with no stored document:

    fetch (plain HTTP) ─┬─ OK ─────────────────────► extract_html / extract_pdf ──► store
                        │                               document / version / chunks
                        ├─ ACCESS_BLOCKED ─────────┐
                        │   / REQUIRES_BROWSER      ├─► browser escalation (genuine session)
                        └─ other failure ──────────┴─► FetchLog + source diagnostics

After a successful fetch + store it runs the Phase-2 requirement extraction contract
(``run_requirement_extraction``) with the real extraction model.  The model is resolved
via ``EXTRACTION_MODEL`` (defaulting to ``gpt-4o-mini``); the matching provider API key
(OPENAI_API_KEY or GEMINI_API_KEY) must be set — there is no silent fallback to a mock
or to any other provider.

Everything here is ORM-driven on the session's transaction; Phase-2 persistence runs through
``db.conn.SqliteConn`` (a sqlite3-shaped shim over the same session) so legacy module code is
reused unchanged.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dotenv
import fetcher

# In TESTS, dotenv loading is suppressed so a developer's .env cannot leak
# keys into the hermetic suite (confest strips them at module load).
if not os.environ.get("TESTING"):
    dotenv.load_dotenv()
from db.conn import SqliteConn
from db.current_session import current_session
from db.models import (
    DocumentChunk,
    DocumentVersion,
    ExtractionRun,
    FetchLog,
    GovernmentDocument,
    GovernmentSource,
)
from extractor_html import extract_html as _extract_html
from extractor_pdf import extract_pdf as _extract_pdf
from hashing import sha256_bytes
from logging_config import get_logger
from sqlalchemy.orm import Session

import browser_fetcher

logger = get_logger(__name__)

_BROWSER_ESCALATION_STATUSES = {
    fetcher.FetchStatus.ACCESS_BLOCKED,
    fetcher.FetchStatus.REQUIRES_BROWSER,
}


def _extraction_model() -> str:
    """Resolve the extraction model.

    Honors EXTRACTION_MODEL from the environment (e.g. ``gpt-4o-mini`` or
    ``gemini-flash-lite-latest``) and falls back to ``gpt-4o-mini`` when unset.  A
    missing API key for the resolved provider will fail fast when the client is
    constructed — there is no silent fallback to a mock or to any other provider.
    """
    from llm_client import DEFAULT_OPENAI_MODEL
    return os.getenv("EXTRACTION_MODEL", DEFAULT_OPENAI_MODEL)

# Phase-2 modules rely on flat imports against src/phase2.
_PHASE2_DIR = Path(__file__).resolve().parent.parent / "phase2"
if str(_PHASE2_DIR) not in sys.path:
    sys.path.insert(0, str(_PHASE2_DIR))
from llm_client import PROMPT_VERSION, get_client  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402
from requirement_repository import (  # noqa: E402
    complete_extraction_run,
    new_run_id,
    persist_requirement,
)


@dataclass
class FetchStoreOutcome:
    source_id: str
    status: str  # "OK" | "UNCHANGED" | a FetchStatus value when fetch failed
    document_id: str | None = None
    document_version: int | None = None
    content_hash: str | None = None
    requirements_extracted: int = 0
    error: str | None = None
    error_type: str | None = None  # machine-readable failure class
    demoted: str | None = None  # GovernmentSource.status set when the source was demoted
    # True when the fetch succeeded (real 200 OK, real content_hash) and the
    # content genuinely changed (INITIAL/CHANGED), but extraction produced zero
    # document chunks — e.g. a JS-rendered SPA shell with no static content.
    # Must never be indistinguishable from a real "nothing changed" success;
    # the caller (government_sync.sync_source) turns this into a visible
    # PARTIAL PipelineRun warning rather than a silent COMPLETED.
    empty_content: bool = False


def _resolve_session(session_or_engine) -> Session:
    if isinstance(session_or_engine, Session):
        return session_or_engine
    return current_session(session_or_engine)


def _load_chunk_dicts(
    session: Session, document_id: str, version_id: int | None = None
) -> list[dict[str, Any]]:
    """Load a document's chunks for extraction.

    ``version_id`` scopes the load to exactly the document version being extracted.
    Scoping matters on versioned documents: evidence must link a chunk to the
    version that chunk actually belongs to (``fk_evidence_chunk_lineage``), so
    re-extraction after a content change must only ever pair new-version chunks
    with the new version.
    """
    q = session.query(DocumentChunk).filter(DocumentChunk.document_id == document_id)
    if version_id is not None:
        q = q.filter(DocumentChunk.version_id == version_id)
    rows = q.order_by(DocumentChunk.order_index).all()
    return [
        {
            "chunk_id": c.chunk_id,
            "chunk_type": c.chunk_type,
            "heading_path": c.heading_path,
            "order_index": c.order_index,
            "raw_text": c.raw_text,
            "page_number": c.page_number,
        }
        for c in rows
    ]


def _record_fetch_failure(
    session: Session, source: GovernmentSource, fetch_result, now: datetime
) -> FetchStoreOutcome:
    demoted: str | None = None
    if fetch_result.status in _BROWSER_ESCALATION_STATUSES:
        source.status = "NEEDS_MANUAL_CAPTURE"
        demoted = source.status
    source.last_checked_at = now
    source.fetch_status = fetch_result.status.value
    source.http_status = fetch_result.http_status
    source.last_error = fetch_result.error
    source.last_error_type = fetch_result.error_type
    source.consecutive_failures = (source.consecutive_failures or 0) + 1

    session.add(
        FetchLog(
            source_id=source.source_id,
            fetched_at=now,
            http_status=fetch_result.http_status,
            status=fetch_result.status.value,
            content_hash=None,
            changed=0,
            duration_ms=fetch_result.duration_ms,
            error=fetch_result.error,
            error_type=fetch_result.error_type,
        )
    )
    session.flush()
    logger.warning(
        "Source fetch failed",
        extra={
            "source_id": source.source_id,
            "fetch_status": fetch_result.status.value,
            "error_type": fetch_result.error_type,
            "http_status": fetch_result.http_status,
            "error": fetch_result.error,
        },
    )
    return FetchStoreOutcome(
        source_id=source.source_id,
        status=fetch_result.status.value,
        error=fetch_result.error,
        error_type=fetch_result.error_type,
        demoted=demoted,
    )


def run_requirement_extraction(
    session_or_engine, source: GovernmentSource, document_id: str, version_number: int
) -> dict[str, Any]:
    """Run the Phase-2 requirement-extraction contract for a freshly stored document.

    Creates one ``extraction_runs`` row for the configured model (via
    ``EXTRACTION_MODEL``, defaulting to ``gpt-4o-mini``), executes the requirement
    contract over the document's chunks, and persists results through the legacy
    Phase-2 repository.  A missing API key for the resolved provider (OPENAI_API_KEY
    or GEMINI_API_KEY) will cause a hard failure; there is no silent fallback to a
    mock or to any other provider.
    Returns the counters dict (same shape as ``run_extraction.py``).
    """
    session = _resolve_session(session_or_engine)
    session.flush()
    doc = session.get(GovernmentDocument, document_id)
    if doc is None:
        return {"document_id": document_id, "status": "DOCUMENT_NOT_FOUND"}

    dv = (
        session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
        .order_by(DocumentVersion.version_id.desc())
        .first()
    )
    if dv is None:
        return {"document_id": document_id, "status": "NO_VERSION"}

    chunks = _load_chunk_dicts(session, document_id, version_id=dv.version_id)
    if not chunks:
        return {"document_id": document_id, "status": "NO_CHUNKS"}

    conn = SqliteConn(session)

    # Record the extraction run via the ORM so counters defaults apply on every engine; the
    # legacy phase2 repository is then reused unchanged for requirement persistence. The model
    # is configurable via EXTRACTION_MODEL (defaults to the deterministic offline mock).
    dv = (
        session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
        .order_by(DocumentVersion.version_id.desc())
        .first()
    )
    if dv is None:
        return {"document_id": document_id, "status": "NO_VERSION"}

    model_name = _extraction_model()
    run = ExtractionRun(
        run_id=new_run_id(),
        document_id=document_id,
        document_version=version_number,
        document_version_id=dv.version_id,
        model=model_name,
        prompt_version=PROMPT_VERSION,
        status="RUNNING",
        dry_run=int(False),
    )
    session.add(run)
    session.flush()
    run_id = run.run_id
    conn.commit()
    source_ctx = {
        "source_id": source.source_id,
        "authority": source.authority,
        "program_id": doc.program_id,
        "visa_category": source.visa_category,
    }
    document_ctx = {
        "document_id": document_id,
        "version": version_number,
        "title": doc.title,
    }

    try:
        client = get_client(model_name, visa_category=source.visa_category)
        outcomes = extract_from_document(
            source=source_ctx,
            document=document_ctx,
            all_chunks=chunks,
            llm_client=client,
            context_window=1,
        )
    except Exception as exc:
        try:
            complete_extraction_run(conn, run_id, {}, status="FAILED", error=str(exc))
        except Exception:
            pass
        raise

    counters = {
        "chunks_seen": len(chunks),
        "candidate_chunks": len(outcomes),
        "llm_calls": len(outcomes),
        "requirements_extracted": 0,
        "validated": 0,
        "needs_review": 0,
        "rejected": 0,
        "evidence_failures": 0,
        "schema_failures": 0,
    }
    persisted: list[dict[str, Any]] = []

    for outcome in outcomes:
        if getattr(outcome, "schema_errors", None):
            counters["schema_failures"] += 1
            continue
        for req in outcome.accepted_requirements:
            counters["requirements_extracted"] += 1
            counters["validated"] += 1
            result = persist_requirement(
                conn,
                program_id=doc.program_id,
                country_id=doc.country_id,
                requirement=req,
                document_id=document_id,
                document_version=version_number,
                extraction_run_id=run_id,
                dry_run=False,
            )
            persisted.append(result)
        for item in outcome.needs_review_requirements:
            counters["requirements_extracted"] += 1
            counters["needs_review"] += 1
            req = dict(item["requirement"])
            result = persist_requirement(
                conn,
                program_id=doc.program_id,
                country_id=doc.country_id,
                requirement=req,
                document_id=document_id,
                document_version=version_number,
                extraction_run_id=run_id,
                dry_run=False,
            )
            if result.get("requirement_id") and result["action"] == "CREATED":
                conn.execute(
                    "UPDATE requirements SET status = 'NEEDS_REVIEW' WHERE requirement_id = ?",
                    (result["requirement_id"],),
                )
                conn.commit()
            persisted.append({**result, "reasons": item["reasons"]})
        for item in outcome.rejected_requirements:
            counters["rejected"] += 1
            if any("evidence" in r.lower() or "fabricat" in r.lower() for r in item["reasons"]):
                counters["evidence_failures"] += 1

    complete_extraction_run(conn, run_id, counters, status="COMPLETED")
    return {
        "run_id": run_id,
        "document_id": document_id,
        "document_version": version_number,
        "model": model_name,
        "status": "COMPLETED",
        **counters,
        "persisted": persisted,
    }


def fetch_document_and_store(session_or_engine, source: GovernmentSource) -> FetchStoreOutcome:
    """Bootstrap a source: fetch (with browser escalation) → store document/version/chunks →
    run requirement extraction. Returns a ``FetchStoreOutcome`` describing what happened.

    On fetch/parse failure the source diagnostics are updated and a ``FetchLog`` row records
    the outcome; blocked sources are demoted to ``NEEDS_MANUAL_CAPTURE``.
    """
    session = _resolve_session(session_or_engine)
    now = datetime.now(timezone.utc)
    source_type = (source.source_type or "HTML").upper()

    fetch_result = fetcher.fetch(source.url, source_type=source_type)
    if fetch_result.status in _BROWSER_ESCALATION_STATUSES:
        logger.info(
            "Plain fetch blocked; escalating to genuine browser session",
            extra={"source_id": source.source_id, "status": fetch_result.status.value},
        )
        fetch_result = browser_fetcher.fetch_with_browser(
            source.url, source_type=source_type
        )
    if not fetch_result.ok:
        return _record_fetch_failure(session, source, fetch_result, now)

    raw_bytes = fetch_result.raw_bytes
    content_hash = sha256_bytes(raw_bytes)

    needs_ocr = False
    if source_type == "PDF":
        parsed = _extract_pdf(raw_bytes)
        chunk_rows = parsed.chunks
        needs_ocr = bool(getattr(parsed, "needs_ocr", False))
        title = parsed.title or source.url
    else:
        parsed = _extract_html(raw_bytes, source.url)
        chunk_rows = parsed.chunks
        title = parsed.title or source.url

    document_id = f"DOC-{source.source_id.replace('SRC-', '')}"
    existing = session.get(GovernmentDocument, document_id)

    if existing is None:
        change_type = "INITIAL"
        version_number = 1
    elif existing.content_hash == content_hash:
        change_type = "UNCHANGED"
        version_number = existing.current_version or 1
    else:
        change_type = "CHANGED"
        version_number = (existing.current_version or 0) + 1

    doc = existing
    if doc is None:
        doc = GovernmentDocument(
            document_id=document_id,
            source_id=source.source_id,
            country_id=source.country_id,
            program_id=source.program_id,
            visa_category=source.visa_category,
            document_type=source.source_subtype or "GENERAL",
            url=fetch_result.url,
            title=title,
            retrieved_at=now,
            content_hash=content_hash,
            current_version=version_number,
            language="en",
            raw_path=f"fetch:{document_id}:v{version_number}",
            status="NEEDS_OCR" if needs_ocr else "CURRENT",
        )
        session.add(doc)
    else:
        doc.content_hash = content_hash
        doc.retrieved_at = now
        doc.current_version = version_number
        doc.url = fetch_result.url
        doc.status = "NEEDS_OCR" if needs_ocr else "CURRENT"
        if doc.title is None:
            doc.title = title

    source.last_hash = content_hash
    source.last_checked_at = now
    source.last_successful_fetch_at = now
    source.status = "ACTIVE"
    source.fetch_status = "OK"
    source.http_status = fetch_result.http_status
    source.last_error = None
    source.last_error_type = None
    source.consecutive_failures = 0

    if change_type != "UNCHANGED":
        session.flush()  # ensure the document row is visible before the version FK is checked
        dv = DocumentVersion(
            document_id=document_id,
            version_number=version_number,
            content_hash=content_hash,
            retrieved_at=now,
            raw_path=f"fetch:{document_id}:v{version_number}",
            change_type=change_type,
        )
        session.add(dv)
        session.flush()
        for c in chunk_rows:
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    version_id=dv.version_id,
                    chunk_type=c.chunk_type,
                    heading_path=c.heading_path,
                    order_index=c.order_index,
                    raw_text=c.raw_text,
                    page_number=getattr(c, "page_number", None),
                )
            )

    session.add(
        FetchLog(
            source_id=source.source_id,
            fetched_at=now,
            http_status=fetch_result.http_status,
            status=fetcher.FetchStatus.OK.value,
            content_hash=content_hash,
            changed=1 if change_type != "UNCHANGED" else 0,
            duration_ms=fetch_result.duration_ms,
            error=None,
        )
    )
    session.flush()

    requirements_extracted = 0
    if change_type != "UNCHANGED" and chunk_rows:
        report = run_requirement_extraction(session, source, document_id, version_number)
        requirements_extracted = report.get("requirements_extracted", 0)
        logger.info(
            "Fetch + extraction complete",
            extra={
                "source_id": source.source_id,
                "document_id": document_id,
                "version": version_number,
                "requirements_extracted": requirements_extracted,
                "run_id": report.get("run_id"),
            },
        )

    return FetchStoreOutcome(
        source_id=source.source_id,
        status="OK" if change_type != "UNCHANGED" else "UNCHANGED",
        document_id=document_id,
        document_version=version_number,
        content_hash=content_hash,
        requirements_extracted=requirements_extracted,
        empty_content=(change_type != "UNCHANGED" and not chunk_rows),
    )