"""Dev-only seeder: 3 clearly-labelled [DEMO] requirements for exercising the
admin Review screen with zero live fetch and zero LLM call.

Scenarios seeded (4 rows - a CONFLICT is always two sides):
  * REQ-DEMO-NEW            - an ordinary NEEDS_REVIEW item.
  * REQ-DEMO-CONFLICT-OLD   \\_ share a semantic_key, both status=CONFLICT,
  * REQ-DEMO-CONFLICT-NEW   /  so the review feed's previous_value shows up.
  * REQ-DEMO-LOWCONF        - a NEEDS_REVIEW item with confidence=0.34.

Each one's evidence is linked to a REAL document_version when one already
exists in the database (any prior real sync), so "view source document" and
the document/version context in the review feed are genuine - never a
fabricated document pretending to be real. Only when the database has no
document version at all (a brand-new dev DB that has never synced) does this
create one minimal document/source chain itself, and that chain is as
clearly labelled [DEMO] as the requirements are.

Refuses to run when ENVIRONMENT=production (same convention as
services/auth_service.py's JWT-secret check) - this writes fake data and
must never touch a real deployment.

Usage:
    python scripts/seed_review_demo.py            # insert the demo rows
    python scripts/seed_review_demo.py --remove   # delete exactly those rows
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from db.connection import get_engine, get_session
from db.models import (
    Country, DocumentChunk, DocumentVersion, ExtractionRun, GovernmentDocument,
    GovernmentSource, ImmigrationProgram, Requirement, RequirementEvidence,
)
from logging_config import get_logger

logger = get_logger(__name__)

# Every row this script ever writes is tagged with one of these - so
# --remove deletes EXACTLY them and nothing else, never a wildcard sweep.
DEMO_REQUIREMENT_IDS = [
    "REQ-DEMO-NEW",
    "REQ-DEMO-CONFLICT-OLD",
    "REQ-DEMO-CONFLICT-NEW",
    "REQ-DEMO-LOWCONF",
]
DEMO_RUN_ID = "RUN-DEMO-REVIEW-SEED"
DEMO_SOURCE_ID = "SRC-DEMO-REVIEW-SEED"
DEMO_DOCUMENT_ID = "DOC-DEMO-REVIEW-SEED"
DEMO_PROGRAM_ID = "SKILLED_189"
DEMO_COUNTRY_ID = "AU"
DEMO_CHUNK_HEADING = "[DEMO]"  # tags chunks this script creates, for precise removal


def _refuse_in_production() -> None:
    env = os.getenv("ENVIRONMENT", "development")
    if env == "production":
        raise RuntimeError(
            "seed_review_demo.py writes fake [DEMO] data and refuses to run "
            f"when ENVIRONMENT=production (currently ENVIRONMENT={env!r})."
        )


def _anchor_document_version(session):
    """Prefer a REAL, already-fetched document version. Only when the
    database has none at all does this create one minimal [DEMO]-labelled
    chain to anchor the demo evidence to.

    Returns (document_version, program_id, country_id, created_anchor_chain).
    """
    existing = session.execute(select(DocumentVersion).limit(1)).scalar_one_or_none()
    if existing is not None:
        doc = session.get(GovernmentDocument, existing.document_id)
        return existing, doc.program_id, doc.country_id, False

    session.merge(Country(country_id=DEMO_COUNTRY_ID, name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id=DEMO_PROGRAM_ID, country_id=DEMO_COUNTRY_ID, visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.merge(GovernmentSource(
        source_id=DEMO_SOURCE_ID, country_id=DEMO_COUNTRY_ID, program_id=DEMO_PROGRAM_ID,
        visa_category="SKILLED", authority="Department of Home Affairs",
        url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
        source_type="HTML", priority="SECONDARY", monitoring_method="WEB",
        crawl_frequency="DAILY", status="DISABLED",
        notes="[DEMO] created by scripts/seed_review_demo.py - excluded from sync (DISABLED).",
    ))
    session.merge(GovernmentDocument(
        document_id=DEMO_DOCUMENT_ID, source_id=DEMO_SOURCE_ID, country_id=DEMO_COUNTRY_ID,
        program_id=DEMO_PROGRAM_ID, visa_category="SKILLED", document_type="GENERAL",
        url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
        title="[DEMO] Skilled Independent visa (subclass 189) - seeded for Review testing",
        retrieved_at=datetime.now(timezone.utc), content_hash="demo-review-seed",
        current_version=1, language="en", raw_path="demo:seed_review_demo", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id=DEMO_DOCUMENT_ID, version_number=1, content_hash="demo-review-seed",
        retrieved_at=datetime.now(timezone.utc), raw_path="demo:seed_review_demo",
        change_type="INITIAL",
    ))
    session.flush()
    version = session.execute(
        select(DocumentVersion).filter_by(document_id=DEMO_DOCUMENT_ID, version_number=1)
    ).scalar_one()
    return version, DEMO_PROGRAM_ID, DEMO_COUNTRY_ID, True


def _ensure_run(session, document_version) -> ExtractionRun:
    run = session.get(ExtractionRun, DEMO_RUN_ID)
    if run is not None:
        return run
    run = ExtractionRun(
        run_id=DEMO_RUN_ID, document_id=document_version.document_id,
        document_version=document_version.version_number,
        document_version_id=document_version.version_id,
        model="mock", prompt_version="demo", status="COMPLETED",
    )
    session.add(run)
    session.flush()
    return run


def _make_requirement_with_evidence(
    session, *, requirement_id: str, semantic_key: str, program_id: str, country_id: str,
    category: str, name: str, field: str, operator: str, value, unit: str | None,
    mandatory: bool, status: str, confidence: float, run_id: str,
    document_version, evidence_text: str, order_index: int,
) -> Requirement:
    # RequirementEvidence.chunk_id is a real, non-nullable FK to
    # document_chunks - a DEMO evidence string must be backed by a real chunk
    # row, not an arbitrary id. Create one whose raw_text IS the evidence
    # text, so the evidence is a genuine (if newly-created) verbatim match.
    chunk = DocumentChunk(
        document_id=document_version.document_id, version_id=document_version.version_id,
        chunk_type="PARAGRAPH", heading_path=DEMO_CHUNK_HEADING, order_index=order_index,
        raw_text=evidence_text,
    )
    session.add(chunk)
    session.flush()

    req = Requirement(
        requirement_id=requirement_id, semantic_key=semantic_key, program_id=program_id,
        country_id=country_id, category=category, name=f"[DEMO] {name}",
        description="[DEMO] Seeded by scripts/seed_review_demo.py for Review-screen testing.",
        field=field, operator=operator, value_json=json.dumps(value), unit=unit,
        mandatory=1 if mandatory else 0, confidence=confidence, status=status,
        current_version=1, extraction_run_id=run_id,
        document_version_id=document_version.version_id,
    )
    session.add(req)
    session.flush()

    session.add(RequirementEvidence(
        requirement_id=requirement_id, document_id=document_version.document_id,
        document_version=document_version.version_number,
        document_version_id=document_version.version_id, chunk_id=chunk.chunk_id,
        evidence_text=evidence_text, evidence_type="DIRECT_TEXT",
        extracted_at=datetime.now(timezone.utc),
    ))
    return req


def seed(engine) -> None:
    with get_session(engine) as session:
        existing = session.execute(
            select(Requirement).where(Requirement.requirement_id.in_(DEMO_REQUIREMENT_IDS))
        ).scalars().all()
        if existing:
            print(
                f"{len(existing)} demo requirement(s) already present - run with --remove "
                "first if you want to reseed."
            )
            return

        version, program_id, country_id, created_anchor = _anchor_document_version(session)
        run = _ensure_run(session, version)

        _make_requirement_with_evidence(
            session, requirement_id="REQ-DEMO-NEW", semantic_key="demo-new-age",
            program_id=program_id, country_id=country_id, category="AGE",
            name="Age requirement (new)", field="applicant.age", operator="LT",
            value=45, unit="years", mandatory=True, status="NEEDS_REVIEW", confidence=0.88,
            run_id=run.run_id, document_version=version,
            evidence_text="Applicants must be under 45 years of age at the time of invitation.",
            order_index=9001,
        )
        _make_requirement_with_evidence(
            session, requirement_id="REQ-DEMO-CONFLICT-OLD", semantic_key="demo-conflict-english",
            program_id=program_id, country_id=country_id, category="ENGLISH_LANGUAGE",
            name="English language requirement (previous extraction)",
            field="applicant.english_level", operator="GTE", value="COMPETENT", unit=None,
            mandatory=True, status="CONFLICT", confidence=0.81, run_id=run.run_id,
            document_version=version,
            evidence_text="Applicants must demonstrate at least Competent English.",
            order_index=9002,
        )
        _make_requirement_with_evidence(
            session, requirement_id="REQ-DEMO-CONFLICT-NEW", semantic_key="demo-conflict-english",
            program_id=program_id, country_id=country_id, category="ENGLISH_LANGUAGE",
            name="English language requirement (latest extraction)",
            field="applicant.english_level", operator="GTE", value="PROFICIENT", unit=None,
            mandatory=True, status="CONFLICT", confidence=0.81, run_id=run.run_id,
            document_version=version,
            evidence_text="Applicants must demonstrate at least Proficient English.",
            order_index=9003,
        )
        _make_requirement_with_evidence(
            session, requirement_id="REQ-DEMO-LOWCONF", semantic_key="demo-lowconf-points",
            program_id=program_id, country_id=country_id, category="POINTS",
            name="Points requirement (low-confidence extraction)",
            field="applicant.points", operator="GTE", value=65, unit="points",
            mandatory=True, status="NEEDS_REVIEW", confidence=0.34, run_id=run.run_id,
            document_version=version,
            evidence_text=(
                "Applicants should generally have a competitive points score, though the "
                "exact threshold is not clearly stated on this page."
            ),
            order_index=9004,
        )
        session.commit()

    logger.info("Seeded demo review-queue requirements", extra={"ids": DEMO_REQUIREMENT_IDS})
    print(f"Seeded {len(DEMO_REQUIREMENT_IDS)} [DEMO] requirements: {', '.join(DEMO_REQUIREMENT_IDS)}")
    if created_anchor:
        print(f"(No existing document version found - created a [DEMO]-labelled one: {DEMO_DOCUMENT_ID})")


def remove(engine) -> None:
    with get_session(engine) as session:
        reqs = session.execute(
            select(Requirement).where(Requirement.requirement_id.in_(DEMO_REQUIREMENT_IDS))
        ).scalars().all()
        removed_ids = []
        for req in reqs:
            evidence_rows = session.execute(
                select(RequirementEvidence).where(
                    RequirementEvidence.requirement_id == req.requirement_id
                )
            ).scalars().all()
            for e in evidence_rows:
                session.delete(e)
            session.delete(req)
            removed_ids.append(req.requirement_id)

        # The [DEMO]-tagged chunks this script created (see
        # _make_requirement_with_evidence) - precise by tag, not a guess.
        demo_chunks = session.execute(
            select(DocumentChunk).where(DocumentChunk.heading_path == DEMO_CHUNK_HEADING)
        ).scalars().all()
        for chunk in demo_chunks:
            session.delete(chunk)

        # This session factory runs with autoflush=False (db/connection.py),
        # so the requirement deletes above are only pending, not yet visible
        # to a plain SELECT - without this flush, the "still referenced?"
        # check below would always see the (about-to-be-deleted) rows and
        # wrongly conclude the fallback anchor chain is still in use.
        session.flush()

        # The fallback anchor chain, only if THIS script created it (fixed,
        # known ids) and nothing else still references it.
        run = session.get(ExtractionRun, DEMO_RUN_ID)
        still_referenced = session.execute(
            select(Requirement).where(Requirement.extraction_run_id == DEMO_RUN_ID)
        ).scalars().all()
        if run is not None and not still_referenced:
            session.delete(run)
            for v in session.execute(
                select(DocumentVersion).filter_by(document_id=DEMO_DOCUMENT_ID)
            ).scalars().all():
                session.delete(v)
            doc = session.get(GovernmentDocument, DEMO_DOCUMENT_ID)
            if doc is not None:
                session.delete(doc)
            source = session.get(GovernmentSource, DEMO_SOURCE_ID)
            if source is not None:
                session.delete(source)

        session.commit()

    if removed_ids:
        print(f"Removed {len(removed_ids)} [DEMO] requirements: {', '.join(removed_ids)}")
    else:
        print("No [DEMO] requirements found - nothing to remove.")


def main() -> None:
    _refuse_in_production()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remove", action="store_true",
        help="Delete exactly the demo rows this script inserts, instead of inserting them.",
    )
    args = parser.parse_args()

    engine = get_engine()
    if args.remove:
        remove(engine)
    else:
        seed(engine)


if __name__ == "__main__":
    main()
