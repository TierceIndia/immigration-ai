"""Admin knowledge-state routes — hierarchical view of Australia's immigration knowledge.

GET /api/v1/admin/knowledge-state  → full tree:
  Australia → {SKILLED,WORK,STUDENT,VISITOR} → Program/Subclass
    → Sources (with scrape status) → Requirements → Rules (by status)

All endpoints require admin role. This is a read-side aggregation over existing
data — no new write path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.models import (
    Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    Requirement, Rule,
)
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("")
def get_knowledge_state(
    request: Request,
    country_id: str = "AU",
    current_user: dict = Depends(get_current_user),
):
    """Hierarchical knowledge state for a country.

    Returns:
        country → visa_categories → programs → sources + requirement/rule counts
    """
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        country = session.get(Country, country_id)
        if not country:
            raise HTTPException(status_code=404, detail=f"Country {country_id} not found")

        programs = list(
            session.query(ImmigrationProgram)
            .filter(ImmigrationProgram.country_id == country_id)
            .all()
        )

        visa_categories: dict[str, list[dict]] = {}
        for prog in programs:
            cat = prog.visa_category
            if cat not in visa_categories:
                visa_categories[cat] = []

            # Sources for this program
            sources = list(
                session.query(GovernmentSource)
                .filter(GovernmentSource.program_id == prog.program_id)
                .all()
            )

            source_list = []
            for src in sources:
                latest_doc = (
                    session.query(GovernmentDocument)
                    .filter(GovernmentDocument.source_id == src.source_id)
                    .order_by(GovernmentDocument.retrieved_at.desc())
                    .first()
                )
                source_list.append({
                    "source_id": src.source_id,
                    "authority": src.authority,
                    "url": src.url,
                    "source_type": src.source_type,
                    "source_subtype": src.source_subtype,
                    "priority": src.priority,
                    "crawl_frequency": src.crawl_frequency,
                    "status": src.status,
                    "last_checked_at": src.last_checked_at.isoformat() if src.last_checked_at else None,
                    "last_successful_fetch_at": src.last_successful_fetch_at.isoformat() if src.last_successful_fetch_at else None,
                    "last_hash": src.last_hash,
                    "latest_document": {
                        "document_id": latest_doc.document_id,
                        "title": latest_doc.title,
                        "content_hash": latest_doc.content_hash,
                        "current_version": latest_doc.current_version,
                        "retrieved_at": latest_doc.retrieved_at.isoformat() if latest_doc.retrieved_at else None,
                    } if latest_doc else None,
                })

            # Aggregate requirement counts by status
            req_rows = (
                session.query(Requirement.status, func.count())
                .filter(Requirement.program_id == prog.program_id)
                .group_by(Requirement.status)
                .all()
            )
            req_by_status = {row[0]: row[1] for row in req_rows}

            # Aggregate rule counts by status
            rule_rows = (
                session.query(Rule.status, func.count())
                .filter(Rule.program_id == prog.program_id)
                .group_by(Rule.status)
                .all()
            )
            rule_by_status = {row[0]: row[1] for row in rule_rows}

            visa_categories[cat].append({
                "program_id": prog.program_id,
                "subclass_code": prog.subclass_code,
                "name": prog.name,
                "status": prog.status,
                "sources": source_list,
                "source_count": len(source_list),
                "requirement_counts": req_by_status,
                "rule_counts": rule_by_status,
            })

        return {
            "country_id": country_id,
            "country_name": country.name,
            "visa_categories": visa_categories,
            "summary": {
                "total_programs": len(programs),
                "total_sources": sum(
                    len(p["sources"]) for cat in visa_categories.values() for p in cat
                ),
                "visa_categories": list(visa_categories.keys()),
            },
        }
